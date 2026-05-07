"""Quote service: fetch fund NAV / stock K-line from public sources.

Sources used:
- OTC fund (天天基金 EastMoney):
    https://api.fund.eastmoney.com/f10/lsjz?fundCode=xxx&pageIndex=1&pageSize=N
    https://fundgz.1234567.com.cn/js/{code}.js  (real-time estimate)
- A 股 / 场内基金 / ETF (新浪 -> 腾讯回退):
    https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData
    http://web.ifzq.gtimg.cn/appstock/app/fqkline/get  (Tencent, 港股 / 港美股回退)
- 港股 (腾讯):
    http://web.ifzq.gtimg.cn/appstock/app/kline/kline (port hk)
- 美股 (Yahoo Finance):
    https://query1.finance.yahoo.com/v8/finance/chart/{symbol}
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any

import httpx

DEFAULT_TIMEOUT = 15.0
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/",
}


def _to_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------- 基金 (OTC) ----------------
async def fetch_fund_nav(code: str, days: int = 365) -> dict[str, Any]:
    """获取基金历史净值 (天天基金).

    EastMoney 实际单页最多 ~20 条，需要分页拉取。
    自然日 days -> 工作日 ≈ days*0.7；这里用 ceil(days*0.7/20)+1 页保证覆盖。
    """
    headers = {**HEADERS, "Referer": f"https://fundf10.eastmoney.com/jjjz_{code}.html"}
    target_count = max(int(days * 0.72), 30)
    per_page = 20  # EastMoney 强制
    max_pages = min(int(target_count / per_page) + 2, 200)  # 上限 200 页 ≈ 4000 个交易日

    items_all: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=headers) as client:
        for page in range(1, max_pages + 1):
            try:
                resp = await client.get(
                    "https://api.fund.eastmoney.com/f10/lsjz",
                    params={"fundCode": code, "pageIndex": page, "pageSize": per_page},
                )
                data = resp.json()
            except Exception:
                break
            page_items = (data.get("Data") or {}).get("LSJZList") or []
            if not page_items:
                break
            items_all.extend(page_items)
            total_count = (data.get("TotalCount") or 0)
            if len(items_all) >= target_count or len(items_all) >= total_count > 0:
                break

    points: list[dict[str, Any]] = []
    for it in reversed(items_all):
        nav = _to_float(it.get("DWJZ"))
        if nav is None:
            continue
        points.append({
            "date": it.get("FSRQ"),
            "open": nav, "high": nav, "low": nav, "close": nav, "volume": None,
        })
    current = points[-1]["close"] if points else None
    return {"name": "", "points": points, "current_price": current}


async def fetch_fund_realtime(code: str) -> float | None:
    """获取基金实时估值."""
    url = f"https://fundgz.1234567.com.cn/js/{code}.js?rt={int(time.time()*1000)}"
    try:
        async with httpx.AsyncClient(timeout=8.0, headers=HEADERS) as client:
            r = await client.get(url)
        m = re.search(r"jsonpgz\((.*)\)", r.text)
        if not m:
            return None
        obj = json.loads(m.group(1))
        return _to_float(obj.get("gsz") or obj.get("dwjz"))
    except Exception:
        return None


# ---------------- A 股 / 场内基金 / ETF ----------------
def _normalize_cn_symbol(code: str) -> str:
    """6 位代码 -> 加 sh/sz 前缀."""
    code = code.strip().lower()
    if code.startswith(("sh", "sz", "bj")):
        return code
    if code.startswith(("6", "5", "11", "9")):
        return "sh" + code
    if code.startswith(("0", "3", "1", "2")):
        return "sz" + code
    if code.startswith(("4", "8")):
        return "bj" + code
    return code


def _parse_sina_kline_text(text: str) -> list[dict[str, Any]]:
    """新浪有时返回标准 JSON，也可能返回带单引号字段名的 JS。统一兼容。"""
    text = (text or "").strip()
    if not text:
        return []
    # 直接尝试标准 JSON
    try:
        obj = json.loads(text)
        if isinstance(obj, list):
            return obj
        # 错误响应通常是 {"__ERROR":...}
        return []
    except Exception:
        pass
    # 兼容 JS 风格：{day:"...",open:'...'}
    try:
        text2 = re.sub(r"([{,])\s*([a-zA-Z_]+)\s*:", r'\1"\2":', text)
        text2 = text2.replace("'", '"')
        obj = json.loads(text2)
        return obj if isinstance(obj, list) else []
    except Exception:
        return []


async def fetch_cn_stock_kline(code: str, days: int = 365) -> dict[str, Any]:
    symbol = _normalize_cn_symbol(code)
    url = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
    params = {"symbol": symbol, "scale": 240, "ma": "no", "datalen": min(max(days, 30), 1023)}
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=HEADERS) as client:
        r = await client.get(url, params=params)
        text = r.text.strip()
    items = _parse_sina_kline_text(text)
    points: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict) or not it.get("day"):
            continue
        c = _to_float(it.get("close"))
        if c is None:
            continue
        points.append({
            "date": it["day"],
            "open": _to_float(it.get("open")) or c,
            "high": _to_float(it.get("high")) or c,
            "low": _to_float(it.get("low")) or c,
            "close": c,
            "volume": _to_float(it.get("volume")) or 0,
        })
    if not points:
        # 用腾讯做兜底
        return await _fetch_via_tencent(symbol, days)
    current = points[-1]["close"]
    return {"name": symbol.upper(), "points": points, "current_price": current}


# ---------------- 港股（腾讯接口为主，新浪已不可用） ----------------
async def fetch_hk_stock_kline(code: str, days: int = 365) -> dict[str, Any]:
    code = code.strip().lower().lstrip("hk")
    code = code.zfill(5)
    symbol = f"hk{code}"
    return await _fetch_via_tencent(symbol, days)


async def _fetch_via_tencent(symbol: str, days: int = 365) -> dict[str, Any]:
    """使用腾讯财经的统一日 K 接口；适配 sh/sz/hk + 港股 / A 股 / 场内基金 / 美股.

    要点：
    - 必须用 HTTPS（http 会 302）
    - 偶发限流，做最多 3 次重试
    """
    n = min(max(days, 30), 2000)
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{symbol},day,,,{n},qfq"}

    last_err: str | None = None
    rows: list[Any] = []
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=HEADERS, follow_redirects=True) as client:
                r = await client.get(url, params=params)
                data = r.json()
            body = (data or {}).get("data", {}).get(symbol, {}) or {}
            rows = body.get("qfqday") or body.get("day") or []
            if rows:
                break
            last_err = f"empty rows from tencent (attempt {attempt + 1})"
        except Exception as e:
            last_err = str(e)
        await asyncio.sleep(0.4)

    points: list[dict[str, Any]] = []
    for row in rows:
        # 腾讯返回顺序：[date, open, close, high, low, volume, ...]
        if not row or len(row) < 5:
            continue
        c = _to_float(row[2])
        if c is None:
            continue
        points.append({
            "date": row[0],
            "open": _to_float(row[1]) or c,
            "close": c,
            "high": _to_float(row[3]) or c,
            "low": _to_float(row[4]) or c,
            "volume": _to_float(row[5] if len(row) > 5 else 0) or 0,
        })
    current = points[-1]["close"] if points else None
    out: dict[str, Any] = {"name": symbol.upper(), "points": points, "current_price": current}
    if not points and last_err:
        out["error"] = last_err
    return out


# ---------------- 美股 ----------------
async def fetch_us_stock_kline(code: str, days: int = 365) -> dict[str, Any]:
    """美股优先腾讯（兼容国内网络），Yahoo 做兜底."""
    sym = code.strip().upper()
    # 腾讯：依次尝试 NASDAQ (.OQ) / NYSE (.N) / AMEX (.A) / 短格式
    for variant in (f"us{sym}.OQ", f"us{sym}.N", f"us{sym}.A", f"us{sym}"):
        res = await _fetch_via_tencent(variant, days)
        if (res.get("points") or []):
            res["name"] = sym
            return res

    # Yahoo 兜底（部分网络可达）
    period2 = int(time.time())
    period1 = period2 - max(days, 30) * 86400
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    params = {"period1": period1, "period2": period2, "interval": "1d"}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=HEADERS) as client:
            r = await client.get(url, params=params)
            data = r.json()
    except Exception as e:
        return {"name": sym, "points": [], "current_price": None,
                "error": f"tencent empty; yahoo: {e}"}
    points: list[dict[str, Any]] = []
    try:
        result = (((data or {}).get("chart") or {}).get("result") or [None])[0]
        if not result:
            return {"name": sym, "points": [], "current_price": None,
                    "error": (data.get("chart", {}) or {}).get("error", "no data")}
        ts = result.get("timestamp") or []
        q = (result.get("indicators", {}).get("quote") or [{}])[0]
        opens = q.get("open") or []
        highs = q.get("high") or []
        lows  = q.get("low")  or []
        closes = q.get("close") or []
        vols   = q.get("volume") or []
        for i, t in enumerate(ts):
            c = _to_float(closes[i] if i < len(closes) else None)
            if c is None:
                continue
            points.append({
                "date": datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
                "open": _to_float(opens[i] if i < len(opens) else c) or c,
                "high": _to_float(highs[i] if i < len(highs) else c) or c,
                "low":  _to_float(lows[i]  if i < len(lows)  else c) or c,
                "close": c,
                "volume": _to_float(vols[i] if i < len(vols) else 0) or 0,
            })
    except Exception as e:
        return {"name": sym, "points": points, "current_price": None, "error": str(e)}
    current = points[-1]["close"] if points else None
    return {"name": sym, "points": points, "current_price": current}


# ---------------- 入口 ----------------
async def fetch_quote(asset_type: str, market: str, code: str, days: int = 365) -> dict[str, Any]:
    asset_type = (asset_type or "").lower()
    market = (market or "").upper()
    try:
        if asset_type == "fund" and market == "OTC":
            return await fetch_fund_nav(code, days)
        if market == "A":
            return await fetch_cn_stock_kline(code, days)
        if market == "HK":
            return await fetch_hk_stock_kline(code, days)
        if market == "US":
            return await fetch_us_stock_kline(code, days)
        return await fetch_cn_stock_kline(code, days)
    except Exception as e:
        return {"name": "", "points": [], "current_price": None, "error": str(e)}


async def fetch_current_price(asset_type: str, market: str, code: str) -> float | None:
    if asset_type == "fund" and market == "OTC":
        v = await fetch_fund_realtime(code)
        if v is not None:
            return v
    quote = await fetch_quote(asset_type, market, code, days=10)
    return quote.get("current_price")


# ---------------- 轻量内存缓存（默认 30s） ----------------
_PRICE_CACHE: dict[tuple[str, str, str], tuple[float, float | None]] = {}
_PRICE_TTL = 30.0  # 秒


async def fetch_current_price_cached(asset_type: str, market: str, code: str) -> float | None:
    key = (asset_type, market, code)
    now = time.time()
    hit = _PRICE_CACHE.get(key)
    if hit and (now - hit[0]) < _PRICE_TTL:
        return hit[1]
    v = await fetch_current_price(asset_type, market, code)
    _PRICE_CACHE[key] = (now, v)
    return v
