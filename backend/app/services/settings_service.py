"""KV settings helper."""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from sqlalchemy.orm import Session


from .. import models


DEFAULTS: dict[str, Any] = {
    "ai": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "temperature": 0.4,
        # 批量分析的最大并发度（1=串行）。
        # 默认 2：跟 vision 保持一致——多模态/思考模型主要瓶颈在远端推理 + 网络往返，
        # 并发 2 在大多数服务上既不会触发 RPM 限流，也能比串行快 1.6-1.8x。
        # 服务支持差就调到 1；本地大模型 + 内网直连可手动调 4-6。
        "batch_concurrency": 2,
        # 单次 LLM 响应的最大 token 数（0 = 不限制）。
        # 默认 4096：
        # - 普通对话模型只用其中 1500-2500 写完整 JSON（够用）
        # - reasoning 模型 reasoning 段会吃 2000-3000，再加 content 1000，4096 是平衡点
        # 设得太低（如 800）reasoning 模型会被截断，永远写不出 content；设得太高
        # （如 8192+）经 CF 时会因生成耗时过长触发 524 超时。
        "max_tokens": 4096,
        # HTTP 超时（秒）。本地 Ollama 吐丰富 JSON 可能较慢
        "timeout": 180,
        # === 限速控制（双层保险）与 vision 共用同一套语义 ===
        # rpm_limit：每分钟最大请求数（滑动窗口）。0 = 不限。
        # NVIDIA NIM 免费档 Kimi K2 ~40 RPM、DeepSeek ~60 RPM、阿里通义千问 ~60 RPM。
        # 推荐设成官方上限的 85%（留余量给重试），如 40 → 35。
        # 跟 batch_concurrency 是协作关系：先看 RPM 是否还有名额，再用 concurrency 控并发。
        "rpm_limit": 0,
        # min_interval_sec：相邻两次请求最小硬间隔（秒）。已被 rpm_limit 覆盖大多数
        # 场景；只在某些代理/网关明确要求"两次之间至少 N 秒"时启用。
        "min_interval_sec": 0,
        # NIM 友好优化：不降低模型能力，只通过全局排队/预算片平滑 RPM/TPM/并发尖峰。
        # 关闭后按用户填写的 rpm_limit/min_interval 原样执行。
        "nim_optimization_enabled": True,
        # ==== 成本控制 / Token 节省 ====
        # cost_mode:
        #   quality  - 质量优先：保持 60 日 OHLC + 长点评，不复用近期结果
        #   balanced - 均衡省钱：压缩行情和输出，批量分析可复用 12 小时内小波动结果
        #   economy  - 极省钱：更短行情和点评，批量分析可复用 24 小时内小波动结果
        "cost_mode": "quality",
        # 对支持的 OpenAI-compatible 服务启用 JSON Mode，减少废话和解析失败重试。
        "json_mode": True,
        # 是否记录 usage token（包括 DeepSeek cache hit/miss 字段，若服务端返回）。
        "token_usage_logging": True,
        # ==== 思考 / Reasoning 控制（统一抽象，兼容 2026 年主流大模型）====


        # thinking_mode:
        #   "auto"  - 不显式传任何思考参数，让模型按默认行为运行（推荐）
        #   "on"    - 强制开启思考（透传 enable_thinking=true / thinking.type="enabled"）
        #   "off"   - 强制关闭思考（适用于"hybrid"模型如 DeepSeek V4 / Qwen3.5 / GLM-5）
        # 三套参数会同时透传，不认识的字段会被 SDK 放进 extra_body 或被服务端忽略：
        #   - enable_thinking + thinking_budget (DeepSeek V4 / Qwen3.5 / GLM 系 / 豆包 / MiniMax)
        #   - thinking: {type, budget_tokens}  (Anthropic Claude / 部分 GLM)
        #   - reasoning_effort                  (OpenAI o-series / GPT-5 / Kimi K2 / Grok 4)
        "thinking_mode": "auto",
        # 思考 token 预算（0 = 不限制）。仅 thinking_mode=on 时生效。
        # 推荐值：浅思考 1024 / 标准 4096 / 深度推理 16384
        "thinking_budget": 0,
        # OpenAI o-series / GPT-5 / Kimi 风格的思考强度
        "reasoning_effort": "medium",
        # 投资者性格：见 agent/profiles.py INVESTOR_PROFILES
        # balanced / conservative / aggressive / income / growth / value / trader
        "investor_profile": "balanced",
        # 分析报告风格：pro（专业）/ beginner（新手）
        "report_style": "pro",
        # Cloudflare Access Service Token（用于调用受 CF Zero Trust 保护的自建 API）
        # 留空表示不启用；非空时会在请求 Header 中注入 CF-Access-Client-Id/Secret
        "cf_access_client_id": "",
        "cf_access_client_secret": "",
        # 逗号分隔的域名列表，只有 base_url 包含其中任意一项时才注入上面的 CF Header
        # 默认为空 = 只要配置了 Client Id/Secret，对所有请求都注入
        "cf_access_hosts": "",
        # 可选 AI Provider 池：批量资产分析时会在主配置 + providers 之间轮询，
        # 每个 provider 拥有独立 RPM/间隔窗口，用于多组合法 API Key 的故障转移与分摊。
        "pool_include_primary": True,
        "pool_primary_name": "主配置",
        "providers": [],
    },
    "vision": {
        # 多模态视觉模型，用于截图 OCR 解析持仓页。
        # use_ai=True：直接复用 ai 配置（要求 ai 配的是多模态模型，如 qwen-vl/glm-4v/gpt-4o）
        # use_ai=False：单独配置以下 base_url/api_key/model
        "use_ai": True,
        "base_url": "",
        "api_key": "",
        "model": "",
        "temperature": 0.1,
        # 持仓页 JSON 可能很长（5-15 项 × 每项 ~250 tokens），默认给到 8192；
        # 复杂截图建议手动调到 12000+，避免被截断导致 JSON 解析失败
        "max_tokens": 8192,
        # 流式调用下首字几秒就到，整体一般 30-60s；300s 足够覆盖网络抖动
        "timeout": 300,
        # 默认 2：单个截图视觉模型大头是网络/模型本身，并发 2 在大多数服务上很安全；
        # NVIDIA NIM 免费 Kimi、阿里 Qwen-VL 通常允许 ≥3。硬件好的可手动调到 3-4。
        "concurrency": 2,
        # === 限速控制（双层保险）===
        # rpm_limit：每分钟最大请求数，使用滑动窗口算法精确控制；0 = 不限
        # **默认 20**：对 NVIDIA NIM 免费档 Kimi（~40 RPM）是保守的 50%，给重试留足余量。
        # 不设限速时 9 张图并发 2 路容易触发服务端风控，连炸多张 429。
        # 用付费档或私有部署可以调高到 60-120。
        "rpm_limit": 20,
        # min_interval_sec：两张图之间硬性最小间隔（秒）。
        # 已被 rpm_limit 覆盖大多数场景；保留作为兜底，比如某些代理 RPM 看上去够但
        # 仍要求请求间隔最少 N 秒的奇葩限制。两者同时生效，谁严格谁说了算。
        "min_interval_sec": 0,
        # 是否开启 JSON Mode（response_format=json_object）。
        # Kimi / Moonshot / GLM-4V / Qwen-VL 都支持；不支持的服务端会自动降级
        "json_mode": True,
        # 是否流式调用模型（stream=True）。默认关。
        # 流式看似 TTFB 快、能看到思考过程，但实测有大量隐藏开销：
        #   - Kimi K2 / NVIDIA NIM 每个 chunk 1-3 字符 → 一张图 2000-3000 chunk
        #   - OpenAI SDK 同步流，我们用线程消费 + asyncio.Queue 跨线程投递，
        #     每 chunk 至少 1 次 run_coroutine_threadsafe 往返
        #   - 主循环还要做循环检测、增量计算、SSE 推日志 → CPU/事件循环吃满
        # 非流式（一次性 await）通常比流式快 50%-100%；只有要看"AI 实时思考"
        # 才打开。注意：开了流式后才有"复读循环检测"和"reasoning 卡死"保护，
        # 关了的话遇到模型死循环只能等 timeout，但 OCR 任务一般 30s 内完成，影响不大。
        "stream": False,
        # 是否在 OCR 解析后自动调用天天基金 API 补全 fund/etf 代码。
        # 默认**开启**：用户期望 OCR 完成就能看到代码。实现已经做了严格的超时保护：
        #   - 单条请求 3s 超时
        #   - 整批最多并发 8 条、总时间不超过 5s
        # 即便天天基金 API 全挂，也最多给每张图加 5s 的后处理时间。
        # 用户嫌慢可以在"设置 → 视觉模型"里把这个开关关掉。
        "auto_fill_code": True,
        # === 死循环 / 跑飞硬防线（多层）===
        # 多模态模型在 OCR 场景下有天然的"复读循环"风险：识别到重复 UI 文字
        # （如"景顺长城景顺长城景顺长城..."）或 reasoning 段卡死时，会把 max_tokens
        # 烧光都不收尾。以下三道硬防线独立生效，任一触发都会立即中断当前图：
        #
        # 1) wall_timeout：单图端到端总耗时上限（秒）。正常 15-30s；90s 给 3× 余量。
        #    超过就强制结束这张图（不影响其它图），避免拖死整个批次。
        "wall_timeout": 90,
        # 2) content_hardcap：单图 content 累积字符硬上限。正常 20 项持仓约 5000-8000
        #    字符；20000 给 2.5× 余量。超过就关流（仅流式路径有意义）。
        "content_hardcap": 20000,
        # 3) force_stream：想用流式必须显式开启这个开关（且 stream=True 才生效）。
        #    默认关：因为流式路径是复读循环的主要触发点，非流式天然有 max_tokens
        #    服务端硬截断、没有"持续写字"问题。只有明确需要看实时输出才打开。
        "force_stream": False,
    },
    "schedule": {
        "enabled": False,
        "cron": "0 9 * * *",      # 每天 9:00
        "preset": "daily",         # daily | every6h | weekly | custom
        "include_investment_plan": False,  # 定时分析后是否顺带生成 AI 投资经理待确认建议
        "include_ai_targets": False,       # 定时分析后是否顺带更新 AI 推荐标的
    },
    "investment_budget": {
        # 平台月投资额度：[{platform, currency, monthly_amount, asset_types}]
        # asset_types 可包含 fund / stock / etf；同一平台可配置多个币种。
        "items": [],
    },
    "quote_sources": {
        # 基金当前价口径：eastmoney_realtime=天天基金实时估值；eastmoney_nav=官方最新净值
        "fund_current": "eastmoney_realtime",
        # 股票/ETF 当前价口径：腾讯 / 东方财富 / 新浪（A股） / K 线最后收盘价
        "stock_current": "tencent_realtime",
        # K 线来源：A 股/ETF 可选 sina / tencent / eastmoney；港股 tencent / eastmoney；美股 tencent / yahoo
        "a_stock_kline": "sina",
        "hk_stock_kline": "tencent",

        "us_stock_kline": "tencent",
        # 主源失败后是否自动回退到其它公开源
        "fallback_enabled": True,
    },
    "ui": {

        "currency": "CNY",
        "theme": "dark",
    },
}


