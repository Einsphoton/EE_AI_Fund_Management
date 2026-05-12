"""Quote API: fetch K-line / NAV with overlay of transactions."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..database import get_db

from ..services import quotes as quotes_service
from ..services import snapshot as snapshot_service
from ..services import settings_service


router = APIRouter(prefix="/api/quotes", tags=["quotes"])


@router.get("/asset/{asset_id}")
async def asset_quote(
    asset_id: int,
    days: int = Query(365, ge=7, le=4000),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    asset = db.query(models.Asset).filter_by(id=asset_id, user_id=current_user.id).first()

    if not asset:
        raise HTTPException(404, "asset not found")
    quote_sources = settings_service.get(db, "quote_sources") or {}
    quote = await quotes_service.fetch_quote(
        asset.asset_type.value, asset.market.value, asset.code, days=days,
        quote_sources=quote_sources,
    )

    txns = [
        {
            "id": t.id,
            "txn_type": t.txn_type.value,
            "shares": t.shares,
            "price": t.price,
            "amount": t.amount,
            "fee": t.fee,
            "trade_date": t.trade_date.isoformat() if t.trade_date else None,
            "note": t.note,
        }
        for t in asset.transactions
    ]
    return {
        "code": asset.code,
        "asset_type": asset.asset_type.value,
        "market": asset.market.value,
        "name": asset.name,
        "points": quote.get("points") or [],
        "current_price": quote.get("current_price"),
        "transactions": txns,
        "error": quote.get("error"),
        "source": quote.get("source"),
        "quote_sources": quote_sources,
    }




@router.get("/asset/{asset_id}/snapshot")
async def asset_snapshot(
    asset_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):

    """基本盘 / 关键指标。仅股票 / 港股 / 美股 / 场内 ETF 有效。

    公开行情源偶发返回异常/空响应时，不能让详情页因为基本盘面板 500。
    这里降级为空对象，并把错误信息带回前端排查。
    """
    asset = db.query(models.Asset).filter_by(id=asset_id, user_id=current_user.id).first()

    if not asset:
        raise HTTPException(404, "asset not found")
    try:
        snap = await snapshot_service.fetch_snapshot(
            asset.asset_type.value, asset.market.value, asset.code,
        )
        return snap or {}
    except Exception as e:
        return {
            "error": f"snapshot fetch failed: {type(e).__name__}: {str(e)[:200]}",
            "symbol": asset.code,
            "name": asset.name,
            "market": asset.market.value,
        }



@router.get("/raw")
async def raw_quote(
    code: str,
    asset_type: str = "stock",
    market: str = "A",
    days: int = Query(180, ge=7, le=4000),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    del current_user

    """无需创建标的也可以预览行情，便于前端在添加时校验代码。"""
    quote_sources = settings_service.get(db, "quote_sources") or {}
    quote = await quotes_service.fetch_quote(asset_type, market, code, days=days, quote_sources=quote_sources)

    return {
        "code": code, "asset_type": asset_type, "market": market,
        "points": quote.get("points") or [],
        "current_price": quote.get("current_price"),
        "error": quote.get("error"),
        "source": quote.get("source"),
        "quote_sources": quote_sources,
    }


