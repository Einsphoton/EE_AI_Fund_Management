"""Hermes-Lite Agent: a minimal but extensible agent inspired by Hermes / OpenClaw.

设计要点：
- Agent 由若干 Skill 组合（每个 Skill = system prompt 段 + 可选的工具调用）。
- 通过 OpenAI 兼容协议调用任意大模型（DeepSeek / OpenAI / 本地 Ollama 等）。
- 输入：标的信息 + 行情序列 + 持仓信息；输出：结构化建议 JSON（含基本面/宏观/微观/风险）。
- 失败时使用启发式回退（基于均线/盈亏阈值）保证 Agent 永远能出建议。
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any

import httpx
from openai import OpenAI

from .profiles import get_profile_prompt, get_report_style_prompt


# ---------------------------------------------------------------------------
# 共享客户端缓存：按 (base_url, api_key, timeout, cf_*) 指纹复用 OpenAI 客户端，
# 避免每个标的都重建 TLS 连接与 Cloudflare Access 握手。
# ---------------------------------------------------------------------------
_CLIENT_LOCK = threading.Lock()
_CLIENT_CACHE: dict[tuple, tuple[OpenAI, httpx.Client]] = {}


def _build_cf_headers(base_url: str, ai_config: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }
    cf_client_id = str((ai_config or {}).get("cf_access_client_id") or "").strip()
    cf_client_secret = str((ai_config or {}).get("cf_access_client_secret") or "").strip()
    cf_hosts_raw = str((ai_config or {}).get("cf_access_hosts") or "").strip()
    if not cf_client_id:
        cf_client_id = os.getenv("CF_ACCESS_CLIENT_ID", "").strip()
    if not cf_client_secret:
        cf_client_secret = os.getenv("CF_ACCESS_CLIENT_SECRET", "").strip()
    if not cf_hosts_raw:
        cf_hosts_raw = os.getenv("CF_ACCESS_HOSTS", "").strip()

    if cf_client_id and cf_client_secret and base_url:
        low_url = base_url.lower()
        if cf_hosts_raw:
            cf_hosts = [h.strip().lower() for h in cf_hosts_raw.split(",") if h.strip()]
            hit = any(h in low_url for h in cf_hosts)
        else:
            hit = True
        if hit:
            headers["CF-Access-Client-Id"] = cf_client_id
            headers["CF-Access-Client-Secret"] = cf_client_secret
    return headers


def _get_openai_client(base_url: str, api_key: str, timeout_sec: int, ai_config: dict[str, Any]) -> OpenAI:
    """取/建一个按配置指纹缓存的 OpenAI 客户端，跨标的共享以复用 TCP/TLS 连接。"""
    headers = _build_cf_headers(base_url, ai_config)
    key = (
        base_url,
        api_key,
        timeout_sec,
        headers.get("CF-Access-Client-Id", ""),
        headers.get("CF-Access-Client-Secret", ""),
    )
    with _CLIENT_LOCK:
        cached = _CLIENT_CACHE.get(key)
        if cached is not None:
            return cached[0]

        http_client = httpx.Client(timeout=timeout_sec, headers=headers)
        default_headers = {"User-Agent": headers.get("User-Agent", "")}
        if "CF-Access-Client-Id" in headers:
            default_headers["CF-Access-Client-Id"] = headers["CF-Access-Client-Id"]
            default_headers["CF-Access-Client-Secret"] = headers["CF-Access-Client-Secret"]
        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout_sec,
            http_client=http_client,
            default_headers=default_headers,
        )
        _CLIENT_CACHE[key] = (client, http_client)
        return client





SYSTEM_PROMPT = (
    "你是名为 Hermes-Lite 的本地金融分析 Agent。\n"
    "你会接收若干个已安装的 Skill 提示，组合成一个综合分析专家。\n"
    "请严格输出结构化 JSON，不得有任何 JSON 之外的文字。\n"
    "\n"
    "### 必须返回的 JSON 字段（英文 key，中文值）\n"
    "{\n"
    '  "action": "buy" 或 "hold" 或 "sell",\n'
    '  "confidence": 0 到 1 之间的浮点数,\n'
    '  "summary": "必填；15-40 字的一句话结论，点明核心动作与理由，不能为空",\n'
    '  "score": {\n'
    '    "technical": 0 到 100 的整数,\n'
    '    "fundamental": 0 到 100 的整数,\n'
    '    "sentiment": 0 到 100 的整数,\n'
    '    "risk": 0 到 100 的整数\n'
    '  },\n'
    '  "fundamentals": "80 字以内：估值、业绩、行业地位等基本面摘要",\n'
    '  "macro": "80 字以内：相关宏观因素（利率、政策、行业周期）",\n'
    '  "micro": "80 字以内：个股/基金特有信号（资金流、基金经理、持仓集中度等）",\n'
    '  "risks": ["风险点 1", "风险点 2"],\n'
    '  "pros":  ["优势点 1", "优势点 2"],\n'
    '  "advice": "100 字左右的具体操作建议；可以使用 Markdown 语法（**加粗**、列表、> 引用等）；内容分条或分段，包含：仓位、节奏、触发条件、止盈止损",\n'
    '  "time_horizon": "short" 或 "mid" 或 "long",\n'
    '  "target_price": 数字或 null,\n'
    '  "stop_loss": 数字或 null\n'
    "}\n"
    "\n"
    "### 关键约束（违反会导致解析失败）\n"
    "- 整个输出必须是可被 JSON.parse 解析的纯 JSON，不要任何解释、不要 Markdown 代码块围栏。\n"
    "- 禁止在 JSON 里写 // 注释或 /* */ 注释。\n"
    "- 禁止尾随逗号（最后一个元素后面不要加逗号）。\n"
    "- 数值字段必须是裸数字，不要写 \"约 3.5\" / \"3.5 元\" / \"3.5%\" 这种带单位/文字的值；不确定就填 null。\n"
    "- advice 字段的 Markdown 是字符串值的一部分，需要把换行写成 \\n，\" 需要转义成 \\\"，保证 JSON 合法。\n"
    "- summary 字段必须独立成章、不能为空字符串——否则会被视为分析失败。\n"
    "- risks / pros 每项 20 字以内，共 2-4 条。\n"
    "- 若信息不足，target_price / stop_loss 填 null，不要瞎猜。"
)


def build_prompt(
    asset: dict,
    points: list[dict],
    holding: dict,
    skill_prompts: list[str],
    investor_profile_prompt: str = "",
    report_style_prompt: str = "",
) -> list[dict]:
    """组装 messages.

    在 SYSTEM 段里按顺序拼上：
      1) 基础 SYSTEM_PROMPT（JSON 协议）
      2) 投资者性格（若有）——决定判断倾向（稳健/进攻/收息/成长 ...）
      3) 报告风格（若有）——决定用词（专业术语 or 大白话）
    """
    skills_block = "\n\n".join([f"# Skill {i+1}\n{p}" for i, p in enumerate(skill_prompts) if p])

    last_60 = points[-60:] if len(points) > 60 else points
    quotes_text = "\n".join(
        f"{p['date']}  O={p.get('open')}  H={p.get('high')}  L={p.get('low')}  C={p['close']}"
        for p in last_60
    )

    system_parts = [SYSTEM_PROMPT]
    if investor_profile_prompt:
        system_parts.append("### 投资者性格（必须遵守，它会直接影响 action/advice/止盈止损）\n" + investor_profile_prompt)
    if report_style_prompt:
        system_parts.append("### 报告风格（影响 summary/advice/risks/pros 的用词）\n" + report_style_prompt)
    system_msg = "\n\n".join(system_parts)

    user_msg = (
        f"## 标的\n"
        f"- 名称: {asset.get('name')}\n"
        f"- 代码: {asset.get('code')}\n"
        f"- 类型: {asset.get('asset_type')} / 市场: {asset.get('market')}\n"
        f"- 平台: {asset.get('platform')}\n"
        f"- 仅观察: {asset.get('watch_only')}\n\n"
        f"## 持仓\n"
        f"- 持有份额/股: {holding.get('total_shares')}\n"
        f"- 持仓成本: {holding.get('total_cost')}\n"
        f"- 平均成本: {holding.get('avg_cost')}\n"
        f"- 当前价: {holding.get('current_price')}\n"
        f"- 浮动盈亏: {holding.get('profit')}  ({holding.get('profit_pct')}%)\n\n"
        f"## 近 60 个交易日行情（升序）\n{quotes_text}\n\n"
        f"## 已加载的 Skill\n{skills_block}\n\n"
        f"请基于以上 Skill 的视角融合给出最终结论，严格按 SYSTEM 的 JSON 模式输出。"
    )

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def _heuristic(points: list[dict], holding: dict) -> dict:
    """大模型不可用时的回退策略（输出也兼容新的丰富字段）."""
    base = {
        "action": "hold", "confidence": 0.3,
        "summary": "暂无足够行情数据，建议观察。",
        "fundamentals": "", "macro": "", "micro": "",
        "risks": [], "pros": [], "advice": "等待数据或手动触发分析。",
        "time_horizon": "mid",
        "target_price": None, "stop_loss": None,
        "score": {"technical": 50, "fundamental": 50, "sentiment": 50, "risk": 50},
        "detail": "",
    }
    if not points:
        return base

    closes = [p["close"] for p in points if p.get("close") is not None]
    if len(closes) < 5:
        return {**base, "summary": "数据过少，建议观察。"}

    last = closes[-1]
    ma20 = sum(closes[-20:]) / min(len(closes), 20)
    ma5 = sum(closes[-5:]) / 5
    pct30 = (last - closes[-min(30, len(closes))]) / closes[-min(30, len(closes))] * 100

    profit_pct = holding.get("profit_pct") or 0
    if ma5 > ma20 and pct30 > 3 and profit_pct < 20:
        action, conf, tech = "buy", 0.55, 70
    elif ma5 < ma20 and (profit_pct > 15 or pct30 < -8):
        action, conf, tech = "sell", 0.55, 30
    else:
        action, conf, tech = "hold", 0.5, 55

    summary = f"启发式：MA5={ma5:.3f}, MA20={ma20:.3f}, 30D涨跌={pct30:.2f}%。"
    return {
        **base,
        "action": action, "confidence": conf, "summary": summary,
        "score": {"technical": tech, "fundamental": 50, "sentiment": 50, "risk": 55},
        "detail": "（Hermes-Lite 启发式回退结果，未调用大模型。）",
    }


def _strip_json_noise(s: str) -> str:
    """移除 LLM 常见的 JSON 噪声：// 注释、/* */ 注释、尾逗号。"""
    # 去掉行注释 //...（注意不要误杀字符串里的 //，做简单状态机）
    out = []
    i = 0
    n = len(s)
    in_str = False
    escape = False
    while i < n:
        c = s[i]
        if escape:
            out.append(c); escape = False; i += 1; continue
        if c == "\\" and in_str:
            out.append(c); escape = True; i += 1; continue
        if c == '"':
            in_str = not in_str
            out.append(c); i += 1; continue
        if not in_str:
            # 行注释
            if c == "/" and i + 1 < n and s[i + 1] == "/":
                # 吃到行末
                while i < n and s[i] != "\n":
                    i += 1
                continue
            # 块注释
            if c == "/" and i + 1 < n and s[i + 1] == "*":
                i += 2
                while i + 1 < n and not (s[i] == "*" and s[i + 1] == "/"):
                    i += 1
                i += 2
                continue
        out.append(c); i += 1
    cleaned = "".join(out)
    # 去尾逗号： , } 或 , ]
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    return cleaned


