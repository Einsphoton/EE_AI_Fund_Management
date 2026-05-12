"""Vision agent: 把"持仓页截图"解析为结构化持仓清单。

设计要点：
- 走 OpenAI Chat Completions 兼容协议（image_url 多模态消息）
- 不限定平台，prompt 里给一份"通用 schema + 多平台示例 + 关键字典"
- 容错：模型可能输出 ```json``` 代码围栏、说明文字，需要剥离
- 失败时返回 `{"items": [], "error": "..."}`，不抛异常
- 流式输出（stream=True）+ 图像降采样 + 精简 prompt：让端到端体感与官网 ChatBot 接近

输出 schema（每张图一个对象）：
{
  "platform": "微信理财通" | "支付宝财富" | "招行" | "富途" | "..." | "未知",
  "screenshot_date": "YYYY-MM-DD" 或 null,
  "items": [
    {
      "name": "兴全合宜",
      "code": "163406" 或 null,
      "asset_type": "fund" | "stock" | "etf" | "money_fund" | "wealth" | "cash" | "bond",
      "shares": 1234.5678 或 null,
      "amount": 12345.67 或 null,
      "avg_cost": 1.234 或 null,
      "current_price": 1.345 或 null,
      "market_value": 12345.67 或 null,
      "profit": 123.45 或 null,
      "profit_pct": 1.23 或 null,
      "yield_7d": 1.85 或 null,
      "expected_apr": 3.5 或 null,
      "maturity_date": "YYYY-MM-DD" 或 null
    }
  ]
}
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import Any

from openai import OpenAI

from .. import models
from ..logging_config import log_ai_event, safe_ai_config
from ..services import settings_service



VISION_SYSTEM_PROMPT = """你是一个个人理财截图识别助手。用户会上传"持仓页"截图（理财通/支付宝/银行 App/券商 App 等）。

任务：识别图中所有持仓项，输出**纯 JSON**（必须可被 JSON.parse 解析，不要任何解释、不要 ```json``` 围栏）。

Schema：
{
  "platform": "<识别到的平台名，未识别填'未知'>",
  "screenshot_date": "YYYY-MM-DD" 或 null,
  "items": [{
    "name": "<完整产品名>",
    "code": "<6 位基金代码 / 股票代码，没显示填 null>",
    "asset_type": "fund" | "stock" | "etf" | "money_fund" | "wealth" | "cash" | "bond",
    "shares": <份额/股数，货基/理财/现金可填 null>,
    "amount": <持有金额（元），货基/理财/现金必填>,
    "avg_cost": <平均成本/持仓单价>,
    "current_price": <最新价/净值>,
    "market_value": <当前市值（元）>,
    "profit": <累计收益（元），亏损填负数>,
    "profit_pct": <收益率（%）数字>,
    "yield_7d": <货基7日年化(%)>,
    "expected_apr": <理财预期年化(%)>,
    "maturity_date": "YYYY-MM-DD" 或 null
  }]
}

类型判定：
- 余额宝/朝朝宝/朝朝盈/零钱通/添益宝/余利宝 → money_fund
- 活期/活期+/活钱/现金宝 → cash
- XX天/XX个月/定期/净值型/结构性/大额存单 → wealth；国债且有代码 → bond
- ETF/LOF/场内基金 → etf；普通公募基金 → fund；个股 → stock

约束：
1. 数值字段必须是裸数字，不带"元"/"%"/"约"等；看不清就填 null（不要瞎猜）。
2. 一张图 5-15 项要全部列出。
3. 不是持仓页（首页/广告/聊天）→ {"platform":"未知","items":[]}。
4. **绝对不要列举 UI 元素**：不要把"买入按钮""取出/转换""更多""详情""持有"这类按钮、标签、链接当成持仓项；
   只列产品本身（基金/股票/理财名）。识别完所有产品就立刻输出 `]}` 收尾，不要继续编号或重复任何短语。
