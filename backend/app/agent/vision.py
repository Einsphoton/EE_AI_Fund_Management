"""Vision agent: 把"持仓页截图"解析为结构化持仓清单。

设计要点：
- 走 OpenAI Chat Completions 兼容协议（image_url 多模态消息）
- 不限定平台，prompt 里给一份"通用 schema + 多平台示例 + 关键字典"
- 容错：模型可能输出 ```json``` 代码围栏、说明文字，需要剥离
- 失败时返回 `{"items": [], "error": "..."}`，不抛异常

输出 schema（每张图一个对象）：
{
  "platform": "微信理财通" | "支付宝财富" | "招行" | "富途" | "..." | "未知",
  "screenshot_date": "YYYY-MM-DD" 或 null（识别截图上的截屏时间）,
  "items": [
    {
      "name": "兴全合宜",
      "code": "163406" 或 null,
      "asset_type": "fund" | "stock" | "etf" | "money_fund" | "wealth" | "cash" | "bond",
      "shares": 1234.5678 或 null,
      "amount": 12345.67 或 null,            // 持有金额（货基/理财必填）
      "avg_cost": 1.234 或 null,              // 平均成本/持仓单价
      "current_price": 1.345 或 null,         // 截图最新价/净值
      "market_value": 12345.67 或 null,       // 当前市值
      "profit": 123.45 或 null,               // 累计收益（盈亏）
      "profit_pct": 1.23 或 null,             // 收益率(%)
      "yield_7d": 1.85 或 null,               // 货基的 7 日年化(%)
      "expected_apr": 3.5 或 null,            // 理财的预期年化(%)
      "maturity_date": "YYYY-MM-DD" 或 null,  // 理财到期日
      "raw_text": "原文卡片中所有可见文字（用于排错）"
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
from ..services import settings_service


VISION_SYSTEM_PROMPT = """你是一个专业的"个人理财截图识别助手"。用户会上传他们在以下任一 App 的"持仓页"截图：
- 微信理财通（基金 / 货币基金 / 理财 / 黄金）
- 支付宝财富（基金 / 余额宝 / 余利宝 / 理财）
- 招商银行 App（朝朝宝 / 朝朝盈 / 基金 / 理财 / 定期存款）
- 中国银行、平安银行、工行等手机银行（活期 / 定期 / 大额存单 / 理财）
- 富途、招商证券、中银国际、雪盈等证券 App（A 股 / 港股 / 美股 / ETF）

你的任务：把图中所有可见的持仓项识别出来，返回 **严格的 JSON**，schema 如下：

{
  "platform": "<识别到的平台名，未识别填'未知'>",
  "screenshot_date": "YYYY-MM-DD" 或 null,
  "items": [
    {
      "name": "<完整产品名称>",
      "code": "<6 位基金代码 / 股票代码，没显示填 null>",
      "asset_type": "fund" | "stock" | "etf" | "money_fund" | "wealth" | "cash" | "bond",
      "shares": <持仓份额/股数；货基/理财/现金可填 null>,
      "amount": <持有金额（元）；货基/理财/现金必填，其他可填 null>,
      "avg_cost": <平均成本/持仓单价，没显示填 null>,
      "current_price": <最新价/净值，没显示填 null>,
      "market_value": <当前市值（元），没显示填 null>,
      "profit": <累计收益（元），亏损填负数，没显示填 null>,
      "profit_pct": <收益率（%）数字，没显示填 null>,
      "yield_7d": <货基的7日年化(%)，仅货基填，其他类型 null>,
      "expected_apr": <理财的预期年化(%)，仅理财填>,
      "maturity_date": "YYYY-MM-DD" 或 null,
      "raw_text": "<这一项卡片上能看到的所有文字，用空格连接>"
    }
  ]
}

### 资产类型判定规则
- "余额宝 / 朝朝宝 / 朝朝盈 / 零钱通 / 添益宝 / 余利宝" → money_fund
- "活期 / 活期+ / 活钱 / 现金宝" 类（不强调收益）→ cash
- "XX 天 / XX 个月 / 定期 / 净值型 / 结构性 / 大额存单 / 国债" → wealth；如果是国债且能看到代码 → bond
- "ETF / LOF / 联接基金（场内）" → etf
- "联接 A / C / 普通公募基金" → fund
- 股票（个股代码）→ stock

