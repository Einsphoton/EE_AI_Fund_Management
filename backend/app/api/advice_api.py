"""AI advice API."""
from __future__ import annotations

import asyncio
import json
from typing import List


from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..agent.analyzer import analyze_one, analyze_all, analyze_all_stream

router = APIRouter(prefix="/api/advice", tags=["advice"])


@router.get("", response_model=List[schemas.AdviceOut])
def list_recent(
    limit: int = 200,
    source: str | None = None,
    db: Session = Depends(get_db),
):
    """列出最近建议。

    - `source=batch` → 只返回批量分析的建议（AI 建议页用）
    - `source=single` → 只返回单独分析的建议
    - 不传 → 全部返回
    """
    q = db.query(models.Advice)
    if source:
        q = q.filter(models.Advice.source == source)
    return q.order_by(models.Advice.created_at.desc()).limit(limit).all()


@router.get("/asset/{asset_id}", response_model=List[schemas.AdviceOut])
def list_by_asset(asset_id: int, limit: int = 50, db: Session = Depends(get_db)):
    """按标的列出历史建议，不过滤 source（详情页要看全部）。"""
    return (
        db.query(models.Advice)
        .filter_by(asset_id=asset_id)
        .order_by(models.Advice.created_at.desc())
        .limit(limit).all()
    )


@router.post("/run/{asset_id}", response_model=schemas.AdviceOut)
async def run_for_asset(asset_id: int, db: Session = Depends(get_db)):
    """单独分析一个标的，默认 source=single，不会出现在全局 AI 建议页。"""
    asset = db.get(models.Asset, asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    return await analyze_one(db, asset)  # source 默认 "single"


@router.post("/run-all")
async def run_for_all():
    """同步版：一次性跑完，只返回总数（保留向后兼容）。"""
    n = await analyze_all()
    return {"analyzed": n}


@router.post("/run-all/stream")
async def run_for_all_stream():
    """流式版（SSE）：每分析完一个标的立刻把状态推给前端。

    前端消费方式：
      const r = await fetch('/api/advice/run-all/stream', { method: 'POST' });
      const reader = r.body.getReader();
      ... 解析 data: {json}\\n\\n
    """
    async def _gen():
        try:
            async for evt in analyze_all_stream():
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
