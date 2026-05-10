"""在线更新 API。"""
from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from ..services import update_service

router = APIRouter(prefix="/api/update", tags=["update"])


class TriggerUpdateIn(BaseModel):
    confirm: str


@router.get("/status")
async def update_status():
    return await update_service.get_update_status()


@router.post("/trigger")
async def trigger_update(payload: TriggerUpdateIn):
    try:
        return await update_service.trigger_update(payload.confirm)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"触发更新失败：{type(e).__name__}: {e}") from e
