"""AI investment manager: turn monthly platform budgets into actionable todos."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from .. import models
from ..agent.hermes import _get_openai_client, _parse_json
from ..agent.profiles import get_profile_prompt, get_profile_public
from ..logging_config import log_ai_event, safe_ai_config
from ..services import ai_guard, holdings as holding_service, quotes as quotes_service, settings_service

from ..tz import now_local


SYSTEM_PROMPT = """
你是专业投资经理。你会收到用户当前资产/标的观察池、投资者性格、各购买平台每月投资预算、当月已使用预算。
请只基于现有资产和“我的标的”观察池给出本次可执行操作，暂时不要推荐数据库外的新标的（下一版本再做）。

核心约束：
1. 投资额度是“月额度”，不是今天必须花完。除非出现极端机会，不要一天花完；也不要长期完全不花。
2. 你需要像投资经理一样控制节奏：通常建议使用剩余额度的 20%-50%，极端机会最多 70%，风险较高时可少用或不用。
3. 必须结合投资者性格调整：稳健型更保守，进攻型可更积极，收息型优先现金流。
4. 对每个平台、币种、允许购买资产类型分别控制预算，不要跨平台/跨币种挪用。
5. 每条预算含 asset_types（fund=场外基金，stock=股票，etf=ETF/场内基金）；buy 动作只能选择该预算允许的资产类型。
6. watch_only=true 表示“我的标的/观察池”，尚未实质持有；可以买入建仓，但不能卖出。
7. 可以建议 buy / sell / hold，但只返回需要用户确认的 buy 或 sell 动作；hold 不要进入 actions。
8. 如果 pending_todos 里已有某个 asset_id 的未处理建议，除非出现非常紧急的极端行情（暴跌低估需要抢筹，或重大风险需要抛售），不要重复推送该资产；确实紧急时必须返回 urgent=true。
9. 每个资产会带 latest_asset_analysis（来自“AI分析我的资产”的最新结论）。你必须优先参考它：高置信 buy/sell 应该进入候选动作或在 reason 中解释为什么暂不执行；不要与高置信资产分析结论无理由相反。
10. 数量必须可执行：给出 asset_id、action、shares、price、amount、platform、currency、reason。
11. 对 buy：amount 不能超过对应平台币种和资产类型预算的 remaining_budget；shares≈amount/price。
12. 对 sell：shares 不能超过当前持仓份额，且不能选择 watch_only=true 的标的。

