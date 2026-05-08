"""HoldingSnapshot service: 写入与查询资产持仓快照。

用于：
- OCR 导入时记录"那一刻"的持仓状态，便于下次导入对账
- 后续可扩展为定时任务（每天打一个快照），用于回看资产曲线
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .. import models
from ..tz import now_local


def create_snapshot(
    db: Session,
    asset_id: int,
    *,
    shares: float = 0.0,
    avg_cost: Optional[float] = None,
    market_value: Optional[float] = None,
    profit: Optional[float] = None,
    profit_pct: Optional[float] = None,
    source: str = "ocr",
    snapshot_date: Optional[datetime] = None,
    raw: Optional[dict] = None,
    note: str = "",
) -> models.HoldingSnapshot:
    """创建一条持仓快照。"""
    snap = models.HoldingSnapshot(
        asset_id=asset_id,
        source=source,
        snapshot_date=snapshot_date or now_local(),
        shares=shares,
        avg_cost=avg_cost,
        market_value=market_value,
        profit=profit,
        profit_pct=profit_pct,
        raw=raw or {},
        note=note,
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def latest_snapshot(db: Session, asset_id: int) -> Optional[models.HoldingSnapshot]:
    """取该资产最新一条快照（用于 OCR 对账时计算份额差）。"""
    return (
        db.query(models.HoldingSnapshot)
        .filter(models.HoldingSnapshot.asset_id == asset_id)
        .order_by(models.HoldingSnapshot.snapshot_date.desc())
        .first()
    )


def list_snapshots(db: Session, asset_id: int, limit: int = 50) -> list[models.HoldingSnapshot]:
    """该资产的全部快照（默认按时间倒序，最多 50 条）。"""
    return (
        db.query(models.HoldingSnapshot)
        .filter(models.HoldingSnapshot.asset_id == asset_id)
        .order_by(models.HoldingSnapshot.snapshot_date.desc())
        .limit(limit)
        .all()
    )
