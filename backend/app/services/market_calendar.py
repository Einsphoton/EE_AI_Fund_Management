"""Online market-calendar checks for scheduled AI analysis."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx


_TIMEOUT = 8.0
_CACHE_TTL_SECONDS = 60 * 30
_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

_MARKET_META: dict[str, dict[str, Any]] = {
    "A": {
        "label": "A股",
        "timezone": "Asia/Shanghai",
        "symbols": ["000001.SS", "399001.SZ"],
        "tencent_symbols": ["sh000001", "sz399001"],
    },
    "HK": {
        "label": "港股",
        "timezone": "Asia/Hong_Kong",
        "symbols": ["^HSI", "0700.HK"],
        "tencent_symbols": ["hk00700", "hk09988"],
    },
    "US": {
        "label": "美股",
        "timezone": "America/New_York",
        "symbols": ["^GSPC", "SPY"],
        "tencent_symbols": ["usSPY", "usAAPL"],
    },

}


def normalize_markets(markets: list[str] | tuple[str, ...] | set[str] | None = None) -> list[str]:
    if not markets:
        return ["A", "HK", "US"]
    out: list[str] = []
    for raw in markets:
        m = str(raw or "").strip().upper()
        if m in ("CN", "CNY", "OTC", "SH", "SZ", "XSHG", "XSHE"):
            m = "A"
        elif m in ("HKG", "XHKG"):
            m = "HK"
        elif m in ("USD", "NYSE", "NASDAQ", "XNYS", "XNAS"):
            m = "US"
        if m in _MARKET_META and m not in out:
            out.append(m)
    return out or ["A", "HK", "US"]


def _local_date(market: str, now: datetime | None = None) -> date:
    tz = ZoneInfo(_MARKET_META[market]["timezone"])
    if now is None:
        return datetime.now(tz).date()
    if now.tzinfo is None:
        return now.replace(tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(tz).date()
    return now.astimezone(tz).date()


def _timestamp_at_local_midnight(day: date, market: str) -> int:
    tz = ZoneInfo(_MARKET_META[market]["timezone"])
    return int(datetime(day.year, day.month, day.day, tzinfo=tz).timestamp())


def _ts_matches_day(ts: Any, day: date, market: str) -> bool:
    try:
        tz = ZoneInfo(_MARKET_META[market]["timezone"])
        return datetime.fromtimestamp(float(ts), tz=tz).date() == day
    except Exception:
        return False


async def _query_tencent_kline(symbol: str, market: str, day: date) -> dict[str, Any]:
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{symbol},day,,,30,qfq"}
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json() or {}
    body = ((data.get("data") or {}).get(symbol) or {})
    rows = body.get("qfqday") or body.get("day") or []
    wanted = day.isoformat()
    for row in rows:
        if isinstance(row, list) and row and str(row[0]) == wanted:
            return {"is_trading_day": True, "source": "tencent_kline", "symbol": symbol}
    if rows:
        return {"is_trading_day": False, "source": "tencent_kline", "symbol": symbol}
    return {"is_trading_day": None, "source": "tencent_kline", "symbol": symbol, "error": "empty rows"}


async def _query_yahoo_chart(symbol: str, market: str, day: date) -> dict[str, Any]:
    period1 = _timestamp_at_local_midnight(day, market)

    period2 = _timestamp_at_local_midnight(day + timedelta(days=1), market)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
    params = {
        "period1": period1,
        "period2": period2,
        "interval": "1d",
        "includePrePost": "true",
        "events": "history",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json() or {}
    result = ((data.get("chart") or {}).get("result") or [None])[0] or {}

    for ts in result.get("timestamp") or []:
        if _ts_matches_day(ts, day, market):
            return {"is_trading_day": True, "source": "yahoo_chart", "symbol": symbol}

    meta = result.get("meta") or {}
    periods = meta.get("currentTradingPeriod") or {}
    for period in periods.values():
        if not isinstance(period, dict):
            continue
        start = period.get("start")
        end = period.get("end")
        if start and end and (_ts_matches_day(start, day, market) or _ts_matches_day(end, day, market)):
            return {"is_trading_day": True, "source": "yahoo_trading_period", "symbol": symbol}

    # 能拿到有效 result 但没有当天时间戳/交易时段，通常表示当天不开市。
    if result:
        return {"is_trading_day": False, "source": "yahoo_chart", "symbol": symbol}
    return {"is_trading_day": None, "source": "yahoo_chart", "symbol": symbol, "error": "empty result"}


async def is_market_trading_day(market: str, now: datetime | None = None) -> dict[str, Any]:
    market = normalize_markets([market])[0]
    day = _local_date(market, now)
    cache_key = (market, day.isoformat())
    cached = _CACHE.get(cache_key)
    if cached and time.time() - cached[0] < _CACHE_TTL_SECONDS:
        return dict(cached[1])

    meta = _MARKET_META[market]
    errors: list[str] = []
    first_closed: dict[str, Any] | None = None

    checks = [
        *[(symbol, _query_yahoo_chart) for symbol in meta.get("symbols", [])],
        *[(symbol, _query_tencent_kline) for symbol in meta.get("tencent_symbols", [])],
    ]
    for symbol, query in checks:
        try:
            checked = await query(symbol, market, day)
        except Exception as e:
            errors.append(f"{symbol}: {type(e).__name__}: {e}")
            continue
        if checked.get("is_trading_day") is True:
            result = {
                "market": market,
                "label": meta["label"],
                "date": day.isoformat(),
                "is_trading_day": True,
                "source": checked.get("source"),
                "symbol": checked.get("symbol"),
                "errors": errors,
            }
            _CACHE[cache_key] = (time.time(), result)
            return dict(result)
        if checked.get("is_trading_day") is False and first_closed is None:
            first_closed = checked

    result = {
        "market": market,
        "label": meta["label"],
        "date": day.isoformat(),
        "is_trading_day": False,
        "source": (first_closed or {}).get("source") or "online_check_failed",
        "symbol": (first_closed or {}).get("symbol"),
        "errors": errors,
    }

    _CACHE[cache_key] = (time.time(), result)
    return dict(result)


async def check_markets_trading_today(markets: list[str] | tuple[str, ...] | set[str] | None = None) -> dict[str, Any]:
    normalized = normalize_markets(markets)
    statuses = [await is_market_trading_day(market) for market in normalized]
    return {
        "should_run": any(s.get("is_trading_day") for s in statuses),
        "markets": statuses,
    }