def _extract_json_block(text: str) -> str | None:
    """从文本里抽出"最长的合法 JSON 对象"候选。

    支持常见姿势：
    1. 纯 JSON
    2. ```json ... ``` 围栏
    3. 前/后有解释文字的情况
    4. LLM 被截断的情况：尝试从第一个 { 往后找最后一个可能合法的 }
    """
    if not text:
        return None
    t = text.strip()
    # 去 markdown 围栏
    t = re.sub(r"^```(?:json|JSON)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)

    # 先找第一个 {
    start = t.find("{")
    if start < 0:
        return None
    # 从尾部向前找最后一个 }；如果整块 parse 失败，再逐步收缩
    end = t.rfind("}")
    if end < start:
        return None
    return t[start:end + 1]


def _parse_json(text: str) -> dict | None:
    """从大模型返回里抽 JSON。兼容围栏、注释、尾逗号、截断等。"""
    if not text:
        return None
    block = _extract_json_block(text)
    if block is None:
        return None
    # 先尝试直接 parse
    try:
        return json.loads(block)
    except Exception:
        pass
    # 再尝试去噪
    try:
        return json.loads(_strip_json_noise(block))
    except Exception:
        pass
    # 最后尝试：被截断的场景——从 block 末尾向前找最后一个合法的 }
    cleaned = _strip_json_noise(block)
    for i in range(len(cleaned), 0, -1):
        if cleaned[i - 1] != "}":
            continue
        try:
            return json.loads(cleaned[:i])
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Reasoning 模型兼容：不同厂家把"思考过程"和"最终答案"放在不同字段
# - OpenAI 标准：message.content（普通模型的全部内容）
# - DeepSeek-R1 / Qwen3-thinking：message.reasoning_content（思考）+ message.content（答案）
# - o1 / Kimi 某些版本：message.reasoning（思考）+ message.content（答案）
# - 部分 Ollama chat template：在 content 里夹 <think>...</think> 标签
#
# 实际会踩的坑：
# - reasoning 模型思考吃光 max_tokens，finish_reason=length，content 是空字符串，
#   但 reasoning_content / reasoning 字段里反而藏着有用信息（甚至包含最终 JSON）。
# - 有些模型在 reasoning 结尾会再写一次"Final answer:" 带 JSON，也可能根本没写完。
#
# 下面的辅助函数把所有可能的文本源收集起来，由调用方逐一尝试解析。
# ---------------------------------------------------------------------------
_THINK_TAG_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)


