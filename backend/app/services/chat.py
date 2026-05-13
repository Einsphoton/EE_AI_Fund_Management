"""AI Chat over the user's full portfolio.

Provide an OpenAI-compatible chat completion that injects the latest
portfolio snapshot (assets, holdings, recent advices, available skills)
as system context, so the LLM can answer concrete questions with grounding.

上下文策略（"聪明裁剪"）：
- 层级 1：总览（标的数/总成本/总市值/总盈亏） + 已启用 Skill 名称
- 层级 2：标的精简表（name/code/type/盈亏%/市值占比），所有标的都有
- 层级 3：聚焦标的的完整明细（成本、均价、最近建议）——由用户最新消息中提到的代码/名称决定
- 层级 4：对话滚动窗口——保留最近 N 轮 user/assistant

只有当估算 input token 超过阈值时才触发降级（去掉层级 3 的完整建议、再去掉层级 3 的完整明细），
以此保证"常规一问一答几乎等价于全量注入"。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Iterable

from openai import OpenAI
from sqlalchemy.orm import Session

from .. import models
from ..logging_config import log_ai_event, safe_ai_config
from . import quotes as quotes_service
from . import holdings as holding_service
from . import ai_guard, settings_service, skills_service





CHAT_SYSTEM_PROMPT = (
    "你是 Hermes-Lite 资产顾问 Agent，一名严谨克制的二级市场分析师。\n"
    "你会先收到一段 # 上下文 (用户的当前持仓和最近 AI 建议)。\n"
    "回答时请遵守：\n"
    "1) 直接基于上下文给出量化的、可执行的建议（含具体标的代码）。\n"
    "2) 当用户问\"我的资产\"\"我该买/卖什么\"等开放问题时，结合每个标的的成本、市值、盈亏、最近建议给出分析。\n"
    "3) 涉及预测时，先给结论再给依据；列出关键风险。\n"
    "4) 用 Markdown 输出（标题、列表、表格随意），但不要超过 600 字。\n"
    "5) 不要捏造你没看到的标的；若上下文里没有该标的，请说明。\n"
    "6) 在回答末尾加一行斜体免责：*以上仅为模型推理，不构成投资建议。*"
)

# ---------- Token 预算 ----------
# DeepSeek-Chat / 多数 OpenAI 兼容模型：context window ≈ 32k ~ 128k，
# 但 input+output 共享，且 stream=True 时 completion 默认不设限。
# Chat 保留较完整资产上下文；NIM 由全局 AI 预算守卫排队平滑，而不是牺牲上下文能力。
MAX_INPUT_TOKENS = 16000


# 粗略 1 token ≈ 1.7 中文字 / 3.5 英文字节；JSON 场景取中间值 ≈ 2.5 字/ token
CHARS_PER_TOKEN = 2.5
# 对话滚动窗口：保留最近 N 轮（user+assistant 配对计 1 轮）
KEEP_RECENT_ROUNDS = 8


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数。只用于决定是否触发降级，不需要精确。"""
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN) + 1


def _extract_focus_codes(user_msg: str, all_codes: list[str], all_names: list[str]) -> set[str]:
    """从用户最新一条消息中抽取提及的标的代码/名称，返回命中的 code 集合。"""
    if not user_msg:
        return set()
    hit: set[str] = set()
    # 1) 数字代码（基金 6 位、A 股 6 位、港股 5 位、美股 1-5 字母）
    for m in re.findall(r"\b([0-9]{5,6}|[A-Z]{1,5})\b", user_msg.upper()):
        if m in {c.upper() for c in all_codes}:
            # 找回原大小写
            for c in all_codes:
                if c.upper() == m:
                    hit.add(c)
                    break
    # 2) 名称子串匹配（长度 >=2 的名称才考虑，避免单字误命中）
    for name, code in zip(all_names, all_codes):
        if name and len(name) >= 2 and name in user_msg:
            hit.add(code)
    return hit


