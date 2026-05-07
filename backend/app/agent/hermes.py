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
    '  "action": "buy" | "hold" | "sell",\n'
    '  "confidence": 0-1 的浮点数,\n'
    '  "summary": "30 字以内的一句话结论，点明核心动作与理由",\n'
    '  "score": {\n'
    '    "technical": 0-100,      // 技术面打分（趋势、均线、动量）\n'
    '    "fundamental": 0-100,   // 基本面打分（估值、盈利、行业）\n'
    '    "sentiment": 0-100,     // 情绪/资金面打分\n'
    '    "risk": 0-100           // 风险打分（越高越危险）\n'
    '  },\n'
    '  "fundamentals": "80 字以内：估值、业绩、行业地位等基本面摘要",\n'
    '  "macro": "80 字以内：相关宏观因素（利率、政策、行业周期）",\n'
    '  "micro": "80 字以内：个股/基金特有信号（资金流、基金经理、持仓集中度等）",\n'
    '  "risks": ["风险点 1", "风险点 2"],        // 2-4 条，每条 20 字内\n'
    '  "pros":  ["优势点 1", "优势点 2"],        // 2-4 条，每条 20 字内\n'
    '  "advice": "100 字以内的具体操作建议：仓位、节奏、止盈止损位、触发条件",\n'
    '  "time_horizon": "short" | "mid" | "long",\n'
    '  "target_price": 数字或 null,             // 目标价（若适用）\n'
    '  "stop_loss": 数字或 null                 // 止损位（若适用）\n'
    "}\n"
    "\n"
    "注意：\n"
    "- 若信息不足无法估算 target_price/stop_loss，填 null，不要瞎猜。\n"
    "- summary 要独立成章（不依赖其他字段即可理解）。\n"
    "- 所有数值 confidence/score 必须是 0-1 或 0-100 的数字，不要加引号。\n"
    "- 禁止输出 Markdown 代码块包裹，直接输出裸 JSON。"
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


def _parse_json(text: str) -> dict | None:
    """从大模型返回里抽 JSON。兼容 ```json ... ``` 包裹。"""
    if not text:
        return None
    # 去掉 markdown 围栏
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    # 找第一个 { ... } 块
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _coerce_result(parsed: dict, fallback: dict) -> dict:
    """把解析出的 JSON 规整到固定字段，缺失用 fallback 补。"""
    def _str(v, default=""):
        return str(v) if v is not None else default

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

    return {
        "action": str(parsed.get("action", "hold")).lower(),
        "confidence": max(0.0, min(1.0, float(_num(parsed.get("confidence"), 0.5) or 0.5))),
        "summary": _str(parsed.get("summary"), fallback.get("summary", "")),
        "score": score,
        "fundamentals": _str(parsed.get("fundamentals"), ""),
        "macro": _str(parsed.get("macro"), ""),
        "micro": _str(parsed.get("micro"), ""),
        "risks": _list_str(parsed.get("risks")),
        "pros": _list_str(parsed.get("pros")),
        "advice": _str(parsed.get("advice"), ""),
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
    # max_tokens：结构化 JSON 用 800 已够；<=0 表示不传
    try:
        max_tokens = int((ai_config or {}).get("max_tokens") or 0)
    except (TypeError, ValueError):
        max_tokens = 0

    if not base_url or not api_key:
        result = _heuristic(points, holding)
        detail = result.pop("detail", "")
        return {**result, "detail": detail, "skill_used": skill_used_label or "fallback"}

    # 把投资性格 + 报告风格注入 system prompt
    profile_prompt = get_profile_prompt((ai_config or {}).get("investor_profile"))
    style_prompt = get_report_style_prompt((ai_config or {}).get("report_style"))

    last_err = None
    parsed = None
    text = ""
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
            text = resp.choices[0].message.content or ""
            parsed = _parse_json(text)
            if parsed:
                break
            # JSON 解析失败也算一次失败，触发重试
            last_err = "JSON 解析失败"
        except Exception as e:
            last_err = e
            if attempt == 0:
                print(f"[hermes] attempt {attempt+1} failed: {e}; will retry")
            continue

    fallback = _heuristic(points, holding)
    if not parsed:
        fallback_detail = fallback.pop("detail", "")
        err_str = f"{last_err!r}" if last_err else ""
        return {
            **fallback,
            "detail": (fallback_detail + f"\n[调用大模型失败] {err_str}"
                       + (f"\n[LLM 原文片段]\n{text[:400]}" if text else "")),
            "skill_used": skill_used_label or "fallback",
        }

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
        "skill_used": skill_used_label or model,
    }
