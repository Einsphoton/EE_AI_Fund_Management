"""资产字段补全服务（enrichment）。

设计目标：
- 给定一个 Asset，自动把缺失/占位的字段补全（首要是 code，未来可扩展到 platform、market 等）
- **多源并行查码**：天天基金 / 腾讯证券 / 新浪 / 雪球 同时打，先到的高分结果获胜
- LLM 兜底：所有数据源都没结果时，让 LLM 给一个最佳猜测（默认关，避免瞎编）
- 通用接口：单一入口 `enrich_asset()` 负责所有补全策略，路由层不用关心细节

当前支持：
- fund / etf 类型 → 基金名称查代码（4 个源并行：天天基金 + 腾讯 + 新浪 + 雪球）
- stock 类型 → 股票名称/拼音查代码（同样 4 个源，覆盖 A 股 / 港股 / 美股）

调用流程：
    enriched = await enrich_asset(db, asset_id)
    # enriched = {"updated": ["code"], "before": {...}, "after": {...}, "source": "eastmoney"}
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from .. import models
from . import settings_service


# 占位 code 形如 "fund_a1b2c3d4" / "etf_xxxxxxxx" — 由 import_api 创建时生成
_PLACEHOLDER_CODE_RE = re.compile(r"^(fund|stock|etf|money_fund|wealth|cash|bond)_[0-9a-f]{6,}$")


def _is_placeholder_code(code: str) -> bool:
    """判断一个 code 是不是 import_api 自动生成的占位 code（需要补全）。"""
    if not code:
        return True
    return bool(_PLACEHOLDER_CODE_RE.match(code.strip()))


def _is_real_fund_code(code: str) -> bool:
    """6 位纯数字的真基金代码。"""
    return bool(re.fullmatch(r"\d{6}", (code or "").strip()))


def _is_real_stock_code(code: str) -> bool:
    """A股/港股/美股代码（粗校验）。

    - A 股：6 位数字（沪 600/601/603/605/688，深 000/002/300/301）
    - 港股：1-5 位数字（带前导 0 也行，如 00700）
    - 美股：1-5 位字母 ticker（AAPL / NVDA / TSM 等）
    """
    s = (code or "").strip().upper()
    if not s:
        return False
    if re.fullmatch(r"\d{1,6}", s):
        return True
    if re.fullmatch(r"[A-Z]{1,8}", s):
        return True
    if re.fullmatch(r"[A-Z]{1,8}\.[A-Z]+", s):  # BRK.B 之类
        return True
    return False


def _market_exchange_from_source(asset_type: str, code: str, source_market: str = "", source_code: str = "") -> tuple[str, str]:
    """把各数据源里的 sh/sz/hk/us/of 前缀统一成 App market + exchange。"""
    low_market = (source_market or "").strip().lower()
    raw_code = (source_code or code or "").strip().upper()
    if low_market in ("jj", "of", "fund"):
        return "OTC", "OTC"
    if low_market in ("sh", "sse") or raw_code.startswith("SH"):
        return "A", "SH"
    if low_market in ("sz", "szse") or raw_code.startswith("SZ"):
        return "A", "SZ"
    if low_market in ("bj", "bse") or raw_code.startswith("BJ"):
        return "A", "BJ"
    if low_market in ("hk", "hkex") or raw_code.startswith("HK"):
        return "HK", "HK"
    if low_market in ("us", "gb") or raw_code.startswith("GB_"):
        # 腾讯美股后缀常见：AAPL.OQ=NASDAQ，BRK.N=NYSE，部分 .A=AMEX
        if ".OQ" in raw_code or ".O" in raw_code:
            return "US", "NASDAQ"
        if ".N" in raw_code:
            return "US", "NYSE"
        if ".A" in raw_code:
            return "US", "AMEX"
        return "US", "UNKNOWN"
    try:
        from ..agent.portfolio_ocr_harness import infer_market_exchange
        return infer_market_exchange(asset_type, code)
    except Exception:
        if asset_type == "fund":
            return "OTC", "OTC"
        return "A", "UNKNOWN"


# ============================================================
# 通用 HTTP 头（伪装浏览器，绕过部分 API 的 403）
# ============================================================
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

EASTMONEY_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Referer": "https://fund.eastmoney.com/",
}


# ============================================================
# 数据源 1：天天基金 fundsuggest（基金/ETF 名 → 代码）
# ============================================================

async def _eastmoney_search_fund(name: str) -> list[dict]:
    """用基金名（部分即可）调天天基金 fundsuggest API，返回 [{code, name, type, source, asset_type}]。

    注意：fundsuggest 的 API 是 JSONP 格式，需要**指定 callback 参数**才返回数据，
    否则会返回空体。历史上不带 callback 也能拿到结果，但后端风控加强后，缺 callback
    会被 Cloudflare/WAF 直接掐掉，这就是"httpx 看到 200 但 body 为空"的原因。

    响应形如：`var Datas=[{"CODE":"006228",...}];`
    """
    if not name or not name.strip():
        return []
    url = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
    # callback 参数是关键：没这个参数 API 会返回空 body（疑似风控）
    params = {"_": "0", "m": "1", "key": name.strip()[:24], "callback": "jQuery"}
    try:
        async with httpx.AsyncClient(
            timeout=4.0,
            headers=EASTMONEY_HEADERS,
            follow_redirects=True,
        ) as client:
            r = await client.get(url, params=params)
        text = r.text or ""
        if not text.strip():
            return []
        # 响应格式可能是 `jQuery({"Datas":[...]})` 或 `var Datas=[ {...} ];`
        # 先尝试 JSONP 风格
        m = re.search(r"\{.*\}", text, re.DOTALL)
        data_list = None
        if m:
            try:
                j = json.loads(m.group(0))
                data_list = j.get("Datas") if isinstance(j, dict) else None
            except Exception:
                data_list = None
        if data_list is None:
            # 旧风格 `var Datas=[...]`
            m2 = re.search(r"Datas\s*=\s*(\[.*?\])\s*;?", text, re.DOTALL)
            if m2:
                try:
                    data_list = json.loads(m2.group(1))
                except Exception:
                    data_list = None
        if not data_list or not isinstance(data_list, list):
            return []
        out: list[dict] = []
        for it in data_list:
            if not isinstance(it, dict):
                continue
            code = (it.get("CODE") or it.get("FCODE") or "").strip()
            full_name = (it.get("NAME") or it.get("SHORTNAME") or "").strip()
            if _is_real_fund_code(code) and full_name:
                ftype = (it.get("FundBaseInfo") or {}).get("FTYPE", "") or it.get("CATEGORYDESC", "")
                out.append({
                    "code": code,
                    "name": full_name,
                    "type": ftype,
                    "source": "eastmoney",
                    "asset_type": "etf" if "ETF" in str(ftype).upper() or "LOF" in str(ftype).upper() else "fund",
                    "market": "OTC",
                    "exchange": "OTC",
                })
        return out
    except Exception as e:
        print(f"[enrich] eastmoney_search_fund(「{name}」) failed: {e}")
        return []


# ============================================================
# 数据源 2：腾讯证券 smartbox（股票/基金/港股/美股全覆盖）
# ============================================================

async def _tencent_smartbox(name: str) -> list[dict]:
    """腾讯证券智能搜索：股票/基金/港股/美股都能查到，覆盖最广。

    URL: https://smartbox.gtimg.cn/s3/?q=平安银行&t=all
    **实际响应格式**（2024-2026 观察）：
        v_hint="sz~000001~平安银行~payh~GP-A^sh~600000~浦发银行~pfyh~GP-A"

    - 多条之间用 `^` 分隔
    - 同一条内字段用 `~` 分隔：[market, code, name, pinyin, type_flag]
      * market: sz/sh/hk/us/jj
      * type_flag: GP-A/GP-B（股票）、JJ-XXX（基金）、ETF-XXX、HY（行业）等
    - 中文是 \\uXXXX unicode 转义，json.loads 单独反序列化；或直接对字节解码。

    t=all 同时返回 A股/港股/美股/基金。
    """
    if not name or not name.strip():
        return []
    url = "https://smartbox.gtimg.cn/s3/"
    params = {"q": name.strip()[:24], "t": "all"}
    headers = {**EASTMONEY_HEADERS, "Referer": "https://gu.qq.com/"}
    try:
        async with httpx.AsyncClient(timeout=4.0, headers=headers) as client:
            r = await client.get(url, params=params)
        text = r.text or ""
        # v_hint=".....";  可能有也可能没有分号
        m = re.search(r'v_hint\s*=\s*"(.*)"\s*;?\s*$', text, re.DOTALL)
        if not m:
            return []
        body = m.group(1)
        # 把 \uXXXX 反义为真中文（json loads 最稳）
        try:
            body = json.loads(f'"{body}"')
        except Exception:
            pass
        if not body:
            return []

        records = body.split("^")
        out: list[dict] = []
        for rec in records:
            parts = rec.split("~")
            if len(parts) < 3:
                continue
            market = parts[0].strip().lower()
            code_raw = parts[1].strip()
            display_name = parts[2].strip()
            type_flag = (parts[4].strip().upper() if len(parts) > 4 else "")
            if not code_raw or not display_name:
                continue

            if market == "jj":
                asset_type = "fund"
                code = code_raw
                if not _is_real_fund_code(code):
                    continue
            elif market in ("sh", "sz", "bj"):
                # A 股：基金（LOF/ETF）代码也在这个前缀下，靠 type_flag 区分
                code = code_raw
                if type_flag.startswith("ETF") or type_flag.startswith("LOF"):
                    asset_type = "etf"
                elif type_flag.startswith("JJ"):
                    asset_type = "fund"
                else:
                    asset_type = "stock"
            elif market == "hk":
                code = code_raw.lstrip("0").rjust(5, "0") if code_raw.isdigit() else code_raw
                asset_type = "stock"
            elif market == "us":
                # 美股：腾讯会返回带交易所后缀的 ticker（AAPL.OQ / BRK.N / VOO.P 等）
                # 去掉尾缀统一成纯 ticker
                code = code_raw.upper().split(".")[0]
                asset_type = "stock"
            else:
                continue

            market_val, exchange = _market_exchange_from_source(asset_type, code, market, code_raw)
            out.append({
                "code": code,
                "name": display_name,
                "type": type_flag,
                "source": "tencent",
                "asset_type": asset_type,
                "market": market_val,
                "exchange": exchange,
            })
        return out
    except Exception as e:
        print(f"[enrich] tencent_smartbox(「{name}」) failed: {e}")
        return []


# ============================================================
# 数据源 3：新浪财经 suggest_data（股票/基金，老牌且稳定）
# ============================================================

async def _sina_suggest(name: str) -> list[dict]:
    """新浪 suggest API：覆盖 A 股 / 港股 / 美股 / 基金。

    URL: https://suggest3.sinajs.cn/suggest/?type=&key=平安银行&name=suggestdata_
    响应示例（GBK 编码）：
        var suggestdata_="平安银行,11,000001,sz000001,平安银行,,平安银行,99,1,ESG,,;
                          博时颐泽平衡养老目标三年持有混合发起(FOF)A,201,007649,of007649,...;"

    - 变量名取决于 name 参数；这里规范设 suggestdata_
    - 多条用 `;` 分隔，字段用 `,` 分隔
    - 字段顺序：[display_name, type_id, short_code, full_code_with_market, ...]
    - type 字段含义：
        11=A股, 21=B股, 31=美股, 41=港股
        82=开放式基金, 83=ETF, 201=FOF, 14=封闭基金, 13=A股指数
    - 编码为 GBK；httpx 会按 Content-Type charset 自动解码（但要确认 r.text 正确）
    """
    if not name or not name.strip():
        return []
    url = "https://suggest3.sinajs.cn/suggest/"
    # 必须带这个 Referer，否则返回空
    headers = {**EASTMONEY_HEADERS, "Referer": "https://finance.sina.com.cn/"}
    params = {"type": "", "key": name.strip()[:24], "name": "suggestdata_"}
    try:
        async with httpx.AsyncClient(timeout=4.0, headers=headers) as client:
            r = await client.get(url, params=params)
        # 强制按 GBK 解码（有时 httpx 猜错）
        try:
            raw = r.content or b""
            text = raw.decode("gbk", errors="replace")
        except Exception:
            text = r.text or ""
        # 匹配 `var <任意名>="..."` ；用户参数 name=suggestdata_ → 变量名 suggestdata_
        # 但偶尔新浪 ignore 掉 name 参数 → 变量名变成 hq_str_suggest 之类；用通用正则
        m = re.search(r'var\s+\w+\s*=\s*"(.*?)"\s*;?\s*$', text, re.DOTALL)
        if not m:
            return []
        body = m.group(1)
        if not body:
            return []
        out: list[dict] = []
        # 多条用 ; 分隔
        for rec in body.split(";"):
            parts = rec.split(",")
            if len(parts) < 5:
                continue
            display_name = parts[0].strip()
            type_id = parts[1].strip()
            code_with_market = parts[3].strip()  # sz000001 / hk00700 / gb_aapl / of000001
            if not display_name or not code_with_market:
                continue
            low = code_with_market.lower()

            # 基金类：of000001 / f000001
            if low.startswith("of") or low.startswith("f0"):
                code = low[2:] if low.startswith("of") else low[1:]
                if not _is_real_fund_code(code):
                    continue
                asset_type = "etf" if type_id == "83" else "fund"
                src_market = "of"
            elif low.startswith("hk"):
                code = low[2:]
                if code and code.isdigit():
                    code = code.lstrip("0").rjust(5, "0")
                asset_type = "stock"
                src_market = "hk"
            elif low.startswith("gb_"):
                code = code_with_market[3:].upper()
                asset_type = "stock"
                src_market = "gb"
            elif low.startswith("sh") or low.startswith("sz") or low.startswith("bj"):
                src_market = low[:2]
                code = low[2:]
                # 按 type_id 判定：82/83/201 是基金类；83 视为 ETF
                asset_type = "etf" if type_id == "83" else ("fund" if type_id in ("82", "201", "14") else "stock")
            elif type_id in ("31", "41") and re.match(r"^[a-zA-Z]+$", code_with_market):
                # 纯字母代码 + 美股/港股 type_id
                code = code_with_market.upper()
                asset_type = "stock"
                src_market = "us" if type_id == "31" else "hk"
            else:
                continue

            market_val, exchange = _market_exchange_from_source(asset_type, code, src_market, code_with_market)
            out.append({
                "code": code,
                "name": display_name,
                "type": type_id,
                "source": "sina",
                "asset_type": asset_type,
                "market": market_val,
                "exchange": exchange,
            })
        return out
    except Exception as e:
        print(f"[enrich] sina_suggest(「{name}」) failed: {e}")
        return []


# ============================================================
# 数据源 4：雪球 stock/search（覆盖最广，含小众基金/分级 / B 股 / 美股 OTC）
# ============================================================

# 雪球需要先访问首页拿到 xq_a_token cookie；同一进程内缓存 10 分钟
_xueqiu_cookie_cache: dict = {"cookies": None, "expire_at": 0.0}
_xueqiu_cookie_lock = asyncio.Lock()


async def _get_xueqiu_cookies() -> httpx.Cookies | None:
    """首次访问雪球首页拿 anonymous session cookie；缓存 10 分钟复用。

    雪球对无 cookie 的请求直接返回 `{"code":400016,"success":false}`。
    必须预热一次首页才能拿到 `xq_a_token` / `xq_r_token` / `device_id`。
    """
    import time as _t
    async with _xueqiu_cookie_lock:
        now = _t.time()
        if _xueqiu_cookie_cache["cookies"] and now < _xueqiu_cookie_cache["expire_at"]:
            return _xueqiu_cookie_cache["cookies"]
        try:
            async with httpx.AsyncClient(
                timeout=4.0,
                headers={"User-Agent": _BROWSER_UA},
                follow_redirects=True,
            ) as client:
                # 访问首页 —— 返回 Set-Cookie（含 xq_a_token 等）
                r = await client.get("https://xueqiu.com/")
                if r.status_code == 200 and r.cookies:
                    _xueqiu_cookie_cache["cookies"] = r.cookies
                    _xueqiu_cookie_cache["expire_at"] = now + 600
                    return r.cookies
        except Exception as e:
            print(f"[enrich] get xueqiu cookie failed: {e}")
    return None


async def _xueqiu_search(name: str) -> list[dict]:
    """雪球综合搜索 API。覆盖最广——含小众基金/B股/美股 OTC 等。

    URL: https://xueqiu.com/query/v1/suggest_stocks.json?q=平安
    响应：{"stocks":[{"code":"SZ000001","name":"平安银行","exchange":"SZ","type":11},...]}

    关键：必须先从首页拿 session cookie，否则直接 400016 拒绝。
    """
    if not name or not name.strip():
        return []
    cookies = await _get_xueqiu_cookies()
    if cookies is None:
        return []
    url = "https://xueqiu.com/query/v1/suggest_stocks.json"
    params = {"q": name.strip()[:24]}
    headers = {
        **EASTMONEY_HEADERS,
        "Referer": "https://xueqiu.com/",
        "Accept": "application/json, text/plain, */*",
    }
    try:
        async with httpx.AsyncClient(
            timeout=4.0, headers=headers, cookies=cookies, follow_redirects=False,
        ) as client:
            r = await client.get(url, params=params)
            if r.status_code != 200:
                # cookie 可能过期 → 重新拿一次
                _xueqiu_cookie_cache["expire_at"] = 0
                return []
            try:
                data = r.json()
            except Exception:
                return []
        if not data.get("success", True) and data.get("code") in (400016, 400):
            # cookie 过期
            _xueqiu_cookie_cache["expire_at"] = 0
            return []
        out: list[dict] = []
        for it in (data.get("stocks") or []):
            if not isinstance(it, dict):
                continue
            raw_code = (it.get("code") or "").strip()  # SZ000001 / SH600000 / 00700 / AAPL
            display_name = (it.get("name") or "").strip()
            if not raw_code or not display_name:
                continue
            low = raw_code.lower()
            t = it.get("type")
            # 雪球 type：11=A股, 14=封闭基金, 82/83=开放基金/ETF, 201=FOF, 30=港股, 31=美股
            is_fund_type = t in (14, 82, 83, 201)

            if low.startswith("sh") or low.startswith("sz") or low.startswith("bj"):
                src_market = low[:2]
                code = raw_code[2:]
                asset_type = "etf" if t == 83 else ("fund" if is_fund_type else "stock")
            elif raw_code.isdigit():
                # 港股纯数字
                src_market = "hk"
                code = raw_code.lstrip("0").rjust(5, "0")
                asset_type = "stock"
            elif re.match(r"^[A-Z][A-Z0-9\.\-]*$", raw_code):
                # 美股 ticker
                src_market = "us"
                code = raw_code.upper()
                asset_type = "stock"
            else:
                continue
            market_val, exchange = _market_exchange_from_source(asset_type, code, src_market, raw_code)
            out.append({
                "code": code,
                "name": display_name,
                "type": str(t) if t is not None else "",
                "source": "xueqiu",
                "asset_type": asset_type,
                "market": market_val,
                "exchange": exchange,
            })
        return out
    except Exception as e:
        print(f"[enrich] xueqiu_search(「{name}」) failed: {e}")
        return []


# ============================================================
# 名称匹配评分
# ============================================================

def _name_match_score(query: str, candidate: str) -> float:
    """简单的中文名相似度评分。

    规则（叠加）：
    - 完全相等：1.0
    - 一方包含另一方：0.95
    - 字符级 Jaccard 相似度：normalize 后 0..0.9
    """
    if not query or not candidate:
        return 0.0
    q = query.strip()
    c = candidate.strip()
    if q == c:
        return 1.0
    # 去掉常见后缀差异（A/C/E/H / 后端/前端 / 联接 等）影响
    def _norm(s: str) -> str:
        s = re.sub(r"[\sABCDEHabcdeh]+$", "", s)
        s = s.replace("（", "(").replace("）", ")")
        return s
    if _norm(q) == _norm(c):
        return 0.97
    if q in c or c in q:
        return 0.92
    # 字符 Jaccard
    qs, cs = set(q), set(c)
    if not qs or not cs:
        return 0.0
    inter = len(qs & cs)
    union = len(qs | cs)
    return min(0.9, inter / union)


# 各数据源的可信度权重（基金类）：天天基金对公募基金最权威
_FUND_SOURCE_WEIGHT = {
    "eastmoney": 1.00,
    "tencent": 0.92,
    "sina": 0.90,
    "xueqiu": 0.95,
}
# 股票类：腾讯/新浪老牌且稳；雪球次之；天天基金不查股票
_STOCK_SOURCE_WEIGHT = {
    "tencent": 1.00,
    "sina": 0.98,
    "xueqiu": 0.95,
    "eastmoney": 0.0,  # 不参与股票评分
}


async def _enrich_fund_code(name: str) -> dict | None:
    """名字 → 基金代码（**多源并行融合**）。

    返回 {"code": "...", "matched_name": "...", "score": 0..1, "source": "...",
           "alternates": [...]}  或 None（所有源都没找到）。

    并行策略：
    - 同时打 4 个源（eastmoney + tencent + sina + xueqiu），任何一个先回都先用
    - 总硬超时 6s（名称查码不是模型 OCR，适当放宽可减少随机漏码）

    - 跨源结果做加权融合：把每条候选打分 = 名字相似度 × 来源权重
    - 最终从所有候选里选总分最高的；alternates 里也是跨源去重后的
    """
    name = (name or "").strip()
    if not name:
        return None

    # 并发起 4 个源
    sources = [
        ("eastmoney", _eastmoney_search_fund(name)),
        ("tencent",   _tencent_smartbox(name)),
        ("sina",      _sina_suggest(name)),
        ("xueqiu",    _xueqiu_search(name)),
    ]
    # 用 wait + 短超时：让快的源先到，慢的最多等 3s
    results: list[dict] = []
    try:
        gathered = await asyncio.wait_for(
            asyncio.gather(*[s[1] for s in sources], return_exceptions=True),
            timeout=3.0,
        )
        for src_name, items in zip([s[0] for s in sources], gathered):
            if isinstance(items, Exception):
                continue
            for it in (items or []):
                # 保留 fund / etf 类型（基金/ETF 代码通常是 6 位）
                if it.get("asset_type") in ("fund", "etf") and _is_real_fund_code(it.get("code", "")):
                    results.append(it)
    except asyncio.TimeoutError:
        # 整批 3s 超时，用已经回来的结果（如果有的话）—— 走不到这里因为 return_exceptions=True
        return None

    if not results:
        return None

    # 跨源去重：相同 code 只保留分数最高的那条
    seen: dict[str, tuple[float, dict]] = {}
    for it in results:
        code = it["code"]
        score_name = _name_match_score(name, it["name"])
        weight = _FUND_SOURCE_WEIGHT.get(it.get("source", ""), 0.7)
        score = score_name * weight
        if code not in seen or score > seen[code][0]:
            seen[code] = (score, it)

    if not seen:
        return None

    scored = sorted(seen.values(), key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    if best_score < 0.55:
        # 即便经过加权，分数也太低 → 不要瞎填
        return None
    return {
        "code": best["code"],
        "matched_name": best["name"],
        "score": round(best_score, 3),
        "source": best.get("source", "unknown"),
        "asset_type": best.get("asset_type", "fund"),
        "market": best.get("market", "OTC"),
        "exchange": best.get("exchange", "OTC"),
        "alternates": [
            {
                "code": c["code"],
                "name": c["name"],
                "score": round(s, 3),
                "source": c.get("source", "unknown"),
                "asset_type": c.get("asset_type", "fund"),
                "market": c.get("market", "OTC"),
                "exchange": c.get("exchange", "OTC"),
            }
            for s, c in scored[:5] if s >= 0.45
        ],
    }


async def _enrich_stock_code(name: str) -> dict | None:
    """名字 → 股票代码（**多源并行融合**）。

    A 股 / 港股 / 美股都能查；天天基金不参与股票评分。
    返回 shape 同 _enrich_fund_code。
    """
    name = (name or "").strip()
    if not name:
        return None

    sources = [
        ("tencent", _tencent_smartbox(name)),
        ("sina",    _sina_suggest(name)),
        ("xueqiu",  _xueqiu_search(name)),
    ]
    results: list[dict] = []
    try:
        gathered = await asyncio.wait_for(
            asyncio.gather(*[s[1] for s in sources], return_exceptions=True),
            timeout=6.0,
        )

        for src_name, items in zip([s[0] for s in sources], gathered):
            if isinstance(items, Exception):
                continue
            for it in (items or []):
                if it.get("asset_type") == "stock" and _is_real_stock_code(it.get("code", "")):
                    results.append(it)
    except asyncio.TimeoutError:
        return None

    if not results:
        return None

    seen: dict[str, tuple[float, dict]] = {}
    for it in results:
        code = it["code"]
        score_name = _name_match_score(name, it["name"])
        weight = _STOCK_SOURCE_WEIGHT.get(it.get("source", ""), 0.7)
        score = score_name * weight
        if code not in seen or score > seen[code][0]:
            seen[code] = (score, it)

    if not seen:
        return None

    scored = sorted(seen.values(), key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    if best_score < 0.55:
        return None
    return {
        "code": best["code"],
        "matched_name": best["name"],
        "score": round(best_score, 3),
        "source": best.get("source", "unknown"),
        "asset_type": best.get("asset_type", "stock"),
        "market": best.get("market", "A"),
        "exchange": best.get("exchange", "UNKNOWN"),
        "alternates": [
            {
                "code": c["code"],
                "name": c["name"],
                "score": round(s, 3),
                "source": c.get("source", "unknown"),
                "asset_type": c.get("asset_type", "stock"),
                "market": c.get("market", "A"),
                "exchange": c.get("exchange", "UNKNOWN"),
            }
            for s, c in scored[:5] if s >= 0.45
        ],
    }


# ============================================================
# 数据源 2：LLM 兜底（API 没结果时用）
# ============================================================

_LLM_FUND_CODE_PROMPT = (
    "你是中国公募基金信息库。用户给你一个基金/ETF 的中文名（可能不完整、可能有 A/C 后缀差异），"
    "你输出最匹配的官方 6 位代码。\n\n"
    "**严格规则：**\n"
    "1. 只输出**纯 JSON 对象**，不要任何说明、不要 markdown 围栏。\n"
    "2. JSON schema：{\"code\": \"6位数字\" 或 null, \"matched_name\": \"完整官方名\", \"confidence\": 0.0-1.0}\n"
    "3. 不确定就把 code 设为 null、confidence ≤ 0.5；**绝对不要瞎编**。\n"
    "4. 区分 A 类 / C 类 / I 类等不同份额——它们代码不同。\n\n"
    "用户输入：{name}"
)


async def _llm_guess_fund_code(db: Session, name: str) -> dict | None:
    """让现有 ai 配置的 LLM 给一个最佳猜测。返回同 _enrich_fund_code 格式。

    注意：纯文本 LLM（无联网）的回答**置信度有限**，仅作 API 失败兜底。
    """
    ai_cfg = settings_service.get(db, "ai") or {}
    if not ai_cfg.get("base_url") or not ai_cfg.get("model"):
        return None

    prompt = _LLM_FUND_CODE_PROMPT.replace("{name}", name)
    try:
        # 复用 hermes 的 client 缓存（自动处理 CF Access、关 keep-alive 等）
        from ..agent.hermes import _get_openai_client
        timeout_sec = int(ai_cfg.get("timeout") or 60)
        client = _get_openai_client(
            ai_cfg.get("base_url"),
            ai_cfg.get("api_key") or "",
            min(60, timeout_sec),  # 这种小任务不需要 180s 超时
            ai_cfg,
        )

        # 同步调用包到线程里（OpenAI SDK 是同步的）
        # 注意：这里不复用 hermes 的 thinking 参数——查代码是一个简短的 lookup 任务，
        # 给 reasoning/thinking 模型开思考反而容易陷入复读循环（已经发生过）。
        # 直接关 thinking、限制 max_tokens=120（6 位代码 + matched_name + confidence
        # 加 JSON 围栏 也就 80 token 顶天）。
        def _build_kwargs(with_jm: bool) -> dict:
            kw = {
                "model": ai_cfg["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 120,
                # 强制关思考：对应 DeepSeek/Qwen/GLM/豆包等
                "extra_body": {
                    "enable_thinking": False,
                    "thinking": {"type": "disabled"},
                },
            }
            if with_jm:
                kw["response_format"] = {"type": "json_object"}
            return kw

        def _call_sync():
            return client.chat.completions.create(**_build_kwargs(with_jm=True))
        try:
            resp = await asyncio.to_thread(_call_sync)
        except Exception as e:
            # 某些代理不支持 response_format / extra_body，去掉重试一次
            err = str(e).lower()
            if "response_format" in err or "json_object" in err or "extra_body" in err or "thinking" in err:
                def _call_sync_min():
                    return client.chat.completions.create(
                        model=ai_cfg["model"],
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.0,
                        max_tokens=120,
                    )
                resp = await asyncio.to_thread(_call_sync_min)
            else:
                raise

        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        # 剥围栏
        if text.startswith("```"):
            nl = text.find("\n")
            if nl > 0:
                text = text[nl + 1:]
            if text.endswith("```"):
                text = text[:-3]
        try:
            data = json.loads(text)
        except Exception:
            # 退一步：从文本里抠 6 位数字
            m = re.search(r"\b(\d{6})\b", text)
            if m:
                return {
                    "code": m.group(1), "matched_name": name,
                    "score": 0.5, "source": "llm-fallback",
                    "alternates": [],
                }
            return None

        code = str(data.get("code") or "").strip()
        if not _is_real_fund_code(code):
            return None
        confidence = float(data.get("confidence") or 0.5)
        if confidence < 0.5:
            # 模型自己都不确定，不要采用
            return None
        return {
            "code": code,
            "matched_name": data.get("matched_name") or name,
            "score": round(confidence, 3),
            "source": "llm-fallback",
            "alternates": [],
        }
    except Exception as e:
        print(f"[enrich] llm_guess_fund_code(「{name}」) failed: {type(e).__name__}: {e}")
        return None


# ============================================================
# 主入口
# ============================================================

async def enrich_asset(
    db: Session,
    asset_id: int,
    *,
    fields: list[str] | None = None,
    apply: bool = True,
    use_llm_fallback: bool = True,
) -> dict[str, Any]:
    """补全资产的缺失字段。

    Parameters
    ----------
    asset_id : int
        资产 ID。
    fields : list[str] | None
        要补全的字段列表；None = 自动检测所有缺失字段（目前只看 code）。
    apply : bool
        True = 直接写库；False = 只返回建议，不修改数据库（前端可用于"预览补全"）。
    use_llm_fallback : bool
        天天基金 API 没结果时是否启用 LLM 兜底。

    Returns
    -------
    dict
        {
          "ok": True/False,
          "asset_id": ...,
          "updated": ["code"],          # 实际更新的字段（apply=False 时为空）
          "suggestions": {              # 各字段的建议值（含 score / source）
              "code": {"value": "006228", "score": 0.97, "source": "eastmoney",
                       "matched_name": "东方红稳健精选混合C", "alternates": [...]}
          },
          "before": {"code": "fund_a1b2c3d4"},
          "after":  {"code": "006228"},  # apply=True 时
          "skipped_reason": "..."        # 没补全的原因
        }
    """
    asset = db.get(models.Asset, asset_id)
    if not asset:
        return {"ok": False, "error": f"asset {asset_id} not found"}

    fields_to_check = fields or _auto_detect_missing_fields(asset)
    if not fields_to_check:
        return {
            "ok": True, "asset_id": asset_id, "updated": [],
            "suggestions": {}, "skipped_reason": "no missing fields detected",
        }

    suggestions: dict[str, dict] = {}
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    updated: list[str] = []

    for f in fields_to_check:
        if f == "code":
            sug = await _enrich_code(db, asset, use_llm_fallback=use_llm_fallback)
            if sug is None:
                continue
            before["code"] = asset.code
            suggestions["code"] = sug
            if apply:
                # 同时清掉 note 里的"⚠️ OCR 未识别到代码"标记
                if asset.note:
                    asset.note = re.sub(
                        r"\s*\|?\s*⚠️ OCR 未识别到代码[^|]*",
                        "", asset.note,
                    ).strip(" |")
                # 同时把"匹配到的官方全名"写进 note 末尾，可追溯
                trace = f"代码自动补全：{sug['code']} ← 「{sug.get('matched_name') or asset.name}」 [{sug['source']}, score={sug['score']}]"
                asset.note = (asset.note + " | " + trace).strip(" |") if asset.note else trace
                asset.code = sug["code"]
                after["code"] = sug["code"]
                updated.append("code")

    if apply and updated:
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            return {"ok": False, "error": f"db commit failed: {e}", "suggestions": suggestions}

    return {
        "ok": True,
        "asset_id": asset_id,
        "updated": updated,
        "suggestions": suggestions,
        "before": before,
        "after": after,
    }


def _auto_detect_missing_fields(asset: models.Asset) -> list[str]:
    """看一下哪些字段是缺的或占位的。"""
    out: list[str] = []
    if asset.asset_type.value in ("fund", "etf", "stock"):
        if not asset.code or _is_placeholder_code(asset.code):
            out.append("code")
    return out


async def _enrich_code(
    db: Session,
    asset: models.Asset,
    *,
    use_llm_fallback: bool = True,
) -> dict | None:
    """补全单个资产的 code 字段（多源并行）。

    分流：
    - fund / etf → _enrich_fund_code（4 源并行：天天基金 + 腾讯 + 新浪 + 雪球）
    - stock      → _enrich_stock_code（3 源并行：腾讯 + 新浪 + 雪球）
    - 其它类型   → 跳过 API，可选走 LLM 兜底
    """
    name = asset.name or ""
    if not name.strip():
        return None
    asset_type = asset.asset_type.value

    if asset_type in ("fund", "etf"):
        sug = await _enrich_fund_code(name)
        if sug:
            return sug
    elif asset_type == "stock":
        sug = await _enrich_stock_code(name)
        if sug:
            return sug
    else:
        # money_fund / wealth / cash / bond 等：API 通常没有，直接走 LLM 兜底（如果开了）
        pass

    # LLM 兜底
    if use_llm_fallback:
        sug = await _llm_guess_fund_code(db, name)
        if sug:
            return sug
    return None