# ---------- 上下文构建 ----------
async def _collect_portfolio_rows(db: Session, user_id: int | None = None) -> tuple[list[dict[str, Any]], list[models.Asset]]:
    assets_q = db.query(models.Asset)
    if user_id is not None:
        assets_q = assets_q.filter(models.Asset.user_id == user_id)
    assets: Iterable[models.Asset] = assets_q.all()
    assets_list = list(assets)
    quote_sources = settings_service.get(db, "quote_sources", user_id=user_id) or {}


    async def _safe_price(a: models.Asset) -> float | None:
        try:
            return await quotes_service.fetch_current_price_cached(
                a.asset_type.value, a.market.value, a.code,
                quote_sources=quote_sources,
            )

        except Exception:
            return None

    prices = await asyncio.gather(*[_safe_price(a) for a in assets_list]) if assets_list else []

    rows: list[dict[str, Any]] = []
    for a, cur in zip(assets_list, prices):
        h = holding_service.summarize(a, cur)
        rows.append({
            "id": a.id,
            "name": a.name,
            "code": a.code,
            "type": a.asset_type.value,
            "market": a.market.value,
            "platform": a.platform,
            "watch_only": a.watch_only,
            "shares": h["total_shares"],
            "avg_cost": h["avg_cost"],
            "cost_basis": h["total_cost"],
            "current_price": h["current_price"],
            "market_value": h["market_value"],
            "profit": h["profit"],
            "profit_pct": h["profit_pct"],
        })
    return rows, assets_list


def _render_overview(rows: list[dict[str, Any]], skill_lines: list[str]) -> list[str]:
    total_cost = sum((r["cost_basis"] or 0) for r in rows)
    total_value = sum((r["market_value"] or 0) for r in rows if r["market_value"])
    total_profit = total_value - total_cost if total_value else None
    total_pct = (total_profit / total_cost * 100) if total_profit is not None and total_cost > 0 else None

    parts: list[str] = []
    parts.append("# 上下文（用户全部资产快照）\n")
    parts.append("## 总览")
    parts.append(f"- 标的数: {len(rows)}")
    parts.append(f"- 总成本: ¥{total_cost:,.2f}")
    if total_value:
        parts.append(f"- 总市值: ¥{total_value:,.2f}")
    if total_profit is not None and total_pct is not None:
        parts.append(f"- 累计盈亏: ¥{total_profit:,.2f} ({total_pct:.2f}%)")
    if skill_lines:
        parts.append("\n## 已启用 Skill")
        parts.extend(skill_lines)
    return parts


def _render_compact_table(rows: list[dict[str, Any]]) -> list[str]:
    """所有标的的精简一行表（markdown 表格），约 40-60 字/行。"""
    if not rows:
        return []
    total_value = sum((r["market_value"] or 0) for r in rows if r["market_value"]) or 1.0
    parts = ["\n## 标的精简表", "| 代码 | 名称 | 类型 | 盈亏% | 市值占比 |", "|---|---|---|---|---|"]
    for r in rows:
        mv = r["market_value"] or 0
        weight = (mv / total_value * 100) if total_value else 0
        pct = r["profit_pct"]
        pct_s = f"{pct:+.2f}%" if pct is not None else "-"
        parts.append(f"| {r['code']} | {r['name']} | {r['type']} | {pct_s} | {weight:.1f}% |")
    return parts


def _render_full_rows(rows: list[dict[str, Any]], focus_ids: set[int] | None) -> list[str]:
    """聚焦标的的完整 JSON。focus_ids 为 None 时输出全部。"""
    if not rows:
        return []
    subset = rows if focus_ids is None else [r for r in rows if r["id"] in focus_ids]
    if not subset:
        return []
    # 去掉 id（对 LLM 无意义）
    cleaned = [{k: v for k, v in r.items() if k != "id"} for r in subset]
    title = "## 聚焦标的完整明细 (JSON)" if focus_ids is not None else "## 标的完整明细 (JSON)"
    return ["\n" + title, "```json", json.dumps(cleaned, ensure_ascii=False, indent=2), "```"]


def _render_recent_advices(db: Session, focus_ids: set[int] | None, limit: int = 15, user_id: int | None = None) -> list[str]:
    q = db.query(models.Advice)
    if user_id is not None:
        q = q.join(models.Asset, models.Asset.id == models.Advice.asset_id)
        q = q.filter(models.Asset.user_id == user_id)

    if focus_ids:
        q = q.filter(models.Advice.asset_id.in_(list(focus_ids)))
    recent = q.order_by(models.Advice.created_at.desc()).limit(limit).all()
    if not recent:
        return []
    advices = [
        {
            "asset_id": ad.asset_id,
            "action": ad.action,
            "confidence": ad.confidence,
            "summary": (ad.summary or "")[:200],
            "skill_used": ad.skill_used,
            "created_at": ad.created_at.isoformat() if ad.created_at else "",
        }
        for ad in recent
    ]
    title = f"\n## 最近 AI 建议 (聚焦, JSON)" if focus_ids else f"\n## 最近 {len(advices)} 条 AI 建议 (JSON)"
    return [title, "```json", json.dumps(advices, ensure_ascii=False, indent=2), "```"]


