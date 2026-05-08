"""DCA suggestion API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..services import dca as dca_service

router = APIRouter(prefix="/api/dca", tags=["dca"])


@router.get("/suggest/{asset_id}")
async def suggest(
    asset_id: int,
    base: float = Query(1000.0, ge=10.0, le=1_000_000.0),
    fee_rate: float = Query(0.001, ge=0.0, le=0.05),
    db: Session = Depends(get_db),
):
    asset = db.get(models.Asset, asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    if asset.asset_type != models.AssetType.fund:
        raise HTTPException(400, "定投建议目前只支持场外基金 (asset_type=fund)")
    s = await dca_service.suggest(asset.code, base_amount=base, fee_rate=fee_rate)
    return dca_service.to_dict(s)
