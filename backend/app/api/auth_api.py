"""Account registration and login API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sqlalchemy.orm import Session

from .. import models
from ..auth import create_access_token, get_current_user, hash_password, validate_username, verify_password
from ..database import get_db
from ..services import settings_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthUserOut(BaseModel):
    id: int
    username: str
    email: str | None = None


class AuthResponse(BaseModel):
    token: str
    user: AuthUserOut


class RegisterPayload(BaseModel):
    username: str
    password: str
    email: str | None = None



class LoginPayload(BaseModel):
    username: str
    password: str


def _user_out(user: models.User) -> AuthUserOut:
    return AuthUserOut(id=user.id, username=user.username, email=user.email or None)


def _claim_legacy_data_for_first_user(db: Session, user: models.User) -> None:
    """把升级前的单用户数据归到第一个注册账号，避免丢历史数据。"""
    db.query(models.Asset).filter(models.Asset.user_id.is_(None)).update({"user_id": user.id})

    legacy_settings = [
        row for row in db.query(models.AppSetting).all()
        if not str(row.key).startswith("u:")
    ]
    for row in legacy_settings:
        scoped_key = settings_service.scoped_key(row.key, user.id)
        if db.get(models.AppSetting, scoped_key) is None:
            db.add(models.AppSetting(key=scoped_key, value=row.value, updated_at=row.updated_at))
        db.delete(row)


@router.post("/register", response_model=AuthResponse)
def register(payload: RegisterPayload, db: Session = Depends(get_db)):
    username = validate_username(payload.username)
    if db.query(models.User).filter(models.User.username == username).first():
        raise HTTPException(400, "用户名已存在")
    email = str(payload.email).strip().lower() if payload.email else ""
    if email and db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(400, "邮箱已被使用")

    is_first_user = db.query(models.User).count() == 0
    user = models.User(
        username=username,
        email=email or None,

        password_hash=hash_password(payload.password),
        is_active=True,
    )
    db.add(user)
    db.flush()
    if is_first_user:
        _claim_legacy_data_for_first_user(db, user)
    db.commit()
    db.refresh(user)
    return AuthResponse(token=create_access_token(user), user=_user_out(user))


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginPayload, db: Session = Depends(get_db)):
    username = (payload.username or "").strip()
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(403, "账号已停用")
    return AuthResponse(token=create_access_token(user), user=_user_out(user))


@router.get("/me", response_model=AuthUserOut)
def me(current_user: models.User = Depends(get_current_user)):
    return _user_out(current_user)