def _strip_think_tags(text: str) -> str:
    """移除 <think>...</think> 标签（含内部内容）。某些 Ollama chat template 会这样输出。"""
    if not text:
        return ""
    return _THINK_TAG_RE.sub("", text).strip()


def _collect_candidate_texts(message: Any, finish_reason: str | None = None) -> list[tuple[str, str]]:
    """从一条 ChatMessage 里采集所有可能包含最终答案的文本。

    返回 `[(source_label, text), ...]`，按"最可能是最终答案"的优先级排序。
    调用方应当按顺序对每个候选跑 `_parse_json`，任一成功即可。

    采集的源（按优先级）：
      1) content 的"去 <think> 后版本"（最常见）
      2) content 原文（不去 think，防止把 JSON 误伤掉）
      3) reasoning_content（DeepSeek-R1 / Qwen3 风格）
      4) reasoning（o1 / Kimi 风格；部分 SDK 会把 reasoning 当 dict）
      5) 其他 dict-like dump 里的 str 字段（兜底）
    """
    candidates: list[tuple[str, str]] = []
    if message is None:
        return candidates

    def _push(label: str, text: Any):
        if not text:
            return
        s = str(text).strip()
        if not s:
            return
        # 同一内容不重复（避免 content 和 stripped_content 完全一样时重复尝试）
        for _, existing in candidates:
            if existing == s:
                return
        candidates.append((label, s))

    content = getattr(message, "content", None) or ""

    # 1) 去掉 <think>...</think> 后的 content
    stripped = _strip_think_tags(content) if content else ""
    if stripped and stripped != content:
        _push("content_after_think", stripped)

    # 2) 原始 content
    _push("content", content)

    # 3) reasoning_content（DeepSeek / Qwen3）
    rc = getattr(message, "reasoning_content", None)
    _push("reasoning_content", rc)

    # 4) reasoning（o1 / Kimi；可能是 str 或 list[dict]）
    r = getattr(message, "reasoning", None)
    if isinstance(r, str):
        _push("reasoning", r)
    elif isinstance(r, list):
        # OpenAI o1 风格：list of {"summary": "..."}
        buf: list[str] = []
        for item in r:
            if isinstance(item, dict):
                for v in item.values():
                    if isinstance(v, str):
                        buf.append(v)
            elif isinstance(item, str):
                buf.append(item)
        if buf:
            _push("reasoning_list", "\n".join(buf))
    elif isinstance(r, dict):
        for v in r.values():
            if isinstance(v, str):
                _push("reasoning_dict", v)

    # 5) 兜底：用 model_dump 把所有 str 字段都挖出来（小心重复）
    try:
        dumped = message.model_dump() if hasattr(message, "model_dump") else {}
    except Exception:
        dumped = {}
    if isinstance(dumped, dict):
        for k, v in dumped.items():
            if k in ("content", "reasoning", "reasoning_content", "role", "refusal"):
                continue
            if isinstance(v, str) and "{" in v:
                _push(f"dump_{k}", v)

    return candidates