async def _build_portfolio_context(
    db: Session,
    user_msg: str,
    token_budget: int,
    user_id: int | None = None,
) -> str:

    """按 token_budget 自适应构建 portfolio 上下文。"""
    rows, assets_list = await _collect_portfolio_rows(db, user_id=user_id)


    skills = db.query(models.Skill).filter_by(enabled=True).all()
    skill_lines = [f"- {s.name} ({s.skill_id}): {s.description}" for s in skills]

    # 抽取 focus
    all_codes = [a.code for a in assets_list]
    all_names = [a.name for a in assets_list]
    focus_codes = _extract_focus_codes(user_msg, all_codes, all_names)
    focus_ids: set[int] = {r["id"] for r in rows if r["code"] in focus_codes} if focus_codes else set()

    # ------- 组装 4 档候选，按优先级从高到低 -------
    # Tier A (必选)：总览 + Skill 列表 + 精简表
    tier_a = _render_overview(rows, skill_lines) + _render_compact_table(rows)

    # Tier B：聚焦标的完整明细（如果有聚焦）
    tier_b = _render_full_rows(rows, focus_ids) if focus_ids else []

    # Tier C：聚焦标的最近建议（如果有聚焦） / 否则全局最近建议
    tier_c = _render_recent_advices(db, focus_ids if focus_ids else None, limit=15, user_id=user_id)


    # Tier D：没有 focus 时的"全量完整明细"——只有预算足够才加
    tier_d = _render_full_rows(rows, None) if not focus_ids else []

    # 按优先级逐层加入，超预算就停
    chosen: list[str] = []
    used = 0
    for tier in (tier_a, tier_b, tier_c, tier_d):
        block = "\n".join(tier)
        cost = _estimate_tokens(block)
        if used + cost > token_budget:
            # 预算不够时，Tier A 无论如何必须保留
            if tier is tier_a:
                chosen.extend(tier)
                used += cost
            # 其他层级直接跳过
            continue
        chosen.extend(tier)
        used += cost

    return "\n".join(chosen)


def _trim_history(history: list[dict[str, str]], max_rounds: int = KEEP_RECENT_ROUNDS) -> list[dict[str, str]]:
    """保留最近 max_rounds 轮对话。最后一条必须是 user。"""
    if len(history) <= max_rounds * 2:
        return history
    # 保留尾部 max_rounds*2 条
    return history[-(max_rounds * 2):]


def _build_llm_headers(base_url: str, ai_cfg: dict[str, Any] | None = None) -> dict[str, str]:
    """构造调用大模型 API 时的 HTTP Headers。

    - 默认附带浏览器风格 UA，规避部分 Cloudflare / WAF 误判。
    - 当 base_url 走受 Cloudflare Zero Trust Access 保护的域名时，自动注入
      Cloudflare Access Service Token（CF-Access-Client-Id / Secret），
      通过 Access 策略的"Service Auth"动作放行。
    - 凭据读取优先级：设置页里保存的 ai_cfg 配置 > 环境变量（兜底）。
    """
    import os

    headers: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }

    ai_cfg = ai_cfg or {}
    # 1) 优先从数据库里的设置读（网页设置页）
    cf_client_id = str(ai_cfg.get("cf_access_client_id") or "").strip()
    cf_client_secret = str(ai_cfg.get("cf_access_client_secret") or "").strip()
    cf_hosts_raw = str(ai_cfg.get("cf_access_hosts") or "").strip()

    # 2) 兜底：环境变量（兼容旧的 .env / docker-compose 用法）
    if not cf_client_id:
        cf_client_id = os.getenv("CF_ACCESS_CLIENT_ID", "").strip()
    if not cf_client_secret:
        cf_client_secret = os.getenv("CF_ACCESS_CLIENT_SECRET", "").strip()
    if not cf_hosts_raw:
        cf_hosts_raw = os.getenv("CF_ACCESS_HOSTS", "").strip()

    if cf_client_id and cf_client_secret and base_url:
        low_url = base_url.lower()
        # 未配置 hosts 白名单时，只要 Client Id/Secret 有效就注入（用户显式开了就对所有请求生效）
        if cf_hosts_raw:
            cf_hosts = [h.strip().lower() for h in cf_hosts_raw.split(",") if h.strip()]
            hit = any(h in low_url for h in cf_hosts)
        else:
            hit = True
        if hit:
            headers["CF-Access-Client-Id"] = cf_client_id
            headers["CF-Access-Client-Secret"] = cf_client_secret

    return headers