### 关键约束
1. **必须输出可被 JSON.parse 解析的纯 JSON**，不要任何解释、不要 ```json``` 围栏、不要尾随逗号。
2. 数值字段必须是裸数字，不要带"元"、"%"、"约"、"≈" 等单位/前缀；不确定就填 null。
3. 截图模糊看不清的字段宁可填 null 也不要瞎猜。
4. 同一截图里多个持仓要全部列出来（一张图常有 5-15 项）。
5. raw_text 帮我留一份原始文字，方便我后续核对。
6. 如果整张图根本不是持仓页（是首页、广告、聊天截图等），返回 `{"platform":"未知","items":[]}`。
"""


def _get_vision_config(db) -> dict | None:
    """读 vision 配置；支持 use_ai 模式（直接复用 ai 配置的 base_url/key/model）。

    返回 None 表示未配置（前端会引导）。
    """
    cfg = settings_service.get(db, "vision") or {}
    if cfg.get("use_ai"):
        # 复用 ai 配置：把 ai 的 base_url / api_key / model 拷过来作为 vision 用
        ai_cfg = settings_service.get(db, "ai") or {}
        if not ai_cfg.get("base_url") or not ai_cfg.get("model"):
            return None
        merged = {
            "base_url": ai_cfg.get("base_url"),
            "api_key": ai_cfg.get("api_key") or "",
            "model": ai_cfg.get("model"),
            # 这些性能字段优先用 vision 自己的，其次回退到 ai 的
            "temperature": cfg.get("temperature", 0.1),
            "max_tokens": cfg.get("max_tokens", ai_cfg.get("max_tokens", 4096)),
            "timeout": cfg.get("timeout", ai_cfg.get("timeout", 180)),
            "concurrency": cfg.get("concurrency", 2),
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
    """
    from .hermes import _build_cf_headers  # 复用 hermes 的 CF header 构造逻辑
    ai_cfg = settings_service.get(db, "ai") or {}
    headers = _build_cf_headers(cfg["base_url"], ai_cfg)
    default_headers = {"User-Agent": headers.get("User-Agent", "")}
    if "CF-Access-Client-Id" in headers:
        default_headers["CF-Access-Client-Id"] = headers["CF-Access-Client-Id"]
        default_headers["CF-Access-Client-Secret"] = headers["CF-Access-Client-Secret"]
    return OpenAI(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"] or "EMPTY",
        timeout=cfg.get("timeout", 180),
        default_headers=default_headers,
    )


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


def _safe_parse(text: str) -> dict | None:
    """尽力解析 JSON。"""
    try:
        return json.loads(text)
    except Exception:
        pass
    cleaned = _strip_json_noise(text)
    try:
        return json.loads(cleaned)
    except Exception:
        return None


def _img_to_data_url(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


async def parse_image(
    db,
    image_bytes: bytes,
    *,
    mime: str = "image/jpeg",
    platform_hint: str = "",
) -> dict[str, Any]:
    """解析单张截图。返回标准化字典；失败时 items=[]。"""
    cfg = _get_vision_config(db)
    if not cfg:
        # 给出针对性提示
        v = settings_service.get(db, "vision") or {}
        if v.get("use_ai"):
            msg = "已开启『复用 AI 大模型』，但 AI 大模型未配置 base_url / model。请到『设置 → AI 大模型』填好。"
        else:
            msg = "请先在『设置 → 视觉模型』填入 base_url / api_key / model；或勾选『复用 AI 大模型』。"
        return {"platform": "未配置", "items": [], "error": msg}

    client = _build_client(db, cfg)
    data_url = _img_to_data_url(image_bytes, mime)

    user_text = "请识别这张截图里的所有持仓项，按 schema 输出 JSON。"
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

    try:
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model=cfg["model"],
            messages=messages,
            temperature=cfg.get("temperature", 0.1),
            max_tokens=cfg.get("max_tokens", 4096),
        )
        text = (resp.choices[0].message.content or "").strip()
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
        # 控制台打印完整错误，便于在后端日志里排查
        print(f"[vision] FAIL model={cfg.get('model')} type={err_type} msg={err_msg[:300]}")
        if body:
            print(f"[vision] response_body={body}")

        # 给前端一个可读的错误提示
        hint = ""
        low = err_msg.lower()
        if "blocked" in low or "permissiondenied" in low.replace(" ", "") or "permission_denied" in low:
            hint = "（多为内容安全/鉴权拒绝：截图含敏感字样、模型不支持图像、或 API Key 没开图像权限）"
        elif "model not found" in low or "unknown model" in low:
            hint = "（模型不存在，请检查 model 名是否填对，例如 qwen-vl-max / glm-4v）"
        elif "401" in err_msg or "invalid api key" in low:
            hint = "（API Key 无效）"
        elif "429" in err_msg:
            hint = "（被限流，请稍后重试或降低并发）"
        elif "timeout" in low:
            hint = "（请求超时，截图过大或网络慢）"

        return {
            "platform": "错误", "items": [],
            "error": f"视觉模型调用失败：{err_type}: {err_msg[:300]}{hint}",
            "raw_body": body,
        }

    parsed = _safe_parse(text)
    if not parsed or not isinstance(parsed, dict):
        return {
            "platform": "解析失败", "items": [],
            "error": "模型返回不是合法 JSON",
            "raw": text[:500],
        }

    # 兜底字段
    parsed.setdefault("platform", "未知")
    parsed.setdefault("items", [])
    if not isinstance(parsed.get("items"), list):
        parsed["items"] = []

    # 类型规范化（兜底）
    valid_types = {t.value for t in models.AssetType}
    for it in parsed["items"]:
        if not isinstance(it, dict):
            continue
        if it.get("asset_type") not in valid_types:
            it["asset_type"] = "fund"  # 兜底
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
