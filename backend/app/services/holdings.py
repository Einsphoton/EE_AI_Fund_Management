"""Holding summary calculation.

口径采用与主流券商 / 基金 App 一致的"成交均价"语义：
- avg_cost      = 持仓成交均价（不含手续费），即 Σ(buy.shares * buy.price) / Σ(buy.shares − sell.shares)
- total_cost    = 持仓本金（不含手续费）= avg_cost * total_shares
- total_fee     = 累计手续费（买入 + 卖出 + 调仓产生的所有费用）
- profit        = 浮动盈亏 = market_value − total_cost  （不扣手续费，与各 App 一致）
- profit_pct    = profit / total_cost
- realized_pnl  = 已实现盈亏（卖出时 (sell_price − avg_cost) * sell.shares − sell.fee）

卖出处理用"加权平均法"：卖出时按当前 avg_cost 抵减成本本金，不影响 avg_cost 本身。

对货基 / 理财 / 现金 / 债券 等"无行情"类型：
- 若 Asset.principal_amount 有值且没有 transactions：直接以 principal_amount 作为本金 + 当前份额；
- 货基按 yield_7d 估算累计收益；理财按 expected_apr × (今日 - start_date) 天数累计；
- 现金不计息（除非 yield_7d 给了）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .. import models
from ..tz import now_local


_NO_TXN_TYPES = {"cash", "wealth", "money_fund", "bond"}


def _summarize_no_quote_asset(asset: models.Asset) -> dict:
    """对货基/理财/现金/债券类资产：以 principal_amount + 年化估算市值与收益。"""
    principal = float(asset.principal_amount or 0.0)
    if principal <= 0:
        # 没填本金，把它当作 0 持仓
        return _empty_summary()

    asset_type = (getattr(asset.asset_type, "value", asset.asset_type) or "").lower()
    start = asset.start_date or asset.created_at or now_local()
    if isinstance(start, str):
        try:
            start = datetime.fromisoformat(start)
        except Exception:
            start = now_local()
    today = now_local()
    days_held = max(0, (today - start).days)

    # 选用对应的年化（百分比，例如 1.85 = 1.85%）
    if asset_type == "money_fund":
        apr_pct = asset.yield_7d or 0.0  # 货基用 7 日年化近似
    elif asset_type in ("wealth", "bond"):
        apr_pct = asset.expected_apr or 0.0
    else:  # cash
        apr_pct = asset.yield_7d or 0.0

    # 累计收益（线性近似，不复利）
    estimated_profit = principal * (apr_pct / 100.0) * (days_held / 365.0)

    # 理财已到期：收益按 (maturity - start) 总天数封顶
    if asset.maturity_date:
        mat = asset.maturity_date
        if isinstance(mat, str):
            try:
                mat = datetime.fromisoformat(mat)
            except Exception:
                mat = None
        if mat and today > mat:
            full_days = max(0, (mat - start).days)
            estimated_profit = principal * (apr_pct / 100.0) * (full_days / 365.0)

    market_value = principal + estimated_profit
    profit_pct = (estimated_profit / principal * 100) if principal > 0 else None

    return {
        "total_shares": round(principal, 2),  # 对这些类型，"shares" 等同于本金金额
        "total_cost": round(principal, 2),
        "avg_cost": 1.0,                       # 单价恒为 1
        "total_fee": 0.0,
        "realized_pnl": 0.0,
        "current_price": 1.0,
        "market_value": round(market_value, 2),
        "profit": round(estimated_profit, 2),
        "profit_pct": round(profit_pct, 2) if profit_pct is not None else None,
        "days_held": days_held,
        "apr_pct": apr_pct,
    }


def _empty_summary() -> dict:
    return {
        "total_shares": 0.0, "total_cost": 0.0, "avg_cost": 0.0,
        "total_fee": 0.0, "realized_pnl": 0.0,
        "current_price": None, "market_value": None,
        "profit": None, "profit_pct": None,
    }


def realized_pnl_events(asset: models.Asset) -> list[dict]:
    """按卖出交易拆出已实现盈亏明细，口径与 summarize() 完全一致。"""
    total_shares = 0.0
    cost_basis = 0.0
    events: list[dict] = []

    txns = sorted(
        asset.transactions,
        key=lambda t: (t.trade_date or asset.created_at, t.id or 0),
    )
    for t in txns:
        shares = t.shares or 0.0
        price = t.price or 0.0
        amount = t.amount or 0.0
        if price <= 0 and shares > 0 and amount > 0:
            price = amount / shares

        if t.txn_type == models.TxnType.buy:
            if shares > 0:
                cost_basis += shares * price
                total_shares += shares
            continue

        sell_shares = min(shares, total_shares) if total_shares > 0 else shares
        if total_shares <= 0 or sell_shares <= 0:
            continue

        avg = cost_basis / total_shares
        realized = (price - avg) * sell_shares - (t.fee or 0.0)
        cost_basis -= avg * sell_shares
        total_shares -= sell_shares
        sell_amount = (amount * sell_shares / shares) if amount > 0 and shares > 0 else (sell_shares * price)
        events.append({

            "transaction_id": t.id,
            "asset_id": asset.id,
            "asset_name": asset.name,
            "asset_code": asset.code,
            "asset_type": asset.asset_type.value,
            "market": asset.market.value,
            "platform": asset.platform or "",
            "operation": "卖出",
            "trade_date": t.trade_date,
            "shares": round(sell_shares, 4),
            "sell_price": round(price, 4),
            "avg_cost": round(avg, 4),
            "sell_amount": round(sell_amount, 2),
            "fee": round(t.fee or 0.0, 2),
            "realized_pnl": round(realized, 2),
            "note": t.note or "",
        })

    return events


def summarize(asset: models.Asset, current_price: float | None) -> dict:
    asset_type = (getattr(asset.asset_type, "value", asset.asset_type) or "").lower()


    # 货基/理财/现金/债券：无 transactions 时直接用 principal_amount 估算
    if asset_type in _NO_TXN_TYPES and not asset.transactions and asset.principal_amount:
        return _summarize_no_quote_asset(asset)

    total_shares = 0.0       # 当前持仓份额
    cost_basis = 0.0         # 当前持仓本金（按成交均价计）
    total_fee = 0.0          # 累计手续费
    realized_pnl = 0.0       # 已实现盈亏

    for t in asset.transactions:
        # 推导单价：份额 + 单价 优先；没有单价但有金额时反推
        shares = t.shares or 0.0
        price = t.price or 0.0
        amount = t.amount or 0.0
        if price <= 0 and shares > 0 and amount > 0:
            price = amount / shares

        if t.txn_type == models.TxnType.buy:
            if shares > 0:
                cost_basis += shares * price
                total_shares += shares
            total_fee += t.fee or 0.0

        else:  # sell
            sell_shares = min(shares, total_shares) if total_shares > 0 else shares
            if total_shares > 0 and sell_shares > 0:
                avg = cost_basis / total_shares
                # 抵减成本本金（按平均成本）
                cost_basis -= avg * sell_shares
                total_shares -= sell_shares
                # 已实现盈亏 = 卖价差 - 卖出费用
                realized_pnl += (price - avg) * sell_shares - (t.fee or 0.0)
            total_fee += t.fee or 0.0

    avg_cost = (cost_basis / total_shares) if total_shares > 0 else 0.0
    # 对无行情类型：current_price 默认 1.0
    if asset_type in _NO_TXN_TYPES and (current_price is None or current_price <= 0):
        current_price = 1.0

    market_value = (current_price * total_shares) if current_price and total_shares > 0 else None
    profit = (market_value - cost_basis) if market_value is not None else None
    profit_pct = (profit / cost_basis * 100) if profit is not None and cost_basis > 0 else None

    return {
        "total_shares": round(total_shares, 4),
        "total_cost": round(cost_basis, 2),       # 持仓本金（不含手续费）
        "avg_cost": round(avg_cost, 4),
        "total_fee": round(total_fee, 2),
        "realized_pnl": round(realized_pnl, 2),
        "current_price": current_price,
        "market_value": round(market_value, 2) if market_value is not None else None,
        "profit": round(profit, 2) if profit is not None else None,
        "profit_pct": round(profit_pct, 2) if profit_pct is not None else None,
    }
