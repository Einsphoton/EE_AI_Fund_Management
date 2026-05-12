"""Todo API: pending user decisions for suggested actions."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import get_current_user
from ..database import get_db

from ..services.investment_manager import expire_pending_todos, get_budget_status, run_investment_manager
from ..tz import now_local

router = APIRouter(prefix="/api/todos", tags=["todos"])


@router.get("/budget-status")
def budget_status(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """列出各平台/币种/资产类型的本月剩余预算。"""
    expire_pending_todos(db, user_id=current_user.id)
    return {"items": get_budget_status(db, user_id=current_user.id)}



@router.post("/ai-investment-plan", response_model=schemas.InvestmentManagerRunOut)
async def run_ai_investment_plan(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """让 AI 投资经理基于投资性格 + 平台月预算生成待确认 To-do。"""
    result = await run_investment_manager(db, user_id=current_user.id)

    return {
        "summary": result.get("summary", ""),
        "created": result.get("created", 0),
        "budget_status": result.get("budget_status", []),
        "todos": [schemas.TodoOut.model_validate(t).model_dump(mode="json") for t in result.get("todos", [])],
    }


@router.get("", response_model=List[schemas.TodoOut])
def list_todos(
    status: str = Query("pending", description="pending | accepted | rejected | all"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    expire_pending_todos(db, user_id=current_user.id)
    q = db.query(models.TodoItem).join(models.Asset, models.Asset.id == models.TodoItem.asset_id)
    q = q.filter(models.Asset.user_id == current_user.id)

    if status != "all":
        q = q.filter(models.TodoItem.status == status)
    return q.order_by(models.TodoItem.due_date.asc(), models.TodoItem.created_at.desc()).all()


@router.post("/{todo_id}/resolve", response_model=schemas.TodoOut)
def resolve_todo(
    todo_id: int,
    payload: schemas.TodoResolvePayload,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    expire_pending_todos(db, user_id=current_user.id)
    todo = db.get(models.TodoItem, todo_id)

    if not todo:
        raise HTTPException(404, "todo not found")
    if todo.asset_id:
        owner_asset = db.query(models.Asset).filter_by(id=todo.asset_id, user_id=current_user.id).first()
        if not owner_asset:
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

    if todo.todo_type not in ("dca_due", "ai_investment"):
        raise HTTPException(400, f"todo type {todo.todo_type} cannot be accepted yet")
    if not todo.asset_id:
        raise HTTPException(400, "todo has no asset")
    asset = db.query(models.Asset).filter_by(id=todo.asset_id, user_id=current_user.id).first()

    if not asset:
        raise HTTPException(404, "asset not found")

    data = todo.payload or {}
    txn_defaults = data.get("transaction") or {}
    txn_type_raw = str(txn_defaults.get("txn_type") or todo.action or "buy").lower()
    try:
        txn_type = models.TxnType(txn_type_raw)
    except ValueError:
        raise HTTPException(400, "todo transaction type must be buy/sell")
    try:
        shares = float(payload.shares if payload.shares is not None else txn_defaults.get("shares") or 0)
        price = float(payload.price if payload.price is not None else txn_defaults.get("price") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "shares/price must be numeric")
    if shares <= 0 or price <= 0:
        raise HTTPException(400, "采纳待办时，份额和价格必须大于 0")

    # 卖出不能超过当前持仓。
    if txn_type == models.TxnType.sell:
        current_shares = sum(
            (t.shares or 0.0) if t.txn_type == models.TxnType.buy else -(t.shares or 0.0)
            for t in asset.transactions
        )
        if current_shares <= 0:
            raise HTTPException(400, "当前没有可卖出的持仓份额")
        shares = min(shares, current_shares)

    amount = round(shares * price, 2)
    if payload.fee is not None:
        fee = float(payload.fee)
    elif "fee" in txn_defaults:
        fee = float(txn_defaults.get("fee") or 0)
    else:
        fee_rate = float(data.get("fee_rate") or 0)
        fee = round(amount * fee_rate, 2)
    trade_date = payload.trade_date or now_local()
    note = payload.note or txn_defaults.get("note") or f"To-do确认 · {todo.title}"

    txn = models.Transaction(
        asset_id=asset.id,
        txn_type=txn_type,
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
            "txn_type": txn_type.value,
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