严格输出纯 JSON，不要 Markdown，不要代码块：
{
  "summary": "本次投资经理总评，80字以内",
  "actions": [
    {
      "asset_id": 123,
      "action": "buy" 或 "sell",
      "platform": "购买平台",
      "currency": "CNY/HKD/USD",
      "amount": 1000.0,
      "shares": 123.4567,
      "price": 1.2345,
      "confidence": 0.0 到 1.0,
      "urgent": false,
      "reason": "为什么这么做，80字以内"
    }
  ]
}
""".strip()


def _currency_for(asset: models.Asset) -> str:
    market = (getattr(asset.market, "value", asset.market) or "").upper()
    if market in ("HK", "HKD"):
        return "HKD"
    if market in ("US", "USD"):
        return "USD"
    return "CNY"


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def expire_pending_todos(db: Session, user_id: int | None = None) -> int:

    """把已过期且未处理的 To-do 自动标记为不采纳。"""
    now = now_local()
    q = db.query(models.TodoItem)
    if user_id is not None:
        q = q.join(models.Asset, models.Asset.id == models.TodoItem.asset_id)
        q = q.filter(models.Asset.user_id == user_id)
    expired = (
        q.filter(models.TodoItem.status == "pending")
        .filter(models.TodoItem.expires_at.isnot(None))
        .filter(models.TodoItem.expires_at <= now)
        .all()
    )

    for todo in expired:
        todo.status = "rejected"
        todo.resolved_at = now
        todo.result = {
            "decision": "reject",
            "reason": "expired",
            "note": "到期未处理，已自动视为不采纳。",
        }
    if expired:
        db.commit()
    return len(expired)


def _budget_items(db: Session, user_id: int | None = None) -> list[dict[str, Any]]:
    cfg = settings_service.get(db, "investment_budget", user_id=user_id) or {}

    raw = cfg.get("items") if isinstance(cfg, dict) else []
    items: list[dict[str, Any]] = []
    for it in raw or []:
        if not isinstance(it, dict):
            continue
        platform = str(it.get("platform") or "").strip()
        currency = str(it.get("currency") or "CNY").strip().upper()
        try:
            monthly_amount = float(it.get("monthly_amount") or 0)
        except (TypeError, ValueError):
            monthly_amount = 0.0
        raw_types = it.get("asset_types") or ["fund", "stock", "etf"]
        asset_types = [
            str(t).strip().lower()
            for t in raw_types
            if str(t).strip().lower() in ("fund", "stock", "etf")
        ] if isinstance(raw_types, list) else []
        if platform and currency and monthly_amount > 0 and asset_types:
            items.append({
                "platform": platform,
                "currency": currency,
                "monthly_amount": monthly_amount,
                "asset_types": asset_types,
            })
    return items


_AI_INVESTMENT_ACCEPTED_PREFIX = "AI投资建议采纳"
_AI_INVESTMENT_LEGACY_MARKER = "AI投资经理建议"


def _budget_usage_reset_at(db: Session, user_id: int | None = None) -> datetime | None:
    cfg = settings_service.get(db, "investment_budget", user_id=user_id) or {}
    raw = cfg.get("usage_reset_at") if isinstance(cfg, dict) else None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw.strip())
        except ValueError:
            return None
    return None


def clear_budget_usage(db: Session, user_id: int | None = None) -> dict[str, Any]:
    now = now_local()
    cfg = settings_service.get(db, "investment_budget", user_id=user_id) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["usage_reset_at"] = now.isoformat()
    settings_service.set_value(db, "investment_budget", cfg, user_id=user_id)
    return {"ok": True, "usage_reset_at": cfg["usage_reset_at"]}


def _is_ai_investment_budget_txn(txn: models.Transaction) -> bool:
    note = str(txn.note or "")
    return note.startswith(_AI_INVESTMENT_ACCEPTED_PREFIX) or _AI_INVESTMENT_LEGACY_MARKER in note


def _spent_this_month(db: Session, budgets: list[dict[str, Any]], user_id: int | None = None) -> dict[tuple[str, str, str], float]:

    start = _month_start(now_local())
    reset_at = _budget_usage_reset_at(db, user_id=user_id)
    if reset_at is not None and reset_at > start:
        start = reset_at

    keys = {
        (b["platform"], b["currency"], asset_type)
        for b in budgets
        for asset_type in b.get("asset_types", [])
    }
    spent = {k: 0.0 for k in keys}
    q = (
        db.query(models.Transaction)
        .join(models.Asset, models.Asset.id == models.Transaction.asset_id)
        .filter(models.Transaction.trade_date >= start)
        .filter(models.Transaction.txn_type == models.TxnType.buy)
    )
    if user_id is not None:
        q = q.filter(models.Asset.user_id == user_id)
    txns = q.all()

    for t in txns:
        if not _is_ai_investment_budget_txn(t):
            continue
        asset = t.asset
        if not asset:
            continue
        asset_type = getattr(asset.asset_type, "value", asset.asset_type)

        key = (asset.platform or "", _currency_for(asset), str(asset_type))
        if key not in spent:
            continue
        amount = float(t.amount or ((t.shares or 0) * (t.price or 0)) or 0) + float(t.fee or 0)
        spent[key] += amount
    return spent


async def _portfolio_rows(db: Session, user_id: int | None = None) -> list[dict[str, Any]]:
    q = db.query(models.Asset)
    if user_id is not None:
        q = q.filter(models.Asset.user_id == user_id)
    assets = q.all()
    quote_sources = settings_service.get(db, "quote_sources", user_id=user_id) or {}


    async def _price(a: models.Asset) -> float | None:
        try:
            return await quotes_service.fetch_current_price_cached(
                a.asset_type.value, a.market.value, a.code,
                quote_sources=quote_sources,
            )

        except Exception:
            return None

    prices = await asyncio.gather(*[_price(a) for a in assets]) if assets else []
    rows: list[dict[str, Any]] = []
    for a, price in zip(assets, prices):
        h = holding_service.summarize(a, price)
        latest_advice = (
            db.query(models.Advice)
            .filter(models.Advice.asset_id == a.id)
            .order_by(models.Advice.created_at.desc())
            .first()
        )
        latest_analysis = None
        if latest_advice is not None:
            latest_analysis = {
                "advice_id": latest_advice.id,
                "source": latest_advice.source,
                "action": latest_advice.action,
                "confidence": latest_advice.confidence,
                "summary": latest_advice.summary,
                "created_at": latest_advice.created_at.isoformat() if latest_advice.created_at else None,
            }
        rows.append({
            "asset_id": a.id,
            "name": a.name,
            "code": a.code,
            "asset_type": a.asset_type.value,
            "market": a.market.value,
            "platform": a.platform,
            "currency": _currency_for(a),
            "watch_only": a.watch_only,
            "shares": h.get("total_shares"),
            "avg_cost": h.get("avg_cost"),
            "current_price": h.get("current_price") or price,
            "market_value": h.get("market_value"),
            "profit_pct": h.get("profit_pct"),
            "latest_asset_analysis": latest_analysis,
        })
    return rows


def _high_confidence_analysis_actions(rows: list[dict[str, Any]], budget_status: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 AI 资产分析里的高置信 buy/sell 转成投资经理候选动作，保证两套 AI 联动。"""
    actions: list[dict[str, Any]] = []
    for r in rows:
        latest = r.get("latest_asset_analysis") or {}
        action = str(latest.get("action") or "").lower()
        confidence = float(latest.get("confidence") or 0)
        price = float(r.get("current_price") or 0)
        if action not in {"buy", "sell"} or confidence < 0.65 or price <= 0:
            continue
        if action == "sell":
            held_shares = float(r.get("shares") or 0)
            if r.get("watch_only") or held_shares <= 0 or confidence < 0.7:
                continue
            shares = round(held_shares * (0.5 if confidence < 0.85 else 0.8), 4)
            if shares > 0:
                actions.append({
                    "asset_id": r["asset_id"],
                    "action": "sell",
                    "platform": r.get("platform") or "",
                    "currency": r.get("currency") or "CNY",
                    "asset_type": r.get("asset_type"),
                    "amount": round(shares * price, 2),
                    "shares": shares,
                    "price": price,
                    "confidence": confidence,
                    "urgent": confidence >= 0.8,
                    "reason": "联动资产分析：最新 AI 资产分析给出高置信卖出/减仓，生成待确认减仓动作。",
                })
            continue

        matched_budget = next(
            (
                b for b in budget_status
                if b.get("platform") == r.get("platform")
                and b.get("currency") == r.get("currency")
                and r.get("asset_type") in (b.get("asset_types") or [])
            ),
            None,
        )
        remaining = float((matched_budget or {}).get("remaining_budget") or 0)
        if remaining <= 0:
            continue
        amount = round(min(remaining * (0.6 if confidence >= 0.8 else 0.4), remaining), 2)
        shares = round(amount / price, 4)
        if shares > 0:
            actions.append({
                "asset_id": r["asset_id"],
                "action": "buy",
                "platform": r.get("platform") or "",
                "currency": r.get("currency") or "CNY",
                "asset_type": r.get("asset_type"),
                "amount": amount,
                "shares": shares,
                "price": price,
                "confidence": confidence,
                "urgent": confidence >= 0.8,
                "reason": "联动资产分析：最新 AI 资产分析给出高置信买入，按预算节奏生成待确认动作。",
            })
    return actions[:8]


