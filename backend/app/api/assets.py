"""Asset CRUD + per-asset transactions + holdings summary."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import quotes as quotes_service
from ..services import holdings as holding_service
from ..services import settings_service
from ..services.target_recommender import TargetRecommendationError, recommend_ai_targets

from ..tz import now_local

router = APIRouter(prefix="/api/assets", tags=["assets"])


def _to_enum(asset_type: str, market: str):
    try:
        a = models.AssetType(asset_type)
    except ValueError:
        valid = "/".join(t.value for t in models.AssetType)
        raise HTTPException(400, f"asset_type must be one of {valid}, got {asset_type}")
    try:
        m = models.Market(market)
    except ValueError:
        valid = "/".join(m.value for m in models.Market)
        raise HTTPException(400, f"market must be one of {valid}, got {market}")
    return a, m


@router.get("", response_model=List[schemas.AssetOut])
def list_assets(db: Session = Depends(get_db)):
    return db.query(models.Asset).order_by(models.Asset.created_at.desc()).all()


@router.post("", response_model=schemas.AssetOut)
def create_asset(payload: schemas.AssetCreate, db: Session = Depends(get_db)):
    a, m = _to_enum(payload.asset_type, payload.market)
    asset = models.Asset(
        name=payload.name, code=payload.code, asset_type=a, market=m,
        platform=payload.platform, note=payload.note,
        watch_only=payload.watch_only,
        target_source=payload.target_source or "manual",
        yield_7d=payload.yield_7d,
        expected_apr=payload.expected_apr,
        start_date=payload.start_date,
        maturity_date=payload.maturity_date,
        principal_amount=payload.principal_amount,
        is_principal_guaranteed=payload.is_principal_guaranteed,
    )
    db.add(asset)
    db.flush()

    has_initial = (
        (payload.initial_shares and payload.initial_shares > 0)
        or (payload.initial_amount and payload.initial_amount > 0)
    )
    if not payload.watch_only and has_initial:
        shares = payload.initial_shares or 0.0
        price = payload.initial_price or 0.0
        amount = payload.initial_amount or (shares * price)
        if shares == 0 and price > 0 and amount > 0:
            shares = amount / price
        txn = models.Transaction(
            asset_id=asset.id, txn_type=models.TxnType.buy,
            shares=shares, price=price, amount=amount,
            fee=payload.initial_fee or 0.0,
            trade_date=payload.initial_date or now_local(),
            note="初始买入",
        )
        db.add(txn)

    db.commit()
    db.refresh(asset)
    return asset


# ============================================================
# 静态路径优先（必须放在 /{asset_id} 等动态路由之前）
# Starlette 路由是按注册顺序匹配的；把 /lookup-code 这类静态路径放在
# /{asset_id} 系列之后，会被某些 starlette 版本的"先动态后静态"路径
# 解析当成 asset_id="lookup-code" 走到 GET /{asset_id} → 404 / 422，
# 用户在前端看到「查询失败：Not Found」就是这种坑。
# ============================================================

@router.post("/ai-targets", response_model=List[schemas.AssetOut])
async def ai_create_targets(
    limit: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """让 AI 根据投资性格和预算，更新/新增“我的标的”中的 AI 推荐标的。"""
    try:
        return await recommend_ai_targets(db, limit=limit)
    except TargetRecommendationError as e:
        raise HTTPException(400, str(e))


@router.post("/lookup-code")
async def lookup_code(
    name: str,
    asset_type: str = "fund",
    use_llm_fallback: bool = False,   # 默认 off：LLM 没联网时容易瞎猜，反而误导用户
    db: Session = Depends(get_db),
):
    """无状态查代码：根据基金/ETF 名直接返回建议代码，不依赖已存在的 asset。

    主要用于 OCR 对账表"代码缺失"行的实时补全，用户在确认入库前就能看到代码。

    use_llm_fallback 默认 false：天天基金 API 是结构化数据库，找不到的基本就是没有；
    这种时候让普通 LLM 瞎猜（不带联网）反而容易出错，且某些 reasoning 模型还可能
    陷入复读循环烧 RPM。前端默认不启用，仅在用户主动开关时才传 true。
    """
    from ..services.enrichment import _enrich_fund_code, _llm_guess_fund_code
    if not name or not name.strip():
        raise HTTPException(400, "name is required")

    # 主源
    if asset_type in ("fund", "etf"):
        sug = await _enrich_fund_code(name.strip())
        if sug:
            return {"ok": True, "suggestion": sug}

    # 兜底
    if use_llm_fallback:
        sug = await _llm_guess_fund_code(db, name.strip())
        if sug:
            return {"ok": True, "suggestion": sug}

    return {"ok": False, "suggestion": None, "reason": "no candidate found"}


@router.get("/realized-pnl", response_model=schemas.RealizedPnlResponse)
def list_realized_pnl(db: Session = Depends(get_db)):
    """列出所有由卖出操作产生的已实现营收 / 盈亏明细。"""
    assets = db.query(models.Asset).all()
    items: list[dict] = []
    for asset in assets:
        items.extend(holding_service.realized_pnl_events(asset))
    items.sort(key=lambda x: (x.get("trade_date") or now_local()), reverse=True)
    total = round(sum(float(x.get("realized_pnl") or 0.0) for x in items), 2)
    return {"total": total, "count": len(items), "items": items}


@router.get("/{asset_id}", response_model=schemas.AssetOut)

def get_asset(asset_id: int, db: Session = Depends(get_db)):
    asset = db.get(models.Asset, asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    return asset


@router.patch("/{asset_id}", response_model=schemas.AssetOut)
def update_asset(asset_id: int, payload: schemas.AssetUpdate, db: Session = Depends(get_db)):
    asset = db.get(models.Asset, asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    data = payload.model_dump(exclude_unset=True)
    if "asset_type" in data:
        try:
            asset.asset_type = models.AssetType(data.pop("asset_type"))
        except ValueError as e:
            raise HTTPException(400, str(e))
    if "market" in data:
        try:
            asset.market = models.Market(data.pop("market"))
        except ValueError as e:
            raise HTTPException(400, str(e))
    for k, v in data.items():
        setattr(asset, k, v)
    db.commit()
    db.refresh(asset)
    return asset


@router.delete("/{asset_id}")
def delete_asset(asset_id: int, db: Session = Depends(get_db)):
    asset = db.get(models.Asset, asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    db.delete(asset)
    db.commit()
    return {"ok": True}


# -------------- enrichment --------------
@router.post("/{asset_id}/enrich")
async def enrich_asset_endpoint(
    asset_id: int,
    fields: str | None = None,        # 逗号分隔；不传 = 自动检测缺失字段
    apply: bool = True,                # False = 仅返回建议不写库（前端预览用）
    use_llm_fallback: bool = True,
    db: Session = Depends(get_db),
):
    """通用资产字段补全（目前主要用于 OCR 导入后补 fund 代码）。

    流程：
    1. 自动检测（或按用户给定的 fields）哪些字段缺失/占位
    2. 主源走天天基金 fundsuggest API（基金名 → 代码）
    3. API 没结果 → 让 LLM 兜底猜（用现有 ai 配置）
    4. apply=True 直接更新数据库；apply=False 只返回建议供前端确认

    返回结构见 enrichment.enrich_asset() 文档。
    """
    from ..services.enrichment import enrich_asset
    field_list = None
    if fields:
        field_list = [f.strip() for f in fields.split(",") if f.strip()]
    result = await enrich_asset(
        db, asset_id,
        fields=field_list,
        apply=apply,
        use_llm_fallback=use_llm_fallback,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "enrich failed")
    return result





# -------------- transactions --------------
@router.get("/{asset_id}/transactions", response_model=List[schemas.TransactionOut])
def list_transactions(asset_id: int, db: Session = Depends(get_db)):
    asset = db.get(models.Asset, asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    return asset.transactions


@router.post("/{asset_id}/transactions", response_model=schemas.TransactionOut)
def create_txn(asset_id: int, payload: schemas.TransactionCreate, db: Session = Depends(get_db)):
    asset = db.get(models.Asset, asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    try:
        ttype = models.TxnType(payload.txn_type)
    except ValueError:
        raise HTTPException(400, "txn_type must be buy/sell")

    shares = payload.shares or 0.0
    price = payload.price or 0.0
    amount = payload.amount or (shares * price)
    if shares == 0 and price > 0 and amount > 0:
        shares = amount / price

    txn = models.Transaction(
        asset_id=asset_id, txn_type=ttype,
        shares=shares, price=price, amount=amount,
        fee=payload.fee or 0.0,
        trade_date=payload.trade_date or now_local(),
        note=payload.note or "",
    )
    db.add(txn)
    if asset.watch_only:
        asset.watch_only = False
    db.commit()
    db.refresh(txn)
    return txn


@router.patch("/{asset_id}/transactions/{txn_id}", response_model=schemas.TransactionOut)
def update_txn(asset_id: int, txn_id: int, payload: schemas.TransactionUpdate, db: Session = Depends(get_db)):
    txn = db.get(models.Transaction, txn_id)
    if not txn or txn.asset_id != asset_id:
        raise HTTPException(404, "txn not found")
    data = payload.model_dump(exclude_unset=True)
    if "txn_type" in data:
        try:
            txn.txn_type = models.TxnType(data.pop("txn_type"))
        except ValueError as e:
            raise HTTPException(400, str(e))
    for k, v in data.items():
        setattr(txn, k, v)
    db.commit()
    db.refresh(txn)
    return txn


@router.delete("/{asset_id}/transactions/{txn_id}")
def delete_txn(asset_id: int, txn_id: int, db: Session = Depends(get_db)):
    txn = db.get(models.Transaction, txn_id)
    if not txn or txn.asset_id != asset_id:
        raise HTTPException(404, "txn not found")
    db.delete(txn)
    db.commit()
    return {"ok": True}


# -------------- holdings summary --------------
@router.get("/summary/all")
async def list_holdings(db: Session = Depends(get_db)):
    """并发拉取所有资产以及标的实时价，避免 N 次串行 HTTP."""
    import asyncio

    assets = db.query(models.Asset).all()
    quote_sources = settings_service.get(db, "quote_sources") or {}

    async def _safe_price(a: models.Asset) -> float | None:
        try:
            return await quotes_service.fetch_current_price_cached(
                a.asset_type.value, a.market.value, a.code,
                quote_sources=quote_sources,
            )

        except Exception:
            return None

    prices = await asyncio.gather(*[_safe_price(a) for a in assets], return_exceptions=False)

    out = []
    for asset, current in zip(assets, prices):
        h = holding_service.summarize(asset, current)
        out.append({
            "asset": {
                "id": asset.id, "name": asset.name, "code": asset.code,
                "asset_type": asset.asset_type.value, "market": asset.market.value,
                "platform": asset.platform, "watch_only": asset.watch_only,
                "target_source": asset.target_source or "manual",
                "note": asset.note,
                "yield_7d": asset.yield_7d,
                "expected_apr": asset.expected_apr,
                "start_date": asset.start_date.isoformat() if asset.start_date else None,
                "maturity_date": asset.maturity_date.isoformat() if asset.maturity_date else None,
                "principal_amount": asset.principal_amount,
                "is_principal_guaranteed": asset.is_principal_guaranteed,
            },
            **h,
        })
    return out