def _llm_client(ai_cfg: dict[str, Any]) -> OpenAI | None:
    base_url = (ai_cfg or {}).get("base_url") or ""
    api_key = (ai_cfg or {}).get("api_key") or ""
    if not base_url or not api_key:
        return None
    import httpx as _httpx
    headers = _build_llm_headers(base_url, ai_cfg)
    has_cf = "CF-Access-Client-Id" in headers
    log_ai_event(
        "chat",
        "client_created",
        config=safe_ai_config(ai_cfg),
        cf_header_injected=has_cf,
    )

    try:
        timeout_sec = float((ai_cfg or {}).get("timeout") or 120)
    except (TypeError, ValueError):
        timeout_sec = 120.0
    http_client = _httpx.Client(timeout=timeout_sec, headers=headers)

    # OpenAI SDK 会在更下层把 User-Agent 覆盖成 "OpenAI/Python x.x.x"，
    # 这个 UA 会被 Cloudflare 的 "Block AI Scrapers and Crawlers" 精准识别并 block。
    # 通过 default_headers 强制覆盖回浏览器风格 UA，作为 CF 规则的兜底。
    default_headers = {
        "User-Agent": headers.get("User-Agent", ""),
    }
    if "CF-Access-Client-Id" in headers:
        default_headers["CF-Access-Client-Id"] = headers["CF-Access-Client-Id"]
        default_headers["CF-Access-Client-Secret"] = headers["CF-Access-Client-Secret"]
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=timeout_sec,
        http_client=http_client,
        default_headers=default_headers,
        max_retries=0,
    )



