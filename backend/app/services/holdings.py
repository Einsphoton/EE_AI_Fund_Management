"""Holding summary calculation.

口径采用与主流券商 / 基金 App 一致的"成交均价"语义：
- avg_cost      = 持仓成交均价（不含手续费），即 Σ(buy.shares * buy.price) / Σ(buy.shares − sell.shares)
- total_cost    = 持仓本金（不含手续费）= avg_cost * total_shares
- total_fee     = 累计手续费（买入 + 卖出 + 调仓产生的所有费用）
- profit        = 浮动盈亏 = market_value − total_cost  （不扣手续费，与各 App 一致）
- profit_pct    = profit / total_cost
- realized_pnl  = 已实现盈亏（卖出时 (sell_price − avg_cost) * sell.shares − sell.fee）

卖出处理用"加权平均法"：卖出时按当前 avg_cost 抵减成本本金，不影响 avg_cost 本身。
"""
from __future__ import annotations

from .. import models


def summarize(asset: models.Asset, current_price: float | None) -> dict:
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