def _with_defaults(key: str, value: Any) -> Any:
    """对 dict 类型设置做浅合并，避免旧数据库行缺少新增配置项时前端读不到默认值。"""
    default = DEFAULTS.get(key)
    if isinstance(default, dict) and isinstance(value, dict):
        merged = deepcopy(default)
        merged.update(value)
        return merged
    return value


def current_user_id(db: Session, user_id: int | None = None) -> int | None:

    if user_id is not None:
        return user_id
    try:
        uid = db.info.get("user_id")
        return int(uid) if uid is not None else None
    except Exception:
        return None


def scoped_key(key: str, user_id: int | None) -> str:
    return f"u:{user_id}:{key}" if user_id else key


def get(db: Session, key: str, user_id: int | None = None) -> Any:
    uid = current_user_id(db, user_id)
    row = db.query(models.AppSetting).filter_by(key=scoped_key(key, uid)).first()
    if row is None:
        return deepcopy(DEFAULTS.get(key))
    return _with_defaults(key, row.value)



def get_all(db: Session, user_id: int | None = None) -> dict[str, Any]:
    uid = current_user_id(db, user_id)
    out = deepcopy(DEFAULTS)
    if uid:
        prefix = f"u:{uid}:"
        rows = db.query(models.AppSetting).filter(models.AppSetting.key.startswith(prefix)).all()
        for row in rows:
            raw_key = row.key[len(prefix):]
            out[raw_key] = _with_defaults(raw_key, row.value)
        return out

    for row in db.query(models.AppSetting).all():
        if not str(row.key).startswith("u:"):
            out[row.key] = _with_defaults(row.key, row.value)

    return out


def set_value(db: Session, key: str, value: Any, user_id: int | None = None) -> Any:
    uid = current_user_id(db, user_id)
    target_key = scoped_key(key, uid)
    row = db.query(models.AppSetting).filter_by(key=target_key).first()
    stored_value = value
    if isinstance(value, dict) and isinstance(DEFAULTS.get(key), dict):
        base = _with_defaults(key, row.value) if row is not None else deepcopy(DEFAULTS.get(key))
        if isinstance(base, dict):
            base.update(value)
            stored_value = base
    if row is None:
        row = models.AppSetting(key=target_key, value=stored_value)
        db.add(row)
    else:
        row.value = stored_value
    db.commit()
    return stored_value


