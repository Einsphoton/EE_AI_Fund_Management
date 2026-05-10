"""Todo API: pending user decisions for suggested actions."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..tz import now_local

router = APIRouter(prefix="/api/todos", tags=["todos"])


@router.get("", response_model=List[schemas.TodoOut])
def list_todos(
    status: str = Query("pending", description="pending | accepted | rejected | all"),
    db: Session = Depends(get_db),
):
    q = db.query(models.TodoItem)
    if status != "all":
        q = q.filter(models.TodoItem.status == status)
    return q.order_by(models.TodoItem.due_date.asc(), models.TodoItem.created_at.desc()).all()


@router.post("/{todo_id}/resolve", response_model=schemas.TodoOut)
def resolve_todo(
    todo_id: int,
    payload: schemas.TodoResolvePayload,
    db: Session = Depends(get_db),
):
    todo = db.get(models.TodoItem, todo_id)
    if not todo:
        raise HTTPException(404, "todo not found")
    if todo.status != "pending":
        raise HTTPException(400, "todo already resolved")

    decision = payload.decision.lower().strip()
    if decision not in ("accept", "reject"):
        raise HTTPException(400, "decision must be accept or reject")

    if decision == "reject":
        todo.status = "rejected"
        todo.resolved_at = now_local()
        todo.result = {"decision": "reject", "note": payload.note or ""}
        db.commit()
        db.refresh(todo)
        return todo

    if todo.todo_type != "dca_due":
        raise HTTPException(400, f"todo type {todo.todo_type} cannot be accepted yet")
    if not todo.asset_id:
        raise HTTPException(400, "todo has no asset")
    asset = db.get(models.Asset, todo.asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")

    data = todo.payload or {}
    txn_defaults = data.get("transaction") or {}
    try:
        shares = float(payload.shares if payload.shares is not None else txn_defaults.get("shares") or 0)
        price = float(payload.price if payload.price is not None else txn_defaults.get("price") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "shares/price must be numeric")
    if shares <= 0 or price <= 0:
        raise HTTPException(400, "采纳定投待办时，份额和价格必须大于 0")

    amount = round(shares * price, 2)
    if payload.fee is not None:
        fee = float(payload.fee)
    else:
        fee_rate = float(data.get("fee_rate") or 0)
        fee = round(amount * fee_rate, 2)
    trade_date = payload.trade_date or now_local()
    note = payload.note or txn_defaults.get("note") or f"定投确认 · {todo.title}"

    txn = models.Transaction(
        asset_id=asset.id,
        txn_type=models.TxnType.buy,
        shares=shares,
        price=price,
        amount=amount,
        fee=fee,
        trade_date=trade_date,
        note=note,
    )
    db.add(txn)
    if asset.watch_only:
        asset.watch_only = False

    todo.status = "accepted"
    todo.resolved_at = now_local()
    todo.result = {
        "decision": "accept",
        "transaction": {
            "asset_id": asset.id,
            "txn_type": "buy",
            "shares": shares,
            "price": price,
            "amount": amount,
            "fee": fee,
            "trade_date": trade_date.isoformat(),
            "note": note,
        },
    }
    db.commit()
    db.refresh(txn)
    todo.result = {
        **(todo.result or {}),
        "transaction_id": txn.id,
    }
    db.commit()
    db.refresh(todo)
    return todo