def _fallback_actions(rows: list[dict[str, Any]], budget_status: list[dict[str, Any]]) -> dict[str, Any]:
    actions: list[dict[str, Any]] = _high_confidence_analysis_actions(rows, budget_status)
    seen = {(a.get("asset_id"), a.get("action")) for a in actions}

    for b in budget_status:
        remaining = float(b.get("remaining_budget") or 0)
        if remaining <= 0:
            continue
        allowed_types = set(b.get("asset_types") or [])
        candidates = [
            r for r in rows
            if r.get("platform") == b.get("platform")
            and r.get("currency") == b.get("currency")
            and r.get("asset_type") in allowed_types
            and (r.get("current_price") or 0) > 0
        ]
        candidates.sort(key=lambda r: (
            (r.get("latest_asset_analysis") or {}).get("action") != "buy",
            -float((r.get("latest_asset_analysis") or {}).get("confidence") or 0),
            r.get("profit_pct") is None,
            r.get("profit_pct") or 0,
        ))
        if not candidates:
            continue
        target = candidates[0]
        latest = target.get("latest_asset_analysis") or {}
        linked_buy = latest.get("action") == "buy" and float(latest.get("confidence") or 0) >= 0.6
        amount = round(min(remaining * (0.5 if linked_buy else 0.35), remaining), 2)
        price = float(target.get("current_price") or 0)
        shares = round(amount / price, 4) if price > 0 else 0.0
        if shares > 0 and (target["asset_id"], "buy") not in seen:
            confidence = float(latest.get("confidence") or 0.45) if linked_buy else 0.45
            seen.add((target["asset_id"], "buy"))
            actions.append({
                "asset_id": target["asset_id"],
                "action": "buy",
                "platform": b["platform"],
                "currency": b["currency"],
                "asset_type": target.get("asset_type"),
                "amount": amount,
                "shares": shares,
                "price": price,
                "confidence": confidence,
                "urgent": linked_buy and confidence >= 0.8,
                "reason": "联动资产分析：最新 AI 资产分析支持买入，按预算节奏生成待确认动作。" if linked_buy else "AI 不可用时的保守兜底：使用约三成本月剩余额度，优先补低位持仓。",
            })
    return {"summary": "AI 暂不可用，已按资产分析联动与保守规则生成少量待确认动作。", "actions": actions[:5]}



