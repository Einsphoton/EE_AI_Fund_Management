"""Local account auth helpers."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .database import get_db

_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\-.]{3,32}$")


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _secret_key() -> bytes:
    env = os.getenv("AUTH_SECRET", "").strip()
    if env:
        return env.encode("utf-8")
    path = Path(settings.data_dir) / "auth_secret.key"
    if path.exists():
        return path.read_bytes().strip()
    key = secrets.token_urlsafe(48).encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    return key


def validate_username(username: str) -> str:
    username = (username or "").strip()
    if not _USERNAME_RE.match(username):
        raise HTTPException(400, "用户名需为 3-32 位字母、数字、下划线、横线或点")
    return username


def hash_password(password: str) -> str:
    if len(password or "") < 6:
        raise HTTPException(400, "密码至少需要 6 位")
    iterations = 200_000
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iter_s, salt_s, digest_s = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _b64d(salt_s), int(iter_s))
        return hmac.compare_digest(digest, _b64d(digest_s))
    except Exception:
        return False


def create_access_token(user: models.User) -> str:
    payload = {
        "sub": user.id,
        "username": user.username,
        "iat": int(time.time()),
        "exp": int(time.time()) + _TOKEN_TTL_SECONDS,
        "nonce": secrets.token_hex(8),
    }
    body = _b64e(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_secret_key(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64e(sig)}"


def _decode_access_token(token: str) -> dict[str, Any]:
    try:
        body, sig = token.split(".", 1)
        expected = hmac.new(_secret_key(), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(sig), expected):
            raise ValueError("bad signature")
        payload = json.loads(_b64d(body).decode("utf-8"))
        if int(payload.get("exp") or 0) < int(time.time()):
            raise ValueError("expired")
        return payload
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _extract_bearer(authorization: str | None, cookie_token: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if cookie_token:
        return cookie_token.strip()
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="请先登录",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    authorization: str | None = Header(default=None),
    ee_auth_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> models.User:
    token = _extract_bearer(authorization, ee_auth_token)
    payload = _decode_access_token(token)
    user = db.get(models.User, int(payload.get("sub") or 0))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="账号不存在或已停用")
    db.info["user_id"] = user.id
    return user
