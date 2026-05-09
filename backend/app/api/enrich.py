"""资产字段补全 API（独立 prefix /api/enrich）。

**为什么不放 assets.py 里？**
早期把 `lookup-code` 放在 `/api/assets/lookup-code` 下，但前端在生产部署下持续拿到
**405 Method Not Allowed**。根因链：

1. `main.py` 里 `@app.get("/{full_path:path}")` 是 SPA 的 catch-all fallback，
   **只接受 GET**；
2. 在 Starlette 的 route 匹配表里，某些部署下（CF Access / uvicorn --reload 未彻底
   重载等）POST `/api/assets/lookup-code` 会被当成 `GET /{full_path:path}` 处理，
   于是返回 405（GET 不允许你用 POST）。
3. 即便按注册顺序 `assets.py` 的 `POST /lookup-code` 在前，只要它因为任何原因没
   真正生效（比如旧 worker 没 reload），fallback 就会接管并拒绝 POST。

**根治**：开一个**独立的 prefix**，跟 `/assets/{asset_id}` 完全不共享路径根，也
就不会被路由顺序 / mount / fallback 等任何地方的"同源动态路径"吃掉。

保留 `assets.py::lookup_code` 不动，作为向后兼容；新代码统一走这里。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db

router = APIRouter(prefix="/api/enrich", tags=["enrich"])


@router.post("/fund-code")
async def enrich_fund_code(
    name: str,
    asset_type: str = "fund",
    use_llm_fallback: bool = False,
    db: Session = Depends(get_db),
):
    """按名字查官方代码（多源并行：天天基金 + 腾讯 + 新浪 + 雪球）。

    支持的 asset_type：
      - fund / etf / money_fund / bond → 走基金代码搜索（4 源并行）
      - stock                          → 走股票代码搜索（A股/港股/美股，3 源并行）

    独立路径 `/api/enrich/fund-code`：与 `/api/assets/{asset_id}` 无任何路径冲突，
    根治前端看到的 405 Method Not Allowed。

    use_llm_fallback：所有数据源都没结果时是否让 LLM 兜底（默认 False，避免瞎编）。
    """
    from ..services.enrichment import (
        _enrich_fund_code, _enrich_stock_code, _llm_guess_fund_code,
    )

    if not name or not name.strip():
        raise HTTPException(400, "name is required")

    name_clean = name.strip()
    asset_type_low = (asset_type or "fund").lower()

    # 主源：按 asset_type 分流
    if asset_type_low == "stock":
        sug = await _enrich_stock_code(name_clean)
    else:
        # fund / etf / money_fund / bond / wealth 都走基金搜索
        # （bond 也可能是场内债基；ETF 在天天基金里也存在）
        sug = await _enrich_fund_code(name_clean)
    if sug:
        return {"ok": True, "suggestion": sug}

    # 兜底：LLM 猜一个（默认关）
    if use_llm_fallback:
        sug = await _llm_guess_fund_code(db, name_clean)
        if sug:
            return {"ok": True, "suggestion": sug}

    return {"ok": False, "suggestion": None, "reason": "no candidate found in any data source"}


# 同时开放 GET 版本：浏览器地址栏 / curl 调试都能直接用
@router.get("/fund-code")
async def enrich_fund_code_get(
    name: str,
    asset_type: str = "fund",
    use_llm_fallback: bool = False,
    db: Session = Depends(get_db),
):
    """GET 版本：等价于 POST，方便调试 / 浏览器地址栏直接访问。

    生产环境任何被动请求（比如"网关把 POST 改写成 GET"、"预加载请求"）
    打进来也不会 405。
    """
    return await enrich_fund_code(
        name=name, asset_type=asset_type,
        use_llm_fallback=use_llm_fallback, db=db,
    )
