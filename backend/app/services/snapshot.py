"""Real-time snapshot (基本盘 / 关键指标) service.

数据源：腾讯财经 https://qt.gtimg.cn/q={symbol}
返回 GBK 编码的字符串，按 "~" 分割字段。

字段位置（实测）:
通用：[1]=name [2]=code [3]=last [4]=prev_close [5]=open [6]=volume
       [30]=time [31]=change [32]=change_pct [33]=high [34]=low

A 股 (88 fields):
  [37]=成交额(万)  [38]=换手率%  [39]=PE_ttm  [43]=振幅%
  [44]=流通市值(亿)  [45]=总市值(亿)  [46]=PB
  [47]=52w高  [48]=52w低

港股 (78 fields):
  [37]=成交额  [39]=PE  [43]=振幅%
  [44]=流通市值(亿HKD)  [45]=总市值(亿HKD)
  [48]=52w高  [49]=52w低

美股 (71 fields):
  [33]=最高 [34]=最低 [37]=成交额 [38]=换手率%
  [39]=PE  [44]=总市值(亿USD)  [47]=52w高 [48]=52w低
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from . import quotes as quotes_service

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0",
    "Referer": "https://stockapp.finance.qq.com/",
}


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if not v or v == "-":
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _at(parts: list[str], idx: int) -> float | None:
    if 0 <= idx < len(parts):
        return _to_float(parts[idx])
    return None


def _resolve_symbol(asset_type: str, market: str, code: str) -> str | None:
    market = (market or "").upper()
    code = (code or "").strip()
    if not code:
        return None
    if market == "A":
        return quotes_service._normalize_cn_symbol(code)
    if market == "HK":
        c = code.lower().lstrip("hk").zfill(5)
        return f"hk{c}"
    if market == "US":
        return f"us{code.upper()}"
    return None


async def _fetch_qt(symbol: str) -> list[str] | None:
    url = f"https://qt.gtimg.cn/q={symbol}"
    try:
        async with httpx.AsyncClient(timeout=8.0, headers=HEADERS, follow_redirects=True) as client:
            r = await client.get(url)
        r.encoding = "gbk"
        text = r.text or ""
    except Exception:
        return None
    m = re.search(rf'v_{re.escape(symbol)}="([^"]*)"', text)
    if not m:
        return None
    body = m.group(1)
    if not body or body == "1":
        return None
    parts = body.split("~")
    if len(parts) < 10:
        return None
    return parts


async def fetch_snapshot(asset_type: str, market: str, code: str) -> dict[str, Any] | None:
    """返回基本盘字典；不可用时返回 None.

    输出字段（统一）：
      symbol, name, last, prev_close, open, change, change_pct, high, low,
      amount(成交额, 单位见 amount_unit), turnover(换手率%),
      pe_ttm, pb, total_mktcap(亿), circ_mktcap(亿),
      high_52w, low_52w, currency, market_type
    """
    sym = _resolve_symbol(asset_type, market, code)
    if not sym:
        return None
    parts = await _fetch_qt(sym)
    if not parts:
        return None

    market = market.upper()
    name = parts[1] if len(parts) > 1 else ""
    last = _at(parts, 3)
    prev_close = _at(parts, 4)
    open_ = _at(parts, 5)
    high = _at(parts, 33)
    low  = _at(parts, 34)

    snap: dict[str, Any] = {
        "symbol": sym,
        "name": name,
        "last": last,
        "prev_close": prev_close,
        "open": open_,
        "high": high,
        "low":  low,
        "change": _at(parts, 31),
        "change_pct": _at(parts, 32),
        "amount": _at(parts, 37),
        "turnover": None,
        "pe_ttm": None,
        "pb": None,
        "total_mktcap": None,
        "circ_mktcap": None,
        "high_52w": None,
        "low_52w": None,
        "amplitude": _at(parts, 43),
        "currency": "CNY" if market == "A" else "HKD" if market == "HK" else "USD" if market == "US" else "",
        "market": market,
        "amount_unit": "万" if market == "A" else "",
    }

    if market == "A":
        snap.update({
            "turnover": _at(parts, 38),
            "pe_ttm":   _at(parts, 39),
            "circ_mktcap":  _at(parts, 44),
            "total_mktcap": _at(parts, 45),
            "pb":       _at(parts, 46),
            "high_52w": _at(parts, 47),
            "low_52w":  _at(parts, 48),
        })
    elif market == "HK":
        snap.update({
            "pe_ttm":       _at(parts, 39),
            "circ_mktcap":  _at(parts, 44),
            "total_mktcap": _at(parts, 45),
            "high_52w": _at(parts, 48),
            "low_52w":  _at(parts, 49),
        })
    elif market == "US":
        snap.update({
            "turnover":     _at(parts, 38),
            "pe_ttm":       _at(parts, 39),
            "total_mktcap": _at(parts, 44),
            "high_52w":     _at(parts, 48),
            "low_52w":      _at(parts, 49),
        })

    return snap
