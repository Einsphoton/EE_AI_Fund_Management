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
from ..services import holdings as holding_service, quotes as quotes_service, settings_service
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
9. 数量必须可执行：给出 asset_id、action、shares、price、amount、platform、currency、reason。
10. 对 buy：amount 不能超过对应平台币种和资产类型预算的 remaining_budget；shares≈amount/price。
11. 对 sell：shares 不能超过当前持仓份额，且不能选择 watch_only=true 的标的。

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


def _spent_this_month(db: Session, budgets: list[dict[str, Any]], user_id: int | None = None) -> dict[tuple[str, str, str], float]:

    start = _month_start(now_local())
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
        })
    return rows


def _fallback_actions(rows: list[dict[str, Any]], budget_status: list[dict[str, Any]]) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
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
        candidates.sort(key=lambda r: (r.get("profit_pct") is None, r.get("profit_pct") or 0))
        if not candidates:
            continue
        target = candidates[0]
        amount = round(min(remaining * 0.35, remaining), 2)
        price = float(target.get("current_price") or 0)
        shares = round(amount / price, 4) if price > 0 else 0.0
        if shares > 0:
            actions.append({
                "asset_id": target["asset_id"],
                "action": "buy",
                "platform": b["platform"],
                "currency": b["currency"],
                "asset_type": target.get("asset_type"),
                "amount": amount,
                "shares": shares,
                "price": price,
                "confidence": 0.45,
                "reason": "AI 不可用时的保守兜底：仅使用约三成本月剩余额度，优先补低位持仓。",
            })
    return {"summary": "AI 暂不可用，已按保守规则生成少量待确认动作。", "actions": actions[:5]}


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
        user_payload = {
            "today": now_local().date().isoformat(),
            "investor_profile": profile_meta,
            "investor_profile_prompt": profile_prompt,
            "monthly_budget_status": budget_status,
            "assets": rows,
            "pending_todos": pending_todo_context,
            "instruction": "请根据预算和资产现状生成本次需要进入 To-do 的 buy/sell 动作。暂不推荐新标的；pending_todos 中已有未处理建议的资产不要重复推送，除非 urgent=true。",
        }
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                temperature=temperature,
                max_tokens=int(ai_cfg.get("max_tokens") or 4096),
            )
            text = resp.choices[0].message.content if resp.choices else ""
            parsed = _parse_json(text or "")
        except Exception as e:
            print(f"[investment_manager] LLM failed: {type(e).__name__}: {e}")

    result = parsed if isinstance(parsed, dict) else _fallback_actions(rows, budget_status)
    actions = result.get("actions") if isinstance(result, dict) else []
    if not isinstance(actions, list):
        actions = []

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
