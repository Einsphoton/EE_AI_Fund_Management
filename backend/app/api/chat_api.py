"""Chat with AI agent over the user's whole portfolio (streaming)."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db, SessionLocal
from ..services import chat as chat_service

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str    # "user" | "assistant" | "system"
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """SSE-style streaming response, content type text/event-stream.

    每行： data: <json-escaped-token>\n\n
    完成： data: [DONE]\n\n
    """
    history = [m.model_dump() for m in req.messages]

    async def _gen():
        # 这里给一个独立 session（不复用请求作用域，因为 generator 可能跨请求生命周期）
        db = SessionLocal()
        try:
            import json as _json
            async for token in chat_service.stream_chat(db, history):
                yield f"data: {_json.dumps(token, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            db.close()

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.post("/once")
async def chat_once(req: ChatRequest, db: Session = Depends(get_db)):
    """Non-streaming variant, returns the whole text at once."""
    history = [m.model_dump() for m in req.messages]
    full = ""
    async for tok in chat_service.stream_chat(db, history):
        full += tok
    return {"content": full}
