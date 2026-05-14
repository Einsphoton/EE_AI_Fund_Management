"""AI advice API."""
from __future__ import annotations

import asyncio
import json
from typing import List


from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import get_current_user
from ..database import get_db

from ..agent.analyzer import analyze_one, analyze_all, analyze_all_stream

router = APIRouter(prefix="/api/advice", tags=["advice"])


@router.get("", response_model=List[schemas.AdviceOut])
def list_recent(
    limit: int = 200,
    source: str | None = None,
    complete_batches: bool = False,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):

    """列出最近建议。

    - `source=batch` → 只返回批量分析的建议
    - `complete_batches=true` → limit 表示最近 N 个批次，并返回这些批次的完整记录。
      这避免前端按“最近 N 条记录”截断时，新批次插入会让老批次少几条，表现为新批次影响老批次。
    - `source=single` → 只返回单独分析的建议
    - 不传 → 全部返回
    """
    q = db.query(models.Advice).join(models.Asset, models.Asset.id == models.Advice.asset_id)
    q = q.filter(models.Asset.user_id == current_user.id)
    if source:
        q = q.filter(models.Advice.source == source)

    if complete_batches and source == "batch":
        batch_rows = (
            db.query(
                models.Advice.batch_id.label("batch_id"),
                func.max(models.Advice.created_at).label("last_at"),
            )
            .join(models.Asset, models.Asset.id == models.Advice.asset_id)
            .filter(models.Asset.user_id == current_user.id)
            .filter(models.Advice.source == "batch")
            .filter(models.Advice.batch_id.isnot(None))
            .filter(models.Advice.batch_id != "")
            .group_by(models.Advice.batch_id)
            .order_by(desc("last_at"))
            .limit(max(1, min(limit, 100)))
            .all()
        )
        batch_ids = [r.batch_id for r in batch_rows]
        if not batch_ids:
            return []
        return (
            q.filter(models.Advice.batch_id.in_(batch_ids))
            .order_by(models.Advice.created_at.desc(), models.Advice.id.desc())
            .all()
        )

    return q.order_by(models.Advice.created_at.desc(), models.Advice.id.desc()).limit(limit).all()


@router.get("/asset/{asset_id}", response_model=List[schemas.AdviceOut])
def list_by_asset(
    asset_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """按标的列出历史建议，不过滤 source（详情页要看全部）。"""
    asset = db.query(models.Asset).filter_by(id=asset_id, user_id=current_user.id).first()
    if not asset:
        raise HTTPException(404, "asset not found")
    return (
        db.query(models.Advice)
        .filter_by(asset_id=asset_id)

        .order_by(models.Advice.created_at.desc())
        .limit(limit).all()
    )


@router.post("/run/{asset_id}", response_model=schemas.AdviceOut)
async def run_for_asset(
    asset_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """单独分析一个标的，默认 source=single，不会出现在全局 AI 建议页。"""
    asset = db.query(models.Asset).filter_by(id=asset_id, user_id=current_user.id).first()

    if not asset:
        raise HTTPException(404, "asset not found")
    return await analyze_one(db, asset)  # source 默认 "single"


@router.post("/run-all")
async def run_for_all(current_user: models.User = Depends(get_current_user)):
    """同步版：一次性跑完，只返回总数（保留向后兼容）。"""
    n = await analyze_all(user_id=current_user.id)

    return {"analyzed": n}


@router.post("/run-all/stream")
async def run_for_all_stream(current_user: models.User = Depends(get_current_user)):

    """流式版（SSE）：每分析完一个标的立刻把状态推给前端。

    前端消费方式：
      const r = await fetch('/api/advice/run-all/stream', { method: 'POST' });
      const reader = r.body.getReader();
      ... 解析 data: {json}\\n\\n
    """
    async def _gen():
        try:
            async for evt in analyze_all_stream(user_id=current_user.id):

                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            return
        except Exception as e:  # pragma: no cover
            yield f"data: {json.dumps({'type': 'fatal', 'error': str(e)}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream", headers={

        # 禁用 Nginx/Cloudflare 缓冲，保证前端能实时拿到
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })
