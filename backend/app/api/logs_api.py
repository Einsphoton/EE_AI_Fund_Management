"""Runtime log inspection and export API."""
from __future__ import annotations

import io
import os
import platform
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse

from .. import models
from ..auth import get_current_user
from ..config import settings
from ..logging_config import get_logger, log_dir, redact_obj
from ..services import settings_service
from ..database import get_db
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/logs", tags=["logs"])
logger = get_logger("app.api.logs")


def _safe_log_path(name: str) -> Path:
    clean = Path(name).name
    if not clean or clean != name or not (clean.endswith(".log") or ".log." in clean):
        raise HTTPException(400, "非法日志文件名")
    path = log_dir() / clean
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "日志文件不存在")
    return path


def _tail_text(path: Path, lines: int) -> str:
    lines = max(1, min(5000, int(lines or 300)))
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            block = 8192
            data = b""
            pos = end
            while pos > 0 and data.count(b"\n") <= lines:
                read_size = min(block, pos)
                pos -= read_size
                f.seek(pos)
                data = f.read(read_size) + data
            return b"\n".join(data.splitlines()[-lines:]).decode("utf-8", errors="replace")
    except Exception as e:
        logger.exception("read_log_tail_failed", extra={"event": "read_log_tail_failed", "file": path.name})
        raise HTTPException(500, f"读取日志失败：{e}") from e


def _log_files() -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for p in sorted(log_dir().glob("*.log*"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        try:
            st = p.stat()
        except OSError:
            continue
        files.append({
            "name": p.name,
            "size": st.st_size,
            "modified_at": st.st_mtime,
        })
    return files


@router.get("")
def list_logs(current_user: models.User = Depends(get_current_user)):
    return {
        "log_dir": str(log_dir()),
        "files": _log_files(),
        "active_user_id": current_user.id,
    }


@router.get("/tail", response_class=PlainTextResponse)
def tail_log(
    name: str = Query("ai.log"),
    lines: int = Query(300, ge=1, le=5000),
    current_user: models.User = Depends(get_current_user),
):
    void_user = current_user.id
    del void_user
    path = _safe_log_path(name)
    return PlainTextResponse(_tail_text(path, lines), media_type="text/plain; charset=utf-8")


@router.get("/download")
def download_log(
    name: str = Query("ai.log"),
    current_user: models.User = Depends(get_current_user),
):
    path = _safe_log_path(name)
    logger.info("log_download", extra={"event": "log_download", "file": path.name, "download_user_id": current_user.id})
    return StreamingResponse(
        path.open("rb"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )


@router.get("/bundle")
def download_bundle(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Export redacted diagnostic bundle for support/debugging."""
    buf = io.BytesIO()
    ai_cfg = settings_service.get(db, "ai", user_id=current_user.id) or {}
    vision_cfg = settings_service.get(db, "vision", user_id=current_user.id) or {}
    manifest = {
        "app": settings.app_name,
        "data_dir": settings.data_dir,
        "log_dir": str(log_dir()),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "user_id": current_user.id,
        "ai_config_redacted": redact_obj(ai_cfg),
        "vision_config_redacted": redact_obj(vision_cfg),
        "files": _log_files(),
    }
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", __import__("json").dumps(manifest, ensure_ascii=False, indent=2))
        for item in _log_files():
            try:
                p = _safe_log_path(item["name"])
                if p.stat().st_size <= 30 * 1024 * 1024:
                    zf.write(p, arcname=f"logs/{p.name}")
            except Exception:
                continue
    buf.seek(0)
    logger.info("log_bundle_download", extra={"event": "log_bundle_download", "download_user_id": current_user.id})
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="ee-fund-diagnostics.zip"'},
    )
