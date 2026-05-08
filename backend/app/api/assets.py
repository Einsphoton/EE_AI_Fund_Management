"""Asset CRUD + per-asset transactions + holdings summary."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import quotes as quotes_service
from ..services import holdings as holding_service
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
    """并发拉取所有标的实时价，避免 N 次串行 HTTP."""
    import asyncio

    assets = db.query(models.Asset).all()

    async def _safe_price(a: models.Asset) -> float | None:
        try:
            return await quotes_service.fetch_current_price_cached(
                a.asset_type.value, a.market.value, a.code,
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
