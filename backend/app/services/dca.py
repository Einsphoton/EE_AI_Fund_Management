"""Fund DCA (Dollar-Cost Averaging) advisor.

国内主流基金定投策略 = 普通定投 + 智能定投（基于估值/价格偏离）的混合规则。
本模块结合实时净值与近 1 年历史净值，自动给出本期建议买入金额，并解释原因。

规则（参考蚂蚁财富/天天基金"慧定投"思路）:

1. **基础金额** base：用户在标的上配置（默认 1000 元）。
2. **价格因子** price_factor：基于"当前价相对于过去 N 天均线（默认 250D / 1Y）"的偏离度
     deviation = (last - ma_n) / ma_n
   - 跌得越多越多投：    deviation < -0.10 → factor 1.5
                     -0.10..-0.05 → 1.3
                     -0.05..-0.02 → 1.15
                     -0.02..+0.02 → 1.0
                     +0.02..+0.05 → 0.85
                     +0.05..+0.10 → 0.7
                     > +0.10       → 0.5
3. **趋势过滤** trend_factor：MA20 > MA60 时 ×1.0；反转下行时 ×0.9（避免左侧大幅追跌）
4. **最终建议金额** = round(base * price_factor * trend_factor / 10) * 10

输出还包括：
   - 建议份额 (基于净值估算)
   - 估算手续费（默认 0.1% 申购费，可配置）
   - 触发原因 (人类可读)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from . import quotes as quotes_service


DEFAULT_BASE_AMOUNT = 1000.0
DEFAULT_FEE_RATE = 0.001  # 0.1%


@dataclass
class DcaSuggestion:
    base_amount: float
    suggest_amount: float
    suggest_shares: float
    estimated_fee: float
    last_price: float | None
    ma20: float | None
    ma60: float | None
    ma250: float | None
    deviation: float | None         # (last - ma250) / ma250
    price_factor: float
    trend_factor: float
    decision: str                   # "buy_more" | "buy_normal" | "buy_less" | "skip"
    reason: str                     # 自然语言解释


def _ma(closes: list[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def _price_factor(deviation: float | None) -> float:
    if deviation is None:
        return 1.0
    if deviation < -0.10:
        return 1.5
    if deviation < -0.05:
        return 1.3
    if deviation < -0.02:
        return 1.15
    if deviation <= 0.02:
        return 1.0
    if deviation <= 0.05:
        return 0.85
    if deviation <= 0.10:
        return 0.7
    return 0.5


def _trend_factor(ma20: float | None, ma60: float | None) -> float:
    if ma20 is None or ma60 is None:
        return 1.0
    return 1.0 if ma20 >= ma60 else 0.9


def _decision_from(price_factor: float, trend_factor: float) -> str:
    coef = price_factor * trend_factor
    if coef >= 1.25:
        return "buy_more"
    if coef >= 1.0:
        return "buy_normal"
    if coef >= 0.75:
        return "buy_less"
    return "skip"


def _build_reason(
    last_price: float | None,
    ma250: float | None,
    deviation: float | None,
    ma20: float | None,
    ma60: float | None,
    decision: str,
    suggest: float,
) -> str:
    parts: list[str] = []
    if last_price is not None and ma250 is not None and deviation is not None:
        d_pct = deviation * 100
        cmp_word = "低于" if d_pct < 0 else "高于"
        parts.append(f"当前净值 {last_price:.4f} {cmp_word} 1 年均线（{ma250:.4f}）{abs(d_pct):.2f}%")
    if ma20 is not None and ma60 is not None:
        parts.append(f"MA20={ma20:.4f}, MA60={ma60:.4f}, "
                     f"短期趋势{'走强' if ma20 >= ma60 else '走弱'}")
    if decision == "buy_more":
        parts.append("→ 处于较深回调区间，建议加大本期投入")
    elif decision == "buy_normal":
        parts.append("→ 估值适中，按基础金额定投")
    elif decision == "buy_less":
        parts.append("→ 当前位置偏高，建议适当减少本期金额")
    else:
        parts.append("→ 估值偏高且趋势走弱，本期可暂缓投入")
    parts.append(f"建议本期投入 ¥{suggest:,.2f}")
    return "；".join(parts)


async def suggest(
    code: str,
    base_amount: float = DEFAULT_BASE_AMOUNT,
    fee_rate: float = DEFAULT_FEE_RATE,
) -> DcaSuggestion:
    """根据实时净值 + 近 1 年净值序列，给出定投建议."""
    quote = await quotes_service.fetch_quote("fund", "OTC", code, days=380)
    points = quote.get("points") or []
    closes = [p["close"] for p in points if p.get("close") is not None]
    last_price = await quotes_service.fetch_current_price("fund", "OTC", code)
    if last_price is None and closes:
        last_price = closes[-1]

    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)
    ma250 = _ma(closes, 250)
    deviation = ((last_price - ma250) / ma250) if (last_price and ma250) else None

    pf = _price_factor(deviation)
    tf = _trend_factor(ma20, ma60)
    decision = _decision_from(pf, tf)

    if decision == "skip":
        suggest_amount = 0.0
    else:
        raw = base_amount * pf * tf
        # 取 10 元的整数倍，更符合实际下单习惯
        suggest_amount = round(raw / 10) * 10
        if suggest_amount < base_amount * 0.5:
            suggest_amount = round(base_amount * 0.5 / 10) * 10

    estimated_fee = round(suggest_amount * fee_rate, 2)
    suggest_shares = (
        round((suggest_amount - estimated_fee) / last_price, 4)
        if last_price and suggest_amount > 0 else 0.0
    )

    reason = _build_reason(last_price, ma250, deviation, ma20, ma60, decision, suggest_amount)

    return DcaSuggestion(
        base_amount=base_amount,
        suggest_amount=suggest_amount,
        suggest_shares=suggest_shares,
        estimated_fee=estimated_fee,
        last_price=last_price,
        ma20=ma20, ma60=ma60, ma250=ma250,
        deviation=deviation,
        price_factor=pf,
        trend_factor=tf,
        decision=decision,
        reason=reason,
    )


def to_dict(s: DcaSuggestion) -> dict[str, Any]:
    return asdict(s)
