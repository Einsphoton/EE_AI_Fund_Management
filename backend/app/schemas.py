"""Pydantic schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, List
from pydantic import BaseModel, Field, ConfigDict


# ---------- Asset ----------
class AssetBase(BaseModel):
    name: str
    code: str
    asset_type: str = Field(..., description="fund | stock | etf | money_fund | wealth | cash | bond")
    market: str = Field("OTC", description="A | HK | US | OTC | CNY | USD | HKD")
    platform: str = ""
    note: str = ""
    watch_only: bool = False
    # 理财/货基/现金/债券扩展字段（fund/stock 类型留空即可）
    yield_7d: float | None = None
    expected_apr: float | None = None
    start_date: datetime | None = None
    maturity_date: datetime | None = None
    principal_amount: float | None = None
    is_principal_guaranteed: bool = True


class AssetCreate(AssetBase):
    initial_shares: float | None = None
    initial_price: float | None = None
    initial_amount: float | None = None
    initial_fee: float = 0.0
    initial_date: datetime | None = None


class AssetUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    asset_type: str | None = None
    market: str | None = None
    platform: str | None = None
    note: str | None = None
    watch_only: bool | None = None
    yield_7d: float | None = None
    expected_apr: float | None = None
    start_date: datetime | None = None
    maturity_date: datetime | None = None
    principal_amount: float | None = None
    is_principal_guaranteed: bool | None = None


class AssetOut(AssetBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


# ---------- Transactions ----------
class TransactionBase(BaseModel):
    txn_type: str = "buy"
    shares: float = 0.0
    price: float = 0.0
    amount: float = 0.0
    fee: float = 0.0
    trade_date: datetime | None = None
    note: str = ""


class TransactionCreate(TransactionBase):
    pass


class TransactionUpdate(TransactionBase):
    pass


class TransactionOut(TransactionBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    asset_id: int


# ---------- Todo ----------
class TodoResolvePayload(BaseModel):
    decision: str = Field(..., description="accept | reject")
    shares: float | None = None
    price: float | None = None
    fee: float | None = None
    trade_date: datetime | None = None
    note: str | None = None


class TodoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    todo_type: str
    status: str
    asset_id: int | None = None
    title: str
    description: str = ""
    action: str = ""
    payload: dict = {}
    result: dict = {}
    due_date: datetime | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    asset: AssetOut | None = None


# ---------- Settings ----------
class SettingPayload(BaseModel):
    key: str
    value: Any


# ---------- Skill ----------
class SkillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    skill_id: str
    name: str
    description: str = ""
    category: str = "finance"
    source: str = "builtin"
    enabled: bool = True
    installed_at: datetime


class SkillInstallPayload(BaseModel):
    skill_id: str
    name: str
    description: str = ""
    category: str = "finance"
    source: str = "skillhub"


# ---------- Advice ----------
class AdviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    asset_id: int | None
    batch_id: str = ""
    source: str = "batch"
    action: str
    confidence: float
    summary: str
    detail: str
    extra: dict = {}
    skill_used: str
    created_at: datetime


# ---------- Quote ----------
class QuotePoint(BaseModel):
    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    volume: float | None = None


class QuoteResponse(BaseModel):
    code: str
    asset_type: str
    market: str
    name: str = ""
    points: List[QuotePoint]
    transactions: List[dict] = []
    current_price: float | None = None


class HoldingSummary(BaseModel):
    asset: AssetOut
    total_shares: float
    total_cost: float
    avg_cost: float
    current_price: float | None
    market_value: float | None
    profit: float | None
    profit_pct: float | None