5. 输出**必须是单一 JSON Object**，items 数组 ≤ 30 条，超过 30 条说明你在复读循环，立刻停止并只输出已识别的产品。
"""


def _get_vision_config(db) -> dict | None:
    """读 vision 配置；支持 use_ai 模式。

    use_ai=True 时：**只复用** ai 配置的 base_url / api_key / model（端点和模型）；
    所有性能参数（temperature / max_tokens / timeout / concurrency / json_mode）
    都用 vision 自己的设置。理由：
      - OCR 任务和文本分析任务对性能的要求差异很大（OCR 输出长得多、需要更低 temperature 等）
      - vision 默认值已经针对持仓截图 OCR 调优过，不应被 ai 的批量分析参数污染

    返回 None 表示未配置（前端会引导）。
    """
    cfg = settings_service.get(db, "vision") or {}
    if cfg.get("use_ai"):
        ai_cfg = settings_service.get(db, "ai") or {}
        if not ai_cfg.get("base_url") or not ai_cfg.get("model"):
            return None
        merged = {
            # 端点 / 鉴权 / 模型：从 ai 拷过来
            "base_url": ai_cfg.get("base_url"),
            "api_key": ai_cfg.get("api_key") or "",
            "model": ai_cfg.get("model"),
            # 性能参数：严格只用 vision 自己的（不 fallback 到 ai），
            # 缺失则用我们为 OCR 调优过的默认值
            "temperature": cfg.get("temperature", 0.1),
            "max_tokens": cfg.get("max_tokens", 8192),
            "timeout": cfg.get("timeout", 300),
            # concurrency 默认 2（与 settings_service.DEFAULTS["vision"] 一致）。
            # 历史 bug：原本默认 1 → 一旦数据库里 vision 配置缺 concurrency 字段，
            # OCR 就被强制串行，多图导入慢得离谱。
            "concurrency": cfg.get("concurrency", 2),
            "json_mode": cfg.get("json_mode", True),
            "stream": cfg.get("stream", False),
            "force_stream": cfg.get("force_stream", False),
            "wall_timeout": cfg.get("wall_timeout", 90),
            "content_hardcap": cfg.get("content_hardcap", 20000),
            "auto_fill_code": cfg.get("auto_fill_code", True),
            "_use_ai": True,
        }
        return merged
    if not cfg.get("base_url") or not cfg.get("api_key") or not cfg.get("model"):
        return None
    return cfg


def _build_client(db, cfg: dict) -> OpenAI:
    """构造 OpenAI 客户端，自动注入 ai 配置里的 Cloudflare Access header。

    视觉模型走自建 Cloudflare Tunnel 时，需要把 CF-Access-Client-Id/Secret
    带上才能通过 Zero Trust 拦截；这套 token 与 ai 配置共用。

    timeout：多模态 + JSON Mode + 长输出经常需要 3-5 分钟，默认给到 600s。
    max_retries=0：超时不要静默重试，否则用户要等 2× timeout 才看到报错，体验极差。

    自定义 http_client：
      - 禁用 keep-alive 连接复用（max_keepalive_connections=0）。
        反代/Cloudflare Tunnel 上空闲一段时间会单方面关闭 TCP，但本端连接池仍然
        保留它的 fd；下次发请求会拿到这个"半死的连接"，httpx 抛 APIConnectionError
        / RemoteProtocolError。每次新建连接只多 ~50ms TCP+TLS，但稳定性提升明显。
      - 显式拆分 connect/read/write 超时，避免 connect 卡住把整体 timeout 用光。
    """
    from .hermes import _build_cf_headers  # 复用 hermes 的 CF header 构造逻辑
    import httpx

    ai_cfg = settings_service.get(db, "ai") or {}
    headers = _build_cf_headers(cfg["base_url"], ai_cfg)
    default_headers = {"User-Agent": headers.get("User-Agent", "")}
    if "CF-Access-Client-Id" in headers:
        default_headers["CF-Access-Client-Id"] = headers["CF-Access-Client-Id"]
        default_headers["CF-Access-Client-Secret"] = headers["CF-Access-Client-Secret"]

    timeout_total = float(cfg.get("timeout", 300))
    http_client = httpx.Client(
        # 禁用 keep-alive 复用（max_keepalive=0），但仍允许并发连接
        limits=httpx.Limits(max_keepalive_connections=0, max_connections=10),
        timeout=httpx.Timeout(
            connect=15.0,         # TCP+TLS 握手最多 15s
            read=timeout_total,   # 流式期间两个 chunk 之间的最大间隔
            write=60.0,           # 上传图片 base64 写入最多 60s
            pool=15.0,            # 从连接池拿连接的等待时间
        ),
    )
    return OpenAI(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"] or "EMPTY",
        timeout=timeout_total,
        max_retries=0,
        default_headers=default_headers,
        http_client=http_client,
    )


def _preprocess_image(image_bytes: bytes, mime: str) -> tuple[bytes, str, str]:
    """OCR 前的图像预处理：降采样 + JPEG 压缩，降低多模态请求延迟。

    经验值：
    - 手机持仓页截图通常 1170×2532（iPhone）或 1080×2400（Android），原图 300KB-2MB
    - 长边压到 1280px 后字仍清晰（持仓页字号本来就大），模型识别准确率几乎不变
      （之前用 1600px 偏保守；1280 是 GPT-4V/Qwen-VL/Kimi 都推荐的"刚好够"尺寸，
       图像 token 数显著少于 1600，prompt token 处理与首字延迟都更快）
    - JPEG quality=80 在文字截图上已足够；体积通常缩到 1/3~1/5
    - 压缩前后总用时收益往往 > 30 秒（上传 + 模型读 token + 视觉编码）

    Pillow 缺失时直接返回原图，不影响功能。
    返回：(processed_bytes, mime_after, info_msg)
    """
    raw_kb = len(image_bytes) // 1024
    # 小图直接放过（< 200KB），压缩反而引入解码开销
    if raw_kb < 200:
        return image_bytes, mime, f"原图 {raw_kb}KB，无需压缩"

    try:
        from PIL import Image  # type: ignore
        import io
    except ImportError:
        return image_bytes, mime, f"原图 {raw_kb}KB（未装 Pillow，跳过压缩）"

    try:
        img = Image.open(io.BytesIO(image_bytes))
        # 把 RGBA / P 模式统一转 RGB（JPEG 不支持透明）
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # 长边降到 1280（多模态模型推荐的"刚好够"尺寸）
        max_side = 1280
        w, h = img.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            resized = f"{w}×{h}→{img.size[0]}×{img.size[1]}"
        else:
            resized = f"{w}×{h}（不缩放）"

        buf = io.BytesIO()
        # quality=78 + progressive：在文字截图上视觉无差，体积再小 ~10%
        img.save(buf, format="JPEG", quality=78, optimize=True, progressive=True)
        out = buf.getvalue()
        out_kb = len(out) // 1024
        return out, "image/jpeg", f"{raw_kb}KB → {out_kb}KB · {resized}"
    except Exception as e:
        # 压缩出错时不影响主流程，原图传过去
        return image_bytes, mime, f"压缩失败（{type(e).__name__}），用原图"


def _strip_json_noise(text: str) -> str:
    """剥离 ```json``` 围栏和首尾说明文字，截取最外层 JSON 对象。"""
    if not text:
        return ""
    # 去掉 markdown 代码围栏
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    # 找第一个 { 和最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def _try_repair_truncated_json(text: str) -> str | None:
    """尽力修复被 max_tokens 截断的 JSON。

    Kimi/Qwen-VL 在 max_tokens 截断时会返回半截 JSON：
    {... "items": [{"name":"A",...},{"name":"B"   ← 这里没了
    我们把代码块围栏剥掉、找到最外层 { 后，按括号层级补 ] / }，并把最后一项不完整的对象丢弃。

    仅作为最后兜底，成功返回修补后的字符串；无法修复返回 None。
    """
    if not text:
        return None
    # 先剥围栏
    s = _strip_json_noise(text)
    if not s.startswith("{"):
        s = s[s.find("{"):] if "{" in s else s
    if not s:
        return None

    # 逐字扫描，记录括号层级，遇到字符串内部时跳过
    in_str = False
    escape = False
    stack: list[str] = []
    last_safe_pos = -1  # 最近一个"完整 item 后的逗号"位置，截断到这里再补闭合
    for i, ch in enumerate(s):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
        elif ch == "," and len(stack) <= 2:
            # 在最外层 object 的某个 array 里，遇到逗号 = 一个完整 item 结束
            last_safe_pos = i

    if not stack:
        # 已经平衡，但 json.loads 失败 → 可能是其他语法错误，交给上层
        return s

    # 截到最近一个安全点（如果在数组里），然后补闭合
    if last_safe_pos > 0:
        s = s[:last_safe_pos]  # 丢掉最后那个不完整的 item

    # 按 stack 补闭合
    closer_map = {"{": "}", "[": "]"}
    while stack:
        s += closer_map.get(stack.pop(), "")

    return s


def _safe_parse(text: str) -> tuple[dict | None, str]:
    """尽力解析 JSON。

    返回 (parsed_or_None, mode)：
    - mode = "raw" / "stripped" / "repaired" / "failed"
    """
    if not text:
        return None, "failed"
    try:
        return json.loads(text), "raw"
    except Exception:
        pass
    cleaned = _strip_json_noise(text)
    try:
        return json.loads(cleaned), "stripped"
    except Exception:
        pass
    # 最后兜底：修复被截断的 JSON
    repaired = _try_repair_truncated_json(text)
    if repaired:
        try:
            return json.loads(repaired), "repaired"
        except Exception:
            pass
    return None, "failed"


def _img_to_data_url(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


async def parse_image(
    db,
    image_bytes: bytes,
    *,
    mime: str = "image/jpeg",
    platform_hint: str = "",
    on_log=None,
    cancel_event=None,
) -> dict[str, Any]:
    """解析单张截图。返回标准化字典；失败时 items=[]。

    Parameters
    ----------
    on_log : Optional[Callable[[str], Awaitable[None]]]
        异步日志回调；用于 OCR 任务把"思考过程"实时推给前端。
    cancel_event : Optional[asyncio.Event]
        外部取消信号。流式循环每次取 chunk 都会检查；一旦 set 立即停止接收并返回
        `{"cancelled": True, "items": []}`。
    """
    async def _log(msg: str):
        if on_log:
            try:
                await on_log(msg)
            except Exception:
                pass

    def _is_cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    if _is_cancelled():
        await _log("已取消（在调用前）")
        return {"platform": "已取消", "items": [], "cancelled": True}

    cfg = _get_vision_config(db)
    if not cfg:
        # 给出针对性提示
        v = settings_service.get(db, "vision") or {}
        if v.get("use_ai"):
            msg = "已开启『复用 AI 大模型』，但 AI 大模型未配置 base_url / model。请到『设置 → AI 大模型』填好。"
        else:
            msg = "请先在『设置 → 视觉模型』填入 base_url / api_key / model；或勾选『复用 AI 大模型』。"
        await _log("视觉模型未配置，跳过本张")
        return {"platform": "未配置", "items": [], "error": msg}

    client = _build_client(db, cfg)

    # ─── 图像预处理：降采样 + 重压 ───
    processed_bytes, processed_mime, preprocess_info = _preprocess_image(image_bytes, mime)
    await _log(f"图像预处理：{preprocess_info}")
    data_url = _img_to_data_url(processed_bytes, processed_mime)

    user_text = (
        "请识别这张截图里的所有持仓项，按 schema 输出 JSON。"
        " 必须只输出一个合法的 JSON Object，不要任何额外说明。"
    )
    if platform_hint:
        user_text += f" 提示：这张截图来自「{platform_hint}」。"

    messages = [
        {"role": "system", "content": VISION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    # max_tokens：持仓页可能 5-15 项，每项约 250 tokens，OCR 任务输出本来就长。
    # 这里做强 normalize：
    #   - 0 / None / 负数 / 解析失败 → 8192（OCR 默认值）
    #   - 1..1023 太小（一定截断）→ 强制抬高到 8192 并打日志
    # 注意：vision 场景下不能用"0=不限"语义，因为大多数多模态服务端会把 0 解释为
    # "立即停止输出"，导致只蹦几个字就 finish_reason=length。
    OCR_FLOOR = 8192
    raw_mt = cfg.get("max_tokens")
    try:
        if raw_mt is None or raw_mt == "":
            max_tokens = OCR_FLOOR
        else:
            max_tokens = int(raw_mt)
    except (TypeError, ValueError):
        max_tokens = OCR_FLOOR
    if max_tokens <= 0 or max_tokens < 1024:
        if max_tokens != OCR_FLOOR:
            log_ai_event(
                "vision",
                "vision_max_tokens_auto_raised",
                old_max_tokens=max_tokens,
                new_max_tokens=OCR_FLOOR,
            )
        max_tokens = OCR_FLOOR

    temperature = cfg.get("temperature", 0.1)
    model_name = cfg["model"]

    # 是否启用 JSON Mode（response_format=json_object）
    # Kimi/Moonshot/DeepSeek/Qwen/智谱 GLM 都支持；保险起见做一次降级重试
    use_json_mode = bool(cfg.get("json_mode", True))

    # ============================================================
    # 流式 vs 非流式：默认走「非流式」，且对多模态 OCR 强制非流式
    # ------------------------------------------------------------
    # 为什么**强制**非流式？
    #   多模态模型在 OCR 场景下有两类高频崩溃：
    #     a) 长 UI 文字复读（"景顺长城景顺长城景顺长城..." 刷满 max_tokens）
    #     b) reasoning 段内陷入思考循环，content 段完全写不出
    #   流式路径下这两类崩溃会产生几千个 chunk 持续涌向前端/SSE/终端，靠"复读检测器"
    #   救场不够及时：即便检测到，中间也已经浪费几十秒 + 消耗 RPM 配额。
    #
    #   非流式路径（一次性 await）天然有服务端层面的硬边界——一次请求到 max_tokens
    #   就自然停；即便模型内部复读也只会让这一次 completion 填满 token 然后返回。
    #   **上层再叠一个"整图总耗时硬上限"**（见 MAX_WALL_SEC）即可根治。
    #
    # 只有用户明确配置 `vision.stream=True` 且 `vision.force_stream=True` 两个开关
    # 同时打开时才走流式路径——这是给"我就是要看实时输出"用户的逃生口，默认严禁。
    # ============================================================
    # 新逻辑：stream=True 只是"想看实时输出"的意愿；要真启用流式必须再加 force_stream=True
    _user_wants_stream = bool(cfg.get("stream", False))
    _force_stream = bool(cfg.get("force_stream", False))
    use_stream = _user_wants_stream and _force_stream
    if _user_wants_stream and not _force_stream:
        await _log(
            "提示：检测到 vision.stream=True 但 force_stream 未开启；"
            "为规避模型复读循环（如『景顺长城景顺长城…』）已自动切换为非流式调用。"
            "如需实时输出请同时设置 vision.force_stream=true。"
        )

    # ── 死循环 / 跑飞总防线：不管流式非流式，单图最多跑这么久就强制结束 ──
    # 一张持仓页截图 OCR 正常 15-30s；90s 已经是「极保守上限」。
    # 任何死循环 / 上游挂起 / SDK 卡住 —— 最多让用户等 MAX_WALL_SEC。
    MAX_WALL_SEC = float(cfg.get("wall_timeout", 90))
    # 内容字符硬上限：正常 20 项持仓 ≈ 5000-8000 字符；给 2.5× 余量。
    # 命中后强制截断，避免流式路径下模型"继续往后吐"。
    MAX_CONTENT_CHARS = int(cfg.get("content_hardcap", 20000))
    # ============================================================

    log_ai_event(
        "vision",
        "vision_parse_start",
        config=safe_ai_config(cfg),
        model=model_name,
        image_bytes=len(image_bytes),
        processed_bytes=len(processed_bytes),
        data_url_kb=len(data_url) // 1024,
        max_tokens=max_tokens,
        json_mode=use_json_mode,
        stream=use_stream,
        wall_timeout=MAX_WALL_SEC,
    )
    await _log(
        f"已构造 prompt（图片转 base64 共 {len(data_url)//1024} KB）→ 调用 "
        f"{model_name}（temperature={temperature}, max_tokens={max_tokens}"
        f"{'（已从配置中的 ' + str(raw_mt) + ' 自动抬高，OCR 必须 >= 1024）' if raw_mt is not None and (not isinstance(raw_mt, int) or raw_mt < 1024) else ''}"
        f"{', json_mode=on' if use_json_mode else ''}, stream={'on' if use_stream else 'off'}）"
    )


    async def _call_model_oneshot(with_json_mode: bool, with_no_think: bool = False):
        """非流式调用：一次性 await 拿到完整响应。

        返回 (text, finish_reason, usage_dict_or_None, cancelled_bool, loop_hit, loop_pat)
        —— 与 _call_model_streaming 同 shape，方便上层无差别处理。

        优势：
          - 没有跨线程 IPC（asyncio.to_thread 一次性返回完整 chunk）
          - 没有循环检测开销（拿到完整文本后一次过 _safe_parse 即可）
          - 取消粒度只到"整张图"级别（无法在中途打断 SDK 的 .create）
            但实测 OCR 单张图 12-25s，取消窗口可接受

        with_no_think 默认 **False**（与基线版本对齐）：
          实测发现 NVIDIA NIM Kimi K2.6 收到 `reasoning_effort=minimal` /
          `enable_thinking=False` / `thinking={type:disabled}` 等非标参数时，
          首字延迟会从几秒涨到几分钟（疑似 NIM 内部把请求踢到"reasoning
          专用队列"或额外校验）。
          基线版本根本没传这些参数，模型走默认路径反而最快。
          只在配置 `vision.force_no_think=true` 时才透传——这是给某些
          强制 thinking 的 hybrid 模型留的逃生口。
        """
        kwargs: dict = dict(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if with_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if with_no_think:
            kwargs["extra_body"] = {
                "enable_thinking": False,
                "thinking": {"type": "disabled"},
            }
            kwargs["reasoning_effort"] = "minimal"

        # 取消检测：把 SDK 同步调用放进 to_thread；同时起一个 cancel watcher
        # 命中取消时立即 raise CancelledError 让上层走取消分支
        request_started_at = asyncio.get_event_loop().time()

        async def _do_call():
            return await asyncio.to_thread(client.chat.completions.create, **kwargs)

        call_task = asyncio.create_task(_do_call())

        async def _watch_cancel():
            while not _is_cancelled():
                if call_task.done():
                    return None
                await asyncio.sleep(0.3)
            return "cancelled"

        watch_task = asyncio.create_task(_watch_cancel())
        done, pending = await asyncio.wait(
            {call_task, watch_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if call_task in done:
            watch_task.cancel()
            resp = call_task.result()
        else:
            # 取消信号先到：不强行 cancel call_task（SDK 同步线程无法被打断），
            # 但立即返回 cancelled 让 _one 推进；call_task 在线程里跑完即被丢弃
            return ("", None, None, True, False, "")

        elapsed = asyncio.get_event_loop().time() - request_started_at
        choice = resp.choices[0] if resp.choices else None
        msg = choice.message if choice else None
        text = (getattr(msg, "content", "") or "").strip() if msg else ""
        finish_reason = getattr(choice, "finish_reason", None) if choice else None
        usage = getattr(resp, "usage", None)
        usage_dict: dict | None = None
        if usage:
            try:
                usage_dict = {
                    "prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                    "total_tokens": getattr(usage, "total_tokens", None),
                }
            except Exception:
                pass
        await _log(
            f"⚡ 模型一次性返回（{elapsed:.1f}s, {len(text)} 字符）"
        )
        return (text, finish_reason, usage_dict, False, False, "")

    async def _call_model_streaming(with_json_mode: bool, with_usage: bool = True,
                                     with_no_think: bool = False):
        """流式调用：把 token 流式推给 on_log，整体累积成完整 text 返回。

        返回 (text, finish_reason, usage_dict_or_None, cancelled_bool, loop_hit, loop_pat)。
        with_usage=False：不带 stream_options（兼容不识别该参数的代理）。

        with_no_think 默认 **False**（基线版本根本不传这些参数，速度更快）：
            透传 enable_thinking=False / reasoning_effort=minimal 等强制关思考的参数。
            实测在 NVIDIA NIM 上反而会让 TTFB 从几秒涨到几分钟——疑似服务端
            把带这些参数的请求路由到不同的处理队列。
            只在配置 `vision.force_no_think=true` 时才透传。
        """
        kwargs: dict = dict(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        if with_usage:
            # Kimi/Moonshot：流式下要显式开启 usage 才能在 done chunk 拿到 token 计数
            kwargs["stream_options"] = {"include_usage": True}
        if with_json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        # ── 强制关 thinking ──
        # OCR 是"看图填表"的 lookup 任务，让 reasoning/thinking 模型开思考反而：
        # 1) 容易陷入复读循环（已多次发生：「华泰保兴安悦债券A → 华泰保兴安悦…」）
        # 2) 把 max_tokens 烧在 reasoning 段，content 区写不完整
        # 3) 多花 50-200% 的延迟
        # 三套主流参数同时透传，不认识的会被 SDK 放进 extra_body 或被服务端忽略：
        #   - DeepSeek V4 / Qwen3.5 / GLM 系 / 豆包 / MiniMax → enable_thinking=False
        #   - Anthropic Claude / GLM-5 部分 → thinking={type:"disabled"}
        #   - OpenAI o-series / GPT-5 / Kimi K2 / Grok 4 → reasoning_effort="minimal"
        if with_no_think:
            kwargs["extra_body"] = {
                "enable_thinking": False,
                "thinking": {"type": "disabled"},
            }
            kwargs["reasoning_effort"] = "minimal"

        # client.chat.completions.create(stream=True) 返回的是同步 Stream 迭代器
        # 用 to_thread 把"打开流 + 逐 chunk 迭代"都丢到线程里跑（OpenAI SDK 是同步的）
        # 主事件循环里通过 asyncio.Queue 消费 chunk → 这样既能 stream，又能 await on_log
        loop = asyncio.get_event_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=1024)
        SENTINEL = object()

        # 用 list 包一层方便 _consume 写入、取消时主线程 close
        stream_holder: list = [None]
        # 让 _consume 知道主循环已经放弃接收（取消时设置）
        consumer_done = [False]

        def _consume():
            """在线程里跑：打开流 + 逐 chunk 投到队列。

            两条退出路径：
              1) 流自然结束 → 推 SENTINEL
              2) 主循环检测到取消后 set consumer_done + close 流 → 静默退出
            """
            try:
                stream_resp = client.chat.completions.create(**kwargs)
                stream_holder[0] = stream_resp
                for chunk in stream_resp:
                    # 取消检测放在线程里：响应最快
                    if _is_cancelled() or consumer_done[0]:
                        break
                    # put 用阻塞调用 + timeout，避免主循环已死时永远卡住
                    fut = asyncio.run_coroutine_threadsafe(q.put(chunk), loop)
                    try:
                        fut.result(timeout=2.0)
                    except Exception:
                        # 主循环已停（loop 关了 or 队列满到永远） → 直接结束
                        break
                # 正常推 SENTINEL（用 nowait 跨线程更稳）
                if not consumer_done[0]:
                    try:
                        asyncio.run_coroutine_threadsafe(q.put(SENTINEL), loop).result(timeout=1.0)
                    except Exception:
                        pass
            except Exception as exc:
                if consumer_done[0] or _is_cancelled():
                    return  # 静默
                try:
                    asyncio.run_coroutine_threadsafe(q.put(exc), loop).result(timeout=1.0)
                except Exception:
                    pass
            finally:
                # 不管怎么退出，都尝试关流，释放 HTTP 连接
                try:
                    if stream_holder[0] is not None:
                        stream_holder[0].close()
                except Exception:
                    pass

        # 后台线程跑同步流
        thread_task = asyncio.create_task(asyncio.to_thread(_consume))

        text_parts: list[str] = []
        # reasoning_content 也单独累积，参与"reasoning 死循环"检测
        # （reasoning 模型在思考段死循环时，content 可能完全空，但 reasoning 段疯狂吐 token）
        reasoning_parts: list[str] = []
        finish_reason = None
        usage_dict: dict | None = None
        last_flush_at = 0.0
        flush_buffer = ""
        first_token_at = None

        # ============================================================
        # 性能关键：流式累积量用累计标量 / 滑动 deque 维护，避免 O(N²)
        # ------------------------------------------------------------
        # 历史 bug：以前每个 chunk 都 `sum(len(p) for p in text_parts)` +
        # `sum(... for c in p if c.isspace() ...)` + `"".join(parts)[-800:]`，
        # 导致 N 个 chunk 累计成本 O(N²)：8000 字符 × 1000 chunk → 几十秒卡死。
        # 现在改成增量更新：每来一段 piece，只扫这一段的 len/isspace + push 到 deque。
        # ============================================================
        from collections import deque
        # content
        content_total_len = 0       # 总字符数（累计）
        content_meaningful = 0      # 非空白字符数（累计）
        # reasoning
        reasoning_total_len = 0
        # 滑动 tail：最近 LOOP_TAIL_WIN 字符；用 deque 维护更便宜
        # （也可以用 string + 切片，但每次 N 字符 push 会复制；deque pop_left 是 O(1)）
        LOOP_TAIL_WIN = 800
        content_tail: deque = deque(maxlen=LOOP_TAIL_WIN)
        reasoning_tail: deque = deque(maxlen=LOOP_TAIL_WIN)

        last_meaningful_growth_at = None  # 上次 content "有意义增长"（非空白）的时间
        last_meaningful_count = 0         # 上次记录到的非空白字符数（标记是否真增长）
        request_started_at = asyncio.get_event_loop().time()
        FLUSH_INTERVAL = 0.4   # 每 400ms 推一次
        FLUSH_CHARS = 80        # 或累计 80 字符就推
        # 「无意义输出超时」：模型一直吐 token 但全是空白/重复字符（content 长度涨、
        # 但非空白字符数不涨），属于死循环。20 秒没有任何"有意义字符"增长 → 关流。
        MEANINGFUL_STALL_SEC = 20.0
        # 「reasoning 段超长」：纯 reasoning 累积 4000 字符还没出 content，大概率死循环了
        REASONING_OVERRUN_LEN = 4000
        # reasoning 段下次检查阈值（用 list 包成可变量，闭包内可写）
        _r_check_at = [400]

        async def _flush(force: bool = False):
            nonlocal flush_buffer, last_flush_at
            if not flush_buffer:
                return
            now = asyncio.get_event_loop().time()
            if not force and (now - last_flush_at) < FLUSH_INTERVAL and len(flush_buffer) < FLUSH_CHARS:
                return
            # 单行预览（去换行 / 截断），让前端滚得平滑
            preview = flush_buffer.replace("\n", " ").replace("\r", "")
            if len(preview) > 200:
                preview = preview[:200]
            # 纯空白 buffer 不推到前端（避免空格死循环时刷屏 170 条 📡 日志），
            # 但仍清空 buffer 让累积量不会无限增长
            if preview.strip():
                await _log(f"📡 {preview}")
            flush_buffer = ""
            last_flush_at = now

        cancelled_during_stream = False
        # 复读循环检测：多模态模型遇到 UI 元素重复 / 长清单时容易陷入死循环
        # （"按钮 / 取出 / 按钮 / 取出..." 或 "景顺长城景顺长城景顺长城..." 刷屏），
        # 把 max_tokens 烧光也不收尾，浪费 RPM 配额。我们在流式累积过程中实时扫描，
        # 命中即关流。
        #
        # 检测方法（**三重指标**，任一命中即判循环）：
        #   A. 字符种类指标（主力，对中文复读极敏感）：最近 N 字符里不同字符种类 < 阈值。
        #      "景顺长城" 重复 100 次 = 400 字符，只有 4 种字符，分数 < 10，必中。
        #      正常 JSON 同样长度通常 ≥ 60 种字符。
        #   B. 高频 n-gram 指标（兜底）：找 4/6/8/12 字 n-gram 出现 ≥ 5 次。
        #      加入 4-gram："景顺长城" 直接命中。
        #   C. 尾部重复率指标（兜底2）：最后 200 字符里相同短串重复率 > 阈值。
        loop_detected = False
        loop_pattern = ""             # 用于错误提示
        # 关键修正：**尽早开始检查**（200 字符即可触发），且每 100 字符就扫一次
        # 以前 400/200 的阈值太晚——某些模型第一个 chunk 就能推 300 字符进来，
        # 中间已经开始复读，等到 400 时已经累积大量垃圾。
        next_loop_check_at = 200
        LOOP_CHECK_STEP = 100

        # 指标 A 阈值（放松到 200 字符起判）
        LOOP_DIVERSITY_TAIL = 400     # 取多少尾部字符算多样性（减小窗口，更敏感）
        LOOP_DIVERSITY_MIN_TAIL = 200 # 只要尾部 ≥ 200 字符就能评估（之前 300 太保守）
        LOOP_DIVERSITY_MIN_CHARS = 20 # 不同字符种类 < 20 即判循环（紧一点）
        # 指标 B 阈值（加 4-gram / 6-gram，对中文短复读敏感）
        LOOP_NGRAM_LENS = (4, 6, 8, 12)
        LOOP_NGRAM_MIN_HITS = 5
        # 指标 C：最后 200 字符内最高频字符主导率过高 → 复读
        # 实现里具体阈值是"前 4 高频字符合计 ≥ 85%"（见 _detect_loop）
        LOOP_CHAR_DOMINANCE_TAIL = 200

        def _detect_loop(tail: str) -> tuple[bool, str]:
            """三重指标循环检测。返回 (is_loop, debug_info)。
            tail 已经是滑动窗口截好的字符串（≤ LOOP_TAIL_WIN），不再 join 全量。"""
            n = len(tail)
            if n < 100:
                return False, ""

            # === 指标 A：字符种类多样性 ===
            div_tail = tail[-LOOP_DIVERSITY_TAIL:] if n >= LOOP_DIVERSITY_TAIL else tail
            if len(div_tail) >= LOOP_DIVERSITY_MIN_TAIL:
                unique = len(set(div_tail))
                if unique < LOOP_DIVERSITY_MIN_CHARS:
                    sample = div_tail[-60:].replace("\n", " ")
                    return True, f"字符种类仅 {unique}（阈值 {LOOP_DIVERSITY_MIN_CHARS}）样本：{sample}"

            # === 指标 C：单字符主导率（中文 OCR 复读最典型的特征）===
            # "景顺长城景顺长城..." → '景' 占 25% × 每个字，任意一个都会 ≥ 25%
            # 但 4 种字分散下来都是 25%，不会触发 50% 阈值。
            # 所以改为：最高频字符 + 前 N 高频字符的合计占比
            if n >= LOOP_CHAR_DOMINANCE_TAIL:
                dom_tail = tail[-LOOP_CHAR_DOMINANCE_TAIL:]
                counts: dict[str, int] = {}
                for c in dom_tail:
                    if c.isspace():
                        continue
                    counts[c] = counts.get(c, 0) + 1
                if counts:
                    total_non_ws = sum(counts.values())
                    if total_non_ws >= 100:
                        # 取前 4 高频字符合计占比（"景顺长城"重复时前 4 个字符占 100%）
                        top4 = sorted(counts.values(), reverse=True)[:4]
                        top4_ratio = sum(top4) / total_non_ws
                        if top4_ratio >= 0.85:
                            sample = dom_tail[-60:].replace("\n", " ")
                            return True, (
                                f"尾部 {LOOP_CHAR_DOMINANCE_TAIL} 字符中前 4 高频字符"
                                f"占 {top4_ratio * 100:.0f}%（疑似短串复读）样本：{sample}"
                            )

            # === 指标 B：高频 n-gram ===
            for plen in LOOP_NGRAM_LENS:
                if n < plen * LOOP_NGRAM_MIN_HITS:
                    continue
                counts_ng: dict[str, int] = {}
                for i in range(n - plen + 1):
                    g = tail[i:i + plen]
                    # 跳过纯空白/标点的 n-gram
                    stripped = g.strip()
                    if len(stripped) < plen - 2:
                        continue
                    counts_ng[g] = counts_ng.get(g, 0) + 1
                if not counts_ng:
                    continue
                top_gram, top_cnt = max(counts_ng.items(), key=lambda kv: kv[1])
                if top_cnt >= LOOP_NGRAM_MIN_HITS:
                    return True, f"n-gram「{top_gram}」重复 {top_cnt} 次"
            return False, ""

        try:
            while True:
                # 取消检测：每次循环都看一眼
                if _is_cancelled():
                    cancelled_during_stream = True
                    # 通知 _consume 不必再投递；主动关流（线程里也会再 close 一次，幂等）
                    consumer_done[0] = True
                    try:
                        if stream_holder[0] is not None:
                            stream_holder[0].close()
                    except Exception:
                        pass
                    break
                # 用短超时拿 chunk，否则取消信号会被 await q.get() 堵住
                try:
                    item = await asyncio.wait_for(q.get(), timeout=0.3)
                except asyncio.TimeoutError:
                    continue
                if item is SENTINEL:
                    break
                if isinstance(item, BaseException):
                    raise item
                chunk = item
                if first_token_at is None:
                    first_token_at = asyncio.get_event_loop().time()
                    ttfb = first_token_at - request_started_at
                    await _log(f"⚡ 首字到达（TTFB {ttfb:.1f}s），模型开始输出…")
                # 处理 choices[0].delta.{content, reasoning_content}
                try:
                    choices = getattr(chunk, "choices", []) or []
                    if choices:
                        ch0 = choices[0]
                        delta = getattr(ch0, "delta", None)
                        if delta is not None:
                            now_t = asyncio.get_event_loop().time()
                            # ── content 累积（实际产出，参与最终 JSON 解析）──
                            piece = getattr(delta, "content", None) or ""
                            if piece:
                                text_parts.append(piece)
                                flush_buffer += piece
                                await _flush()
                                # 性能关键：增量更新，O(len(piece)) 不是 O(总长)
                                content_total_len += len(piece)
                                # 一次扫 piece 同时算非空白字符数 + 推到滑动窗口
                                meaningful_in_piece = 0
                                for c in piece:
                                    if not c.isspace():
                                        meaningful_in_piece += 1
                                    content_tail.append(c)
                                content_meaningful += meaningful_in_piece
                                if content_meaningful > last_meaningful_count:
                                    last_meaningful_count = content_meaningful
                                    last_meaningful_growth_at = now_t
                                elif first_token_at is not None and last_meaningful_growth_at is None:
                                    # 首字之后从未出现过非空白字符，把基线设到首字时刻
                                    last_meaningful_growth_at = first_token_at

                            # ── reasoning_content 累积 ──
                            r_piece = (
                                getattr(delta, "reasoning_content", None)
                                or getattr(delta, "reasoning", None)
                                or ""
                            )
                            if r_piece:
                                reasoning_parts.append(r_piece)
                                reasoning_total_len += len(r_piece)
                                for c in r_piece:
                                    reasoning_tail.append(c)

                            # ── 字符硬上限（防线 2）：即便循环检测失效，累积超
                            # MAX_CONTENT_CHARS 也必须强制截断。正常 20 项持仓约
                            # 5000-8000 字符，默认 20000 已经给了 2.5× 余量。
                            if content_total_len >= MAX_CONTENT_CHARS:
                                loop_detected = True
                                loop_pattern = (
                                    f"[content_hardcap] content 累积已达 {content_total_len} "
                                    f"字符（硬上限 {MAX_CONTENT_CHARS}），不再等待模型收尾"
                                )
                                await _log(
                                    f"🛑 content 累积已达 {content_total_len} 字符 "
                                    f"(硬上限 {MAX_CONTENT_CHARS})，强制关流避免继续浪费 token"
                                )
                                consumer_done[0] = True
                                try:
                                    if stream_holder[0] is not None:
                                        stream_holder[0].close()
                                except Exception:
                                    pass
                                break

                            # ── 复读循环检测（content + reasoning 任一命中即停）──
                            check_targets: list[tuple[str, str]] = []
                            if content_total_len >= next_loop_check_at:
                                next_loop_check_at = content_total_len + LOOP_CHECK_STEP
                                check_targets.append(("content", "".join(content_tail)))
                            if reasoning_total_len >= _r_check_at[0]:
                                _r_check_at[0] = reasoning_total_len + LOOP_CHECK_STEP
                                check_targets.append(("reasoning", "".join(reasoning_tail)))

                            for src_name, tail_str in check_targets:
                                is_loop, pat = _detect_loop(tail_str)
                                if is_loop:
                                    loop_detected = True
                                    loop_pattern = f"[{src_name}] {pat}"
                                    await _log(
                                        f"🛑 检测到模型陷入复读循环（{src_name}：{pat[:80]}），"
                                        f"已累积 {content_total_len} 字符 content + "
                                        f"{reasoning_total_len} 字符 reasoning，立即关流"
                                    )
                                    consumer_done[0] = True
                                    try:
                                        if stream_holder[0] is not None:
                                            stream_holder[0].close()
                                    except Exception:
                                        pass
                                    break
                            if loop_detected:
                                break

                            # ── 无意义输出超时检测（核心防线，独立于 reasoning_content）──
                            if (
                                first_token_at is not None
                                and last_meaningful_growth_at is not None
                                and content_total_len > 15
                            ):
                                stall = now_t - last_meaningful_growth_at
                                if stall >= MEANINGFUL_STALL_SEC:
                                    loop_detected = True
                                    loop_pattern = (
                                        f"[meaningful_stall] 非空白字符卡在 {content_meaningful} 已 {stall:.0f}s，"
                                        f"但 content 总长仍在涨到 {content_total_len}（疑似空白/重复死循环）"
                                    )
                                    await _log(
                                        f"🛑 输出停滞：非空白字符已 {stall:.0f}s 无增长（停在 "
                                        f"{content_meaningful} 个），但 content 总长仍涨到 "
                                        f"{content_total_len}（在吐空白/重复字符）→ 立即关流"
                                    )
                                    consumer_done[0] = True
                                    try:
                                        if stream_holder[0] is not None:
                                            stream_holder[0].close()
                                    except Exception:
                                        pass
                                    break

                            # ── reasoning 段超长 ──
                            if content_total_len < 50 and reasoning_total_len >= REASONING_OVERRUN_LEN:
                                loop_detected = True
                                loop_pattern = (
                                    f"[reasoning_overrun] content 还只 {content_total_len} 字符，"
                                    f"reasoning 已 {reasoning_total_len} 字符（远超正常思考长度）"
                                )
                                await _log(
                                    f"🛑 reasoning 段失控：仅 {content_total_len} 字符 content 但 "
                                    f"reasoning 已 {reasoning_total_len} 字符，立即关流"
                                )
                                consumer_done[0] = True
                                try:
                                    if stream_holder[0] is not None:
                                        stream_holder[0].close()
                                except Exception:
                                    pass
                                break

                        fr = getattr(ch0, "finish_reason", None)
                        if fr:
                            finish_reason = fr
                    # 最后一个 chunk 带 usage（include_usage=True 的情况下）
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage:
                        try:
                            usage_dict = {
                                "prompt_tokens": getattr(chunk_usage, "prompt_tokens", None),
                                "completion_tokens": getattr(chunk_usage, "completion_tokens", None),
                                "total_tokens": getattr(chunk_usage, "total_tokens", None),
                            }
                        except Exception:
                            pass
                except Exception:
                    # 单 chunk 解析异常不影响整体
                    continue

            await _flush(force=True)
        finally:
            # 取消 / 循环命中：都不等线程结束（HTTP 流 close 后线程会自然退出，但 SDK 内部读
            # 可能慢几秒；让它在后台死亡，主路径立即推进）
            if cancelled_during_stream or loop_detected:
                consumer_done[0] = True
                # 给一个非常短的等待，能等到就最好；等不到也立即放手
                try:
                    await asyncio.wait_for(thread_task, timeout=0.1)
                except (asyncio.TimeoutError, Exception):
                    pass
            else:
                try:
                    await thread_task
                except Exception:
                    pass

        if cancelled_during_stream:
            await _log("⏹ 用户取消，已停止接收模型流式输出")
        if loop_detected and not finish_reason:
            finish_reason = "repetition_loop"
        return (
            "".join(text_parts).strip(),
            finish_reason,
            usage_dict,
            cancelled_during_stream,
            loop_detected,
            loop_pattern,
        )

    async def _call_with_retry(with_json_mode: bool, with_usage: bool = True,
                                with_no_think: bool | None = None):
        """带指数退避的调用包装（自动选 流式/非流式）。

        with_no_think=None 时使用 cfg.force_no_think 的值（默认 False）。
        强烈建议保持 False —— 实测 NVIDIA NIM 对 reasoning_effort/enable_thinking
        等非标参数极敏感，会让 TTFB 从几秒涨到几分钟。

        重试覆盖三类瞬时故障：
          1) 429 限流  → 优先解析 Retry-After / X-RateLimit-Reset，否则默认退避
          2) 5xx 网关错误（502/503/504）→ Cloudflare Tunnel / nginx 反代时常见，
             上游慢一秒就 504。这类错误下次重试很可能就好。
          3) 连接错误（APIConnectionError / ConnectionError / ReadError / RemoteDisconnected）
             → 多发生在 keep-alive 连接被对端 RST。等几秒再连一次几乎都能成。

        策略：
        - 最多 4 次尝试（1 + 3 次重试）
        - 取消时立即停止重试
        - 不重试：401/403（鉴权）、404（模型不存在）、400（请求体非法）、JSON 解析等业务错
        """
        if with_no_think is None:
            with_no_think = bool(cfg.get("force_no_think", False))
        attempts = 4
        backoffs = [1.5, 4.0, 9.0, 20.0]
        last_exc: Exception | None = None
        for i in range(attempts):
            try:
                if use_stream:
                    return await _call_model_streaming(
                        with_json_mode=with_json_mode,
                        with_usage=with_usage,
                        with_no_think=with_no_think,
                    )
                else:
                    return await _call_model_oneshot(
                        with_json_mode=with_json_mode,
                        with_no_think=with_no_think,
                    )
            except Exception as e:
                last_exc = e
                if _is_cancelled():
                    raise

                err_str = str(e)
                err_low = err_str.lower()
                err_type = type(e).__name__

                # 分类：哪些是值得重试的瞬时错误？
                is_429 = (
                    "429" in err_str
                    or "ratelimit" in err_low.replace(" ", "").replace("_", "")
                    or "too many" in err_low
                    or err_type == "RateLimitError"
                )
                # 5xx 网关错（不含 5xx 业务错。OpenAI SDK 把 500-599 都包成 InternalServerError）
                is_5xx = (
                    err_type in ("InternalServerError", "APIError")
                    and any(c in err_str for c in (" 500", " 502", " 503", " 504",
                                                   "code: 500", "code: 502", "code: 503", "code: 504"))
                ) or any(c in err_str for c in ("502 Bad Gateway", "503 Service Unavailable",
                                                "504 Gateway Time-out", "504 Gateway Timeout"))
                # 连接错误：keep-alive 被 RST、DNS 抖动、tunnel 闪断
                is_conn = (
                    err_type in ("APIConnectionError", "APITimeoutError", "ConnectionError",
                                 "ConnectError", "ReadError", "RemoteProtocolError",
                                 "ReadTimeout", "ConnectTimeout")
                    or "connection error" in err_low
                    or "connection reset" in err_low
                    or "remote end closed" in err_low
                    or "server disconnected" in err_low
                    or "broken pipe" in err_low
                )

                retryable = is_429 or is_5xx or is_conn
                if not retryable:
                    raise

                # 没有下一次重试机会了
                if i >= attempts - 1:
                    raise

                # 决定等多久 + 提示文案
                wait_secs: float | None = None
                kind: str
                if is_429:
                    kind = "429 限流"
                    # 尝试从响应头里读限流信息
                    try:
                        resp = getattr(e, "response", None)
                        if resp is not None and hasattr(resp, "headers"):
                            h = resp.headers
                            ra = h.get("Retry-After") or h.get("retry-after")
                            if ra:
                                wait_secs = float(ra)
                                kind = f"429（Retry-After={ra}）"
                            else:
                                reset = (
                                    h.get("X-Ratelimit-Reset")
                                    or h.get("x-ratelimit-reset")
                                    or h.get("X-RateLimit-Reset")
                                )
                                if reset:
                                    try:
                                        val = float(reset)
                                        import time as _t
                                        if val > 1_000_000_000:
                                            wait_secs = max(0.5, val - _t.time())
                                        else:
                                            wait_secs = max(0.5, val)
                                        kind = f"429（X-RateLimit-Reset={reset}）"
                                    except ValueError:
                                        pass
                    except Exception:
                        pass
                elif is_5xx:
                    kind = "5xx 网关错（多为反代/Tunnel 上游超时）"
                else:
                    kind = "连接错（keep-alive 被对端 RST）"

                if wait_secs is None:
                    wait_secs = backoffs[min(i, len(backoffs) - 1)]
                wait_secs = min(60.0, max(0.5, wait_secs))

                # ▶ 429 / 5xx → 把"全局静默期"注入共享限速器，让**所有并发图**
                # 都自动后退 wait_secs 秒，避免"A 图刚 429 我这里等着，B 图不知情
                # 又撞上去"的雪崩。
                if is_429 or is_5xx:
                    try:
                        from ..services import rate_limiter as _rl
                        await _rl.limiter.penalize(
                            "vision",
                            pause_sec=wait_secs,
                            reason=kind,
                        )
                    except Exception:
                        pass

                await _log(
                    f"🔄 {kind}：{err_type}: {err_str[:120]}"
                    f"  → 第 {i + 1}/{attempts} 次失败，等待 {wait_secs:.1f}s 后重试"
                    + ("（已同步全局静默期给其它并发图）" if (is_429 or is_5xx) else "")
                    + "…"
                )

                slept = 0.0
                while slept < wait_secs:
                    if _is_cancelled():
                        raise
                    await asyncio.sleep(min(0.5, wait_secs - slept))
                    slept += 0.5

        # 理论上走不到这里（要么 return 要么 raise），保险起见
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("retry exhausted without exception captured")


    try:
        # ============================================================
        # 墙钟总超时硬防线：不管流式 / 非流式 / 循环检测是否工作，
        # 单图最多占用 MAX_WALL_SEC 秒；超过直接中断返回错误。
        # 这是"保命底线"——之前看到过"景顺长城景顺长城..."刷屏 30+ 秒的
        # 根本原因就是内部检测在某些字符分布下失效。用 asyncio.wait_for 封一层
        # 外壳，保证不管底层发生什么，最多 MAX_WALL_SEC 就推进到下一张图。
        # ============================================================
        async def _call_and_guard():
            """封装完整的调用 + 参数降级重试链，供外层 wait_for 统一超时。"""
            nonlocal use_json_mode
            try:
                return await _call_with_retry(with_json_mode=use_json_mode)
            except Exception as inner:
                inner_msg = str(inner).lower()
                # response_format 在某些三方代理 / 老模型上不支持 → 降级一次
                if use_json_mode and any(k in inner_msg for k in [
                    "response_format", "response format", "unsupported", "not support",
                    "unknown parameter", "invalid parameter", "json_object",
                ]):
                    await _log("当前服务端不支持 response_format=json_object，已降级为普通模式重试")
                    result = await _call_with_retry(with_json_mode=False)
                    use_json_mode = False
                    return result
                # 流式相关参数（stream_options.include_usage）个别代理不认 → 静默重试
                if "stream_options" in inner_msg or "include_usage" in inner_msg:
                    await _log("服务端不识别 stream_options，重试时不带 usage")
                    return await _call_with_retry(
                        with_json_mode=use_json_mode, with_usage=False,
                    )
                # 严格代理可能拒绝 reasoning_effort / extra_body.thinking → 去掉重试
                if any(k in inner_msg for k in [
                    "reasoning_effort", "enable_thinking", "thinking",
                    "extra_body", "extra body",
                ]):
                    await _log("服务端不识别思考控制参数（reasoning_effort/enable_thinking），已去除并重试")
                    return await _call_with_retry(
                        with_json_mode=use_json_mode, with_no_think=False,
                    )
                raise

        try:
            text, finish_reason, usage_dict, cancelled, loop_hit, loop_pat = (
                await asyncio.wait_for(_call_and_guard(), timeout=MAX_WALL_SEC)
            )
        except asyncio.TimeoutError:
            # 硬超时：视同"模型跑飞"。返回明确错误，不再重试。
            # 注意：底层 _call_with_retry 启动的 to_thread 可能还在跑（SDK 同步读），
            # 但我们不等它——它会在 HTTP read 超时 / TCP RST 时自然死亡，
            # 主路径立即推进到下一张图。
            log_ai_event(
                "vision",
                "vision_wall_timeout",
                level="error",
                model=model_name,
                wall_sec=MAX_WALL_SEC,
            )

            # 硬超时通常意味着上游服务已经不稳（排队、复读循环、连接饱和）。
            # 给全局限速器注入一段惩罚期，让**并发的其它图先暂停一下**，
            # 避免"A 图刚超时，B/C 图紧接着也超时" 连锁反应。
            try:
                from ..services import rate_limiter as _rl_mod
                await _rl_mod.limiter.penalize(
                    "vision",
                    pause_sec=15.0,
                    reason="wall_timeout",
                )
            except Exception:
                pass
            await _log(
                f"⏰ 单图总耗时超过 {MAX_WALL_SEC:.0f}s 硬上限，"
                f"强制中断（通常意味着模型陷入了复读循环或上游挂起；"
                f"已给其它并发图注入 15s 冷静期）"
            )
            return {
                "platform": "识别失败", "items": [],
                "error": (
                    f"本图 OCR 耗时超过 {MAX_WALL_SEC:.0f}s 硬上限，已强制中断。"
                    f" 常见原因：1) 多模态模型在该图上陷入复读循环（如『XXXX XXXX XXXX…』"
                    f" 刷屏）；2) 模型服务端排队 / 反代超时；3) 图片含大量重复 UI 元素。"
                    f" 建议：a) 截图时裁掉底部按钮区，只留持仓清单；"
                    f" b) 换一张更清晰的截图；"
                    f" c) 换一个更稳定的多模态模型；"
                    f" d) 如需放宽超时，在设置里调整 vision.wall_timeout（默认 90s）"
                ),
                "loop_detected": True,
            }

        # 取消短路：直接返回 cancelled，不继续解析半截 JSON
        if cancelled:
            return {"platform": "已取消", "items": [], "cancelled": True}

        # 复读循环短路：模型陷入死循环时强制截断的输出几乎不可能解析成合法 JSON，
        # 也不应该当作"可重试错误"——重试只会再撞同一个循环，浪费 RPM。
        # 直接返回明确的错误，不尝试 _safe_parse（虽然 _try_repair_truncated_json
        # 偶尔能救出几项，但准确性极低，宁可让用户重传该图）。
        if loop_hit:
            # text_tail 用 !r 是故意的：它可能含 \n / \u200b 等不可见字符，需要看转义
            # 但 pattern 是给人读的中文描述，去掉 !r 避免 \uXXXX 噪声
            log_ai_event(
                "vision",
                "vision_loop_detected",
                level="error",
                model=model_name,
                pattern=loop_pat,
                text_len=len(text),
                text_tail=text[-200:],
            )

            await _log(
                f"💥 模型在该图上陷入复读循环（重复输出「{loop_pat[:30]}…」），已强制中断；"
                f"该图未能成功识别，建议重新上传或换张更清晰的截图"
            )
            return {
                "platform": "识别失败", "items": [],
                "error": (
                    f"模型陷入复读循环，被强制中断（{loop_pat[:80]}）。"
                    f" 这通常发生在截图含大量重复 UI 元素（按钮/列表项）或"
                    f" 模型被『thinking 段』带偏时。"
                    f" 建议：1) 截图时只保留持仓清单区域，裁掉底部按钮区；"
                    f" 2) 在『设置 → 视觉模型』关闭 thinking_mode；"
                    f" 3) 换一个更稳定的多模态模型；4) 该图重传一次（重复循环不可重试）"
                ),
                "loop_detected": True,
            }

        usage_str = ""
        if usage_dict:
            usage_str = (
                f"输入 {usage_dict.get('prompt_tokens', '?')} / "
                f"输出 {usage_dict.get('completion_tokens', '?')} tokens，"
            )
        await _log(
            f"✅ 模型流式输出完成，{usage_str}finish_reason={finish_reason}，"
            f"原始内容 {len(text)} 字符"
        )

        # 关键：finish_reason=length 说明被 max_tokens 截断 → JSON 大概率不完整
        truncated = finish_reason == "length"
        if truncated:
            await _log(
                f"⚠️ 响应被 max_tokens={max_tokens} 截断（finish_reason=length），"
                f"JSON 可能不完整，将尝试修复；建议在『设置 → 视觉模型』把 max_tokens 调大到 12000+"
            )
    except Exception as e:
        # 详细错误信息：尽量从 OpenAI SDK 异常里挖出 status_code + body
        err_type = type(e).__name__
        err_msg = str(e)
        # OpenAI SDK 的 APIStatusError 会把 server 返回的 JSON 放在 .body / .response
        body = ""
        try:
            body_obj = getattr(e, "body", None)
            if body_obj:
                body = json.dumps(body_obj, ensure_ascii=False)[:600]
        except Exception:
            pass
        if not body:
            try:
                resp_obj = getattr(e, "response", None)
                if resp_obj is not None and hasattr(resp_obj, "text"):
                    body = str(resp_obj.text)[:600]
            except Exception:
                pass
        log_ai_event(
            "vision",
            "vision_call_failed",
            level="error",
            model=cfg.get("model"),
            error_type=err_type,
            error=err_msg[:1000],
            response_body=body,
        )
        await _log(f"视觉模型调用失败：{err_type}: {err_msg[:200]}")


        # 给前端一个可读的错误提示
        hint = ""
        low = err_msg.lower()
        if "blocked" in low or "permissiondenied" in low.replace(" ", "") or "permission_denied" in low:
            hint = "（多为内容安全/鉴权拒绝：截图含敏感字样、模型不支持图像、或 API Key 没开图像权限）"
        elif "model not found" in low or "unknown model" in low:
            hint = "（模型不存在，请检查 model 名是否填对，例如 qwen-vl-max / glm-4v）"
        elif "401" in err_msg or "invalid api key" in low:
            hint = "（API Key 无效）"
        elif "429" in err_msg or err_type == "RateLimitError":
            hint = (
                "（RPM/TPM 限流。已自动指数退避重试 4 次仍失败。"
                " 不同服务商上限不同：NVIDIA NIM 免费 Kimi ~40 RPM，Kimi 官方免费档 3 RPM，阿里 Qwen-VL ~60 RPM。"
                " 建议：1) 到『设置 → 视觉模型』把『每分钟最大请求数』(rpm_limit) 调到比官方上限小 15%；"
                " 2) 把『并发』调到 1-2；3) 升级账户档位）"
            )
        elif "timeout" in low or err_type == "APITimeoutError":
            current_timeout = cfg.get("timeout", 600)
            hint = (
                f"（流式调用首字超时 {current_timeout}s。多发生在网络不通 / 模型排队 / API Key 限速时；"
                f" 检查能否访问 base_url，或换个时段再试）"
            )
        elif any(c in err_msg for c in (" 504", "code: 504", "504 Gateway")):
            hint = (
                "（504 网关超时：上游模型未在反代/网关超时窗口内响应。已自动重试 4 次仍失败。"
                " 在 NVIDIA NIM / Cloudflare Tunnel 等环境下，504 经常是『触发了 RPM 限流被默默丢弃』的伪装。"
                " 建议：1) 到『设置 → 视觉模型』调小『每分钟最大请求数』(rpm_limit)，"
                " NVIDIA NIM 免费档 Kimi 实测 ≤40 RPM；"
                " 2) 减少图片中持仓项数（一张 < 10 项）；"
                " 3) 调小 max_tokens 到 4096；4) 换时段重试）"
            )
        elif any(c in err_msg for c in (" 502", " 503", "code: 502", "code: 503", "Bad Gateway", "Service Unavailable")):
            hint = (
                "（5xx 网关错：上游服务暂时不可用。已自动重试 4 次仍失败。"
                " 多为模型方临时维护或反代抖动，等几分钟再试）"
            )
        elif err_type in ("APIConnectionError", "ConnectionError") or "connection error" in low:
            hint = (
                "（连接错误：到模型服务的 TCP 链路被切断。已自动重试 4 次仍失败。"
                " 排查：1) 能否 ping/curl 到 base_url；2) 是否走代理/Tunnel，对端是否在线；"
                " 3) 防火墙是否拦截了长连接）"
            )

        return {
            "platform": "错误", "items": [],
            "error": f"视觉模型调用失败：{err_type}: {err_msg[:300]}{hint}",
            "raw_body": body,
        }

    parsed, parse_mode = _safe_parse(text)
    if not parsed or not isinstance(parsed, dict):
        log_ai_event(
            "vision",
            "vision_parse_failed",
            level="error",
            model=model_name,
            finish_reason=finish_reason,
            text_len=len(text),
            text_head=text[:800],
        )
        await _log(

            f"模型返回不是合法 JSON（即使尝试剥离围栏 + 截断修复后仍失败）。"
            f"原始内容 {len(text)} 字符，前 200 字符：{text[:200]!r}"
        )
        # 给出明确的可读错误
        if truncated:
            # 区分：是模型只输出了几十字（说明 max_tokens 极小），还是输出了很多但仍被截
            if len(text) < 200:
                err_msg = (
                    f"模型只输出了 {len(text)} 字符就被截断（max_tokens={max_tokens}）。"
                    f" 这通常意味着服务端把 max_tokens 当成了硬上限。"
                    f" 请到『设置 → 视觉模型』把 max_tokens 调到 8192 或更高。"
                )
            else:
                err_msg = (
                    f"JSON 解析失败：模型输出 {len(text)} 字符后被 max_tokens={max_tokens} 截断。"
                    f" 请到『设置 → 视觉模型』把 max_tokens 调到 12000 或更高（持仓项越多需要越大）。"
                )
        elif not use_json_mode:
            err_msg = (
                "模型返回不是合法 JSON（且服务端不支持 json_object）。"
                " 建议换用支持 JSON Mode 的模型（Kimi / GLM-4V / Qwen-VL-Max 等）。"
            )
        else:
            err_msg = "模型返回不是合法 JSON。可能是模型对图像理解失败、或输出含大段说明文字。"
        return {
            "platform": "解析失败", "items": [],
            "error": err_msg,
            "raw": text[:500],
        }

    if parse_mode == "repaired":
        await _log("ℹ️ JSON 通过截断修复成功，最后若干项可能丢失，请人工核对识别结果")

    # 兜底字段
    parsed.setdefault("platform", "未知")
    parsed.setdefault("items", [])
    if not isinstance(parsed.get("items"), list):
        parsed["items"] = []

    # 类型规范化（兜底）
    valid_types = {t.value for t in models.AssetType}
    fixed_types = 0
    for it in parsed["items"]:
        if not isinstance(it, dict):
            continue
        if it.get("asset_type") not in valid_types:
            it["asset_type"] = "fund"  # 兜底
            fixed_types += 1
    item_count = len(parsed["items"])
    log_ai_event(
        "vision",
        "vision_parse_done",
        model=model_name,
        platform=parsed.get("platform"),
        item_count=item_count,
        parse_mode=parse_mode,
        fixed_types=fixed_types,
        finish_reason=finish_reason,
        text_len=len(text),
    )
    await _log(
        f"JSON 解析成功（{parse_mode}），平台={parsed.get('platform')}，"
        f"识别到 {item_count} 项"
        + (f"（{fixed_types} 项类型已兜底为 fund）" if fixed_types else "")
    )
    return parsed



async def parse_images_concurrently(
    db,
    images: list[tuple[bytes, str, str]],
    *,
    concurrency: int | None = None,
) -> list[dict[str, Any]]:
    """并发解析多张图。images 元素：(bytes, mime, platform_hint)。"""
    cfg = settings_service.get(db, "vision") or {}
    sem = asyncio.Semaphore(max(1, concurrency or cfg.get("concurrency", 2)))

    async def _one(idx: int, item: tuple[bytes, str, str]) -> dict:
        b, mime, hint = item
        async with sem:
            r = await parse_image(db, b, mime=mime, platform_hint=hint)
            r["_index"] = idx
            return r

    results = await asyncio.gather(*[_one(i, im) for i, im in enumerate(images)])
    results.sort(key=lambda r: r.get("_index", 0))
    return results