async def stream_chat(db: Session, history: list[dict[str, str]], user_id: int | None = None):

    """Async generator yielding text deltas (SSE-style).

    history 形如 [{"role":"user|assistant","content":"..."}]
    最新一条必须是 user.
    """
    ai_cfg = settings_service.get(db, "ai", user_id=user_id) or {}

    client = _llm_client(ai_cfg)
    if client is None:
        yield (
            "⚠️ 尚未配置大模型 API。请到「设置」页填写 Base URL + API Key + Model。\n\n"
            "你也可以使用本地 Ollama / LM Studio（OpenAI 兼容协议）。"
        )
        return

    # --- 1) 对话滚动窗口 ---
    history = _trim_history(history)
    latest_user = ""
    for m in reversed(history):
        if m.get("role") == "user":
            latest_user = m.get("content") or ""
            break

    # --- 2) 计算给 portfolio 上下文的预算 ---
    sys_prompt_tokens = _estimate_tokens(CHAT_SYSTEM_PROMPT)
    history_tokens = _estimate_tokens("\n".join(m.get("content", "") for m in history))

    # 预留 4k 给模型输出 + 1k 给 skill prompts
    portfolio_budget = MAX_INPUT_TOKENS - sys_prompt_tokens - history_tokens - 4000 - 1000
    portfolio_budget = max(portfolio_budget, 2000)  # 至少给 2k，不然连精简表都放不下

    # --- 3) 构建 portfolio 上下文 + Skill prompts ---
    portfolio_ctx = await _build_portfolio_context(db, latest_user, portfolio_budget, user_id=user_id)


    skill_prompts: list[str] = []
    for s in db.query(models.Skill).filter_by(enabled=True).all():
        p = skills_service.get_skill_prompt(s.skill_id)
        if p:
            skill_prompts.append(f"## Skill: {s.name}\n{p}")
    skills_block = "\n\n".join(skill_prompts) if skill_prompts else ""

    messages: list[dict[str, str]] = [
        {"role": "system", "content": CHAT_SYSTEM_PROMPT},
        {"role": "system", "content": portfolio_ctx},
    ]
    if skills_block:
        messages.append({"role": "system", "content": "# 已加载的 Skill 提示\n" + skills_block})
    messages.extend(history)

    model = ai_cfg.get("model") or "deepseek-chat"
    temperature = float(ai_cfg.get("temperature", 0.4))
    try:
        raw_max_tokens = int(ai_cfg.get("max_tokens") or 0)
    except (TypeError, ValueError):
        raw_max_tokens = 0
    # Chat 不使用“0=不限”，但保留足够输出预算；由全局预算守卫按 token 成本排队。
    chat_max_tokens = raw_max_tokens if raw_max_tokens > 0 else 4096
    chat_max_tokens = max(512, min(chat_max_tokens, 8192))

    input_chars = sum(len(m.get("content", "")) for m in messages)

    output_chars = 0
    import time as _time
    started_at = _time.perf_counter()

    log_ai_event(
        "chat",
        "chat_stream_start",
        config=safe_ai_config(ai_cfg),
        history_messages=len(history),
        total_messages=len(messages),
        input_chars=input_chars,
        portfolio_context_chars=len(portfolio_ctx),
        skill_prompt_count=len(skill_prompts),
        max_tokens=chat_max_tokens,
    )

    try:
        _ = await ai_guard.acquire_ai_budget(
            "chat",
            ai_cfg,
            key="ai",
            messages=messages,
            max_tokens=chat_max_tokens,
        )
    except asyncio.CancelledError:
        raise


    def _do_stream():
        return client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=chat_max_tokens,
            stream=True,
        )


    try:
        stream = await asyncio.to_thread(_do_stream)
        for chunk in stream:
            try:
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    output_chars += len(content)
                    yield content
            except Exception as chunk_error:
                log_ai_event(
                    "chat",
                    "chat_stream_chunk_parse_error",
                    level="warning",
                    error_type=type(chunk_error).__name__,
                    error=str(chunk_error),
                )
                continue
        log_ai_event(
            "chat",
            "chat_stream_done",
            model=model,
            output_chars=output_chars,
            elapsed_ms=round((_time.perf_counter() - started_at) * 1000, 1),
        )
    except Exception as e:

        # 友好诊断：根据异常类型给出具体的修复建议
        base_url = (ai_cfg or {}).get("base_url") or ""
        msg = str(e) or e.__class__.__name__
        hint = ""
        low = msg.lower()
        if "connection" in low or "timed out" in low or "timeout" in low or "refus" in low or "unreachable" in low:
            hint = (
                "\n\n**网络无法到达大模型 Base URL。常见原因：**\n"
                f"- Base URL 无法访问：当前为 `{base_url}`，请在浏览器或 `curl` 试一下\n"
                "- 本地 Ollama 默认只监听 `127.0.0.1`，要想被其他机器访问必须设置 "
                "`OLLAMA_HOST=0.0.0.0:11434` 后重启 Ollama\n"
                "- macOS / Linux 防火墙未放开 11434 端口\n"
                "- 局域网 IP 写错（Macbook 重连 Wi-Fi 后 IP 可能变化）"
            )
        elif "404" in msg or "not found" in low:
            hint = (
                "\n\n**Endpoint / 模型不存在。**\n"
                f"- 检查 Base URL 路径，Ollama 必须以 `/v1` 结尾：`{base_url}`\n"
                "- 检查 Model 名是否与 `ollama list` 输出**完全一致**（区分大小写、含冒号 tag）"
            )
        elif "401" in msg or "403" in msg or "unauthor" in low or "api key" in low:
            hint = (
                "\n\n**认证失败。**\n"
                "- DeepSeek/OpenAI：检查 API Key 是否正确、是否欠费\n"
                "- Ollama 不校验 Key，但前端要求非空，随便填都能通"
            )
        elif "429" in msg or "too many" in low or type(e).__name__ == "RateLimitError":
            hint = (
                "\n\n**模型服务限流。**\n"
                "- 当前服务商返回 429 Too Many Requests，通常是 RPM/TPM/并发额度不足\n"
                "- 已自动把后续 AI 请求纳入限速等待；建议稍等 1-5 分钟再试\n"
                "- 若使用 NVIDIA NIM，建议把 AI 设置里的 RPM 调到 10-20，并发调到 1"
            )
            try:
                await ai_guard.penalize_from_exception("chat", ai_cfg, e, key="ai")
            except Exception:
                pass

        log_ai_event(

            "chat",
            "chat_stream_failed",
            level="error",
            config=safe_ai_config(ai_cfg),
            error_type=type(e).__name__,
            error=msg,
            elapsed_ms=round((_time.perf_counter() - started_at) * 1000, 1),
        )
        yield f"\n\n[调用大模型失败] {msg}{hint}"