def _salvage_from_text(text: str) -> dict:
    """JSON 完全解析不出来时的救援：正则抠 summary / advice / action 尽量救回内容。"""
    rescued: dict[str, Any] = {}
    if not text:
        return rescued
    # 尝试从裸文本里用正则抓字段——LLM 就算吐注释或漏逗号，通常字段本身还是完整的
    patterns = {
        "summary": r'"summary"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
        "advice": r'"advice"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
        "action": r'"action"\s*:\s*"([^"]+)"',
        "fundamentals": r'"fundamentals"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
    }
    for k, pat in patterns.items():
        m = re.search(pat, text)
        if m:
            val = m.group(1)
            # 反转义
            val = val.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
            rescued[k] = val
    return rescued


def _coerce_result(parsed: dict, fallback: dict) -> dict:
    """把解析出的 JSON 规整到固定字段，缺失用 fallback 补。"""
    def _str(v, default=""):
        return str(v) if v is not None else default

    def _nonempty_str(v, default=""):
        """比 _str 严格：空串/空白也触发 fallback。"""
        if v is None:
            return default
        s = str(v).strip()
        return s if s else default

    def _list_str(v):
        if isinstance(v, list):
            return [str(x) for x in v if x][:6]
        if isinstance(v, str) and v.strip():
            return [v.strip()]
        return []

    def _num(v, default=None):
        try:
            if v is None or v == "":
                return default
            # 容忍 "3.5元" / "约 3.5" / "3.5%" 这种带单位/文字的值
            if isinstance(v, str):
                m = re.search(r"-?\d+(?:\.\d+)?", v)
                if not m:
                    return default
                return float(m.group(0))
            return float(v)
        except (TypeError, ValueError):
            return default

    score_raw = parsed.get("score") or {}
    score = {
        "technical": int(_num(score_raw.get("technical"), 50) or 50),
        "fundamental": int(_num(score_raw.get("fundamental"), 50) or 50),
        "sentiment": int(_num(score_raw.get("sentiment"), 50) or 50),
        "risk": int(_num(score_raw.get("risk"), 50) or 50),
    }
    for k, v in score.items():
        score[k] = max(0, min(100, v))

    advice_text = _str(parsed.get("advice"), "")
    # summary 兜底策略：parsed.summary → parsed.advice 的前 30 字 → fallback.summary → 固定文案
    summary = _nonempty_str(parsed.get("summary"))
    if not summary:
        if advice_text.strip():
            # 取 advice 第一行/前 30 字，去掉 Markdown 符号
            first_line = advice_text.strip().splitlines()[0]
            first_line = re.sub(r"[#>*`\-\s]+", "", first_line)[:40]
            summary = first_line or fallback.get("summary", "")
        if not summary:
            summary = fallback.get("summary", "") or "分析已完成，详见下方建议。"

    return {
        "action": str(parsed.get("action", "hold")).lower(),
        "confidence": max(0.0, min(1.0, float(_num(parsed.get("confidence"), 0.5) or 0.5))),
        "summary": summary,
        "score": score,
        "fundamentals": _str(parsed.get("fundamentals"), ""),
        "macro": _str(parsed.get("macro"), ""),
        "micro": _str(parsed.get("micro"), ""),
        "risks": _list_str(parsed.get("risks")),
        "pros": _list_str(parsed.get("pros")),
        "advice": advice_text,
        "time_horizon": str(parsed.get("time_horizon") or "mid").lower()[:10],
        "target_price": _num(parsed.get("target_price")),
        "stop_loss": _num(parsed.get("stop_loss")),
    }


