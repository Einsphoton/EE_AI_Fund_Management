"""DCA suggestion API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import dca as dca_service
from ..tz import now_local

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


@router.post("/todo/{asset_id}", response_model=schemas.TodoOut)
async def create_dca_todo(
    asset_id: int,
    base: float = Query(1000.0, ge=10.0, le=1_000_000.0),
    fee_rate: float = Query(0.001, ge=0.0, le=0.05),
    db: Session = Depends(get_db),
):
    """把本期定投到期动作放入 To-do，等待用户确认是否采纳。"""
    asset = db.get(models.Asset, asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    if asset.asset_type != models.AssetType.fund:
        raise HTTPException(400, "定投待办目前只支持场外基金 (asset_type=fund)")

    s = await dca_service.suggest(asset.code, base_amount=base, fee_rate=fee_rate)
    suggestion = dca_service.to_dict(s)
    price = float(s.last_price or 0)
    shares = float(s.suggest_shares or 0)
    amount = round(shares * price, 2) if price and shares else 0.0
    now = now_local()
    decision_label = {
        "buy_more": "加大投入",
        "buy_normal": "正常定投",
        "buy_less": "减少投入",
        "skip": "本期暂缓",
    }.get(s.decision, s.decision)
    payload = {
        "source": "dca",
        "base_amount": base,
        "fee_rate": fee_rate,
        "suggestion": suggestion,
        "transaction": {
            "txn_type": "buy",
            "shares": shares,
            "price": price,
            "amount": amount,
            "fee": float(s.estimated_fee or 0),
            "trade_date": now.isoformat(),
            "note": f"定投·{decision_label}（基础¥{base:g}，To-do确认）",
        },
    }

    # 同一标的只保留一个待确认的定投到期待办；重复生成时刷新建议，避免堆积重复项。
    todo = (
        db.query(models.TodoItem)
        .filter_by(asset_id=asset.id, todo_type="dca_due", status="pending")
        .first()
    )
    if todo is None:
        todo = models.TodoItem(
            todo_type="dca_due",
            status="pending",
            asset_id=asset.id,
        )
        db.add(todo)

    todo.title = f"定投到期：{asset.name}"
    todo.description = s.reason
    todo.action = "skip" if s.decision == "skip" else "buy"
    todo.payload = payload
    todo.result = {}
    todo.due_date = now
    db.commit()
    db.refresh(todo)
    return todo