def get_budget_status(db: Session, user_id: int | None = None) -> list[dict[str, Any]]:
    """返回每个平台/币种/资产类型预算的本月已用与剩余。"""
    budgets = _budget_items(db, user_id=user_id)
    spent = _spent_this_month(db, budgets, user_id=user_id)

    budget_status: list[dict[str, Any]] = []
    for b in budgets:
        used = round(sum(
            spent.get((b["platform"], b["currency"], asset_type), 0.0)
            for asset_type in b.get("asset_types", [])
        ), 2)
        monthly = float(b["monthly_amount"])
        budget_status.append({
            **b,
            "used_this_month": used,
            "remaining_budget": max(0.0, round(monthly - used, 2)),
        })
    return budget_status


async def run_investment_manager(db: Session, user_id: int | None = None) -> dict[str, Any]:
    expire_pending_todos(db, user_id=user_id)
    budgets = _budget_items(db, user_id=user_id)

    if not budgets:
        return {"summary": "尚未配置平台月投资额度，无法生成投资经理建议。", "created": 0, "todos": [], "budget_status": []}

    budget_status = get_budget_status(db, user_id=user_id)

    rows = await _portfolio_rows(db, user_id=user_id)
    pending_q = (
        db.query(models.TodoItem)
        .filter(models.TodoItem.status == "pending")
        .filter(models.TodoItem.asset_id.isnot(None))
    )
    if user_id is not None:
        pending_q = pending_q.join(models.Asset, models.Asset.id == models.TodoItem.asset_id)
        pending_q = pending_q.filter(models.Asset.user_id == user_id)
    pending_todos = pending_q.all()

    pending_asset_ids = {int(t.asset_id) for t in pending_todos if t.asset_id is not None}
    pending_todo_context = [
        {
            "todo_id": t.id,
            "asset_id": t.asset_id,
            "action": t.action,
            "title": t.title,
            "expires_at": t.expires_at.isoformat() if t.expires_at else None,
        }
        for t in pending_todos
    ]
    ai_cfg = settings_service.get(db, "ai", user_id=user_id) or {}

    profile_id = ai_cfg.get("investor_profile")
    profile_meta = get_profile_public(profile_id)
    profile_prompt = get_profile_prompt(profile_id) or get_profile_prompt(profile_meta.get("id"))

    base_url = ai_cfg.get("base_url") or ""
    api_key = ai_cfg.get("api_key") or ""
    model = ai_cfg.get("model") or "deepseek-chat"
    temperature = float(ai_cfg.get("temperature", 0.3) or 0.3)
    try:
        timeout_sec = int(ai_cfg.get("timeout") or 180)
    except (TypeError, ValueError):
        timeout_sec = 180

    parsed: dict[str, Any] | None = None
    if base_url and api_key:
        client = _get_openai_client(base_url, api_key, timeout_sec, ai_cfg)
        log_ai_event(
            "investment_manager",
            "investment_plan_start",
            config=safe_ai_config(ai_cfg),
            asset_count=len(rows),
            pending_todo_count=len(pending_todo_context),
            budget_item_count=len(budget_status),
        )
        user_payload = {

            "today": now_local().date().isoformat(),
            "investor_profile": profile_meta,
            "investor_profile_prompt": profile_prompt,
            "monthly_budget_status": budget_status,
            "assets": rows,
            "pending_todos": pending_todo_context,
            "instruction": "请根据预算、资产现状和 latest_asset_analysis 生成本次需要进入 To-do 的 buy/sell 动作。暂不推荐新标的；pending_todos 中已有未处理建议的资产不要重复推送，除非 urgent=true。高置信资产分析给出的买入/卖出建议需要被优先纳入或说明暂缓原因。",
        }
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        max_tokens = int(ai_cfg.get("max_tokens") or 4096)
        try:
            await ai_guard.acquire_ai_budget(
                "investment_manager",
                ai_cfg,
                key="ai",
                messages=messages,
                max_tokens=max_tokens,
            )
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            text = resp.choices[0].message.content if resp.choices else ""
            parsed = _parse_json(text or "")
            log_ai_event(
                "investment_manager",
                "investment_plan_response",
                model=model,
                text_len=len(text or ""),
                parsed=bool(parsed),
            )
        except Exception as e:
            await ai_guard.penalize_from_exception("investment_manager", ai_cfg, e, key="ai")
            log_ai_event(
                "investment_manager",
                "investment_plan_failed",
                level="error",
                config=safe_ai_config(ai_cfg),
                error_type=type(e).__name__,
                error=str(e),
            )


    result = parsed if isinstance(parsed, dict) else _fallback_actions(rows, budget_status)

    actions = result.get("actions") if isinstance(result, dict) else []
    if not isinstance(actions, list):
        actions = []
    linked_actions = _high_confidence_analysis_actions(rows, budget_status)
    seen_actions = {
        (int(a.get("asset_id")), str(a.get("action") or "").lower())
        for a in actions
        if isinstance(a, dict) and a.get("asset_id") is not None
    }
    for linked in linked_actions:
        key = (int(linked.get("asset_id")), str(linked.get("action") or "").lower())
        if key not in seen_actions:
            actions.append(linked)
            seen_actions.add(key)

    asset_by_id = {r["asset_id"]: r for r in rows}
    created: list[models.TodoItem] = []
    now = now_local()
    for raw in actions[:12]:
        if not isinstance(raw, dict):
            continue
        try:
            asset_id = int(raw.get("asset_id"))
        except (TypeError, ValueError):
            continue
        row = asset_by_id.get(asset_id)
        if not row:
            continue
        urgent = bool(raw.get("urgent"))
        if asset_id in pending_asset_ids and not urgent:
            continue
        action = str(raw.get("action") or "").lower()
        if action not in ("buy", "sell"):
            continue
        price = float(raw.get("price") or row.get("current_price") or 0)
        shares = float(raw.get("shares") or 0)
        if price <= 0 or shares <= 0:
            continue
        if action == "sell":
            if row.get("watch_only"):
                continue
            shares = min(shares, float(row.get("shares") or 0))
            if shares <= 0:
                continue
        amount = round(float(raw.get("amount") or (shares * price)), 2)
        platform = str(raw.get("platform") or row.get("platform") or "")
        currency = str(raw.get("currency") or row.get("currency") or _currency_for(db.get(models.Asset, asset_id))).upper()
        asset_type = str(row.get("asset_type") or "")
        if action == "buy":
            matched_budget = next(
                (
                    b for b in budget_status
                    if b.get("platform") == platform
                    and b.get("currency") == currency
                    and asset_type in (b.get("asset_types") or [])
                ),
                None,
            )
            if matched_budget is None:
                continue
            amount = min(amount, float(matched_budget.get("remaining_budget") or 0))
            if amount <= 0:
                continue
            shares = round(amount / price, 4)
        reason = str(raw.get("reason") or "AI 投资经理建议")[:300]
        confidence = float(raw.get("confidence") or 0.5)
        title_action = "建仓" if action == "buy" and row.get("watch_only") else ("追加投入" if action == "buy" else "卖出/减仓")
        todo = models.TodoItem(
            todo_type="ai_investment",
            status="pending",
            asset_id=asset_id,
            title=f"AI投资经理建议：{title_action} {row.get('name')}",
            description=reason,
            action=action,
            due_date=now,
            expires_at=now + timedelta(days=2),
            payload={
                "source": "ai_investment_manager",
                "summary": result.get("summary", ""),
                "platform": platform,
                "currency": currency,
                "asset_type": asset_type,
                "allowed_asset_types": matched_budget.get("asset_types") if action == "buy" and matched_budget else [],
                "urgent": urgent,
                "confidence": confidence,
                "linked_asset_analysis": row.get("latest_asset_analysis"),
                "transaction": {
                    "txn_type": action,
                    "shares": round(shares, 4),
                    "price": price,
                    "amount": amount,
                    "fee": 0.0,
                    "trade_date": now.isoformat(),
                    "note": f"AI投资经理建议·{title_action}（{currency} {amount:g}）",
                },
            },
            result={},
        )
        db.add(todo)
        created.append(todo)
    db.commit()
    for t in created:
        db.refresh(t)
    return {
        "summary": result.get("summary", ""),
        "created": len(created),
        "todos": created,
        "budget_status": budget_status,
    }