def run_agent(
    asset: dict,
    points: list[dict],
    holding: dict,
    skill_prompts: list[str],
    ai_config: dict[str, Any],
    skill_used_label: str = "",
) -> dict:
    base_url = (ai_config or {}).get("base_url") or ""
    api_key = (ai_config or {}).get("api_key") or ""
    model = (ai_config or {}).get("model") or "deepseek-chat"
    temperature = float((ai_config or {}).get("temperature", 0.4))
    # 可配置超时，默认 180s——本地 Ollama 吐丰富 JSON 可能需要较长时间
    try:
        timeout_sec = int((ai_config or {}).get("timeout") or 180)
    except (TypeError, ValueError):
        timeout_sec = 180
    # max_tokens：普通模型 2000 够，但 reasoning/thinking 模型（R1、Qwen3-coding、o1 等）
    # 的 reasoning 本身就会吃掉 3000-8000 token，然后才轮到写 content。
    # 为了兼容这类模型，默认提高到 8192。用户 DB 里如果显式设了较小值则优先。
    # 显式 <=0 视为"完全不传"（让服务端/模型自己决定），但不推荐，容易踩 Ollama 默认 2048 坑。
    try:
        raw_mt = (ai_config or {}).get("max_tokens")
        if raw_mt is None or raw_mt == "":
            max_tokens = 8192
        else:
            max_tokens = int(raw_mt)
    except (TypeError, ValueError):
        max_tokens = 8192

    if not base_url or not api_key:
        result = _heuristic(points, holding)
        detail = result.pop("detail", "")
        return {**result, "detail": detail, "skill_used": skill_used_label or "fallback"}

    # 把投资性格 + 报告风格注入 system prompt
    profile_prompt = get_profile_prompt((ai_config or {}).get("investor_profile"))
    style_prompt = get_report_style_prompt((ai_config or {}).get("report_style"))

    last_err = None
    parsed = None
    text = ""                      # 用于救援/调试展示的"主要文本"——通常是 content
    all_candidates_text = ""       # 用于救援的"所有候选合并文本"
    finish_reason = ""
    successful_source = ""         # 哪个候选解析成功的（content / reasoning_content / ...）
    # 简单重试 2 次，对付 Ollama 偶发超时
    for attempt in range(2):
        try:
            client = _get_openai_client(base_url, api_key, timeout_sec, ai_config)
            messages = build_prompt(
                asset, points, holding, skill_prompts,
                investor_profile_prompt=profile_prompt,
                report_style_prompt=style_prompt,
            )
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            if max_tokens > 0:
                kwargs["max_tokens"] = max_tokens
            resp = client.chat.completions.create(**kwargs)
            choice = resp.choices[0] if resp.choices else None
            msg = choice.message if choice else None
            finish_reason = (getattr(choice, "finish_reason", "") or "") if choice else ""
            text = (getattr(msg, "content", "") or "") if msg else ""

            # 兼容 reasoning 模型：从 content / reasoning_content / reasoning / <think> 等
            # 多种字段挨个尝试解析，谁先 parse 成功用谁。
            candidates = _collect_candidate_texts(msg, finish_reason)
            all_candidates_text = "\n\n---\n\n".join(
                f"[{label}]\n{t}" for label, t in candidates
            )
            for label, candidate_text in candidates:
                p = _parse_json(candidate_text)
                if p:
                    parsed = p
                    successful_source = label
                    # 让"被采纳的那段"作为后续可能展示的 text
                    text = candidate_text
                    break
            if parsed:
                break
            # 没解析出 JSON：触发重试
            last_err = (
                f"JSON 解析失败 (finish_reason={finish_reason}, "
                f"sources_tried={[c[0] for c in candidates] or 'none'})"
            )
        except Exception as e:
            last_err = e
            if attempt == 0:
                print(f"[hermes] attempt {attempt+1} failed: {e}; will retry")
            continue

    fallback = _heuristic(points, holding)
    if not parsed:
        # 救援：在"所有候选文本的拼接"里用正则抠 summary/advice/action 字段。
        # 这样即使 LLM 把 JSON 写到 reasoning 里没来得及复制到 content，也有机会救回来。
        salvage_source = all_candidates_text or text
        salvaged = _salvage_from_text(salvage_source) if salvage_source else {}
        err_str = f"{last_err!r}" if last_err else ""

        # 诊断提示：reasoning 模型被吃光 token 的经典场景
        length_hint = ""
        if finish_reason == "length":
            length_hint = (
                "\n[诊断] finish_reason=length，模型因 token 上限被截断。"
                "若你用的是 reasoning/thinking 模型（如 Qwen3-coding、DeepSeek-R1、o1），"
                "建议把 AI 设置里的 max_tokens 调高到 8192+，或换一个非 reasoning 模型。"
            )

        if salvaged:
            patched = dict(fallback)
            patched.update(salvaged)
            coerced = _coerce_result(patched, fallback)
            detail_parts = [
                "⚠️ 大模型返回未能完整解析为 JSON，已尝试从原文中救援部分字段。",
            ]
            if coerced["advice"]:
                detail_parts.append(f"【建议】\n{coerced['advice']}")
            detail_parts.append(f"[错误] {err_str}{length_hint}")
            detail_parts.append(f"[LLM 原文片段]\n{salvage_source[:800]}")
            return {
                **coerced,
                "detail": "\n\n".join(detail_parts),
                "skill_used": skill_used_label or f"{model}(partial)",
            }
        fallback_detail = fallback.pop("detail", "")
        return {
            **fallback,
            "detail": (fallback_detail + f"\n[调用大模型失败] {err_str}{length_hint}"
                       + (f"\n[LLM 原文片段]\n{salvage_source[:800]}" if salvage_source else "")),
            "skill_used": skill_used_label or "fallback",
        }

    # 成功解析。若来源不是 content 本身，skill_used 里标注一下，方便前端显示"部分解析"
    if successful_source and successful_source not in ("content", "content_after_think"):
        skill_used_effective = skill_used_label or f"{model}({successful_source})"
    else:
        skill_used_effective = skill_used_label or model

    coerced = _coerce_result(parsed, fallback)
    # 生成一段适合折叠展示的 detail（人类可读）
    detail_parts = []
    if coerced["fundamentals"]:
        detail_parts.append(f"【基本面】{coerced['fundamentals']}")
    if coerced["macro"]:
        detail_parts.append(f"【宏观】{coerced['macro']}")
    if coerced["micro"]:
        detail_parts.append(f"【微观】{coerced['micro']}")
    if coerced["pros"]:
        detail_parts.append("【优势】\n- " + "\n- ".join(coerced["pros"]))
    if coerced["risks"]:
        detail_parts.append("【风险】\n- " + "\n- ".join(coerced["risks"]))
    if coerced["advice"]:
        detail_parts.append(f"【建议】{coerced['advice']}")
    if coerced["target_price"] is not None or coerced["stop_loss"] is not None:
        tp = coerced["target_price"]
        sl = coerced["stop_loss"]
        detail_parts.append(
            f"【价位】目标 {tp if tp is not None else '—'} / 止损 {sl if sl is not None else '—'}"
        )
    detail_text = "\n\n".join(detail_parts)

    return {
        **coerced,
        "detail": detail_text,
        "skill_used": skill_used_effective,
    }
