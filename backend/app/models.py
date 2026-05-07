"""ORM models for assets, transactions, settings, skills, advice."""
from __future__ import annotations

import enum
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Enum, ForeignKey, Text, Boolean, JSON,
)
from sqlalchemy.orm import relationship

from .database import Base
from .tz import now_local


class AssetType(str, enum.Enum):
    fund = "fund"            # OTC 场外基金
    stock = "stock"          # 股票 / 场内基金 / ETF


class Market(str, enum.Enum):
    cn = "A"        # A 股
    hk = "HK"       # 港股
    us = "US"       # 美股
    otc = "OTC"     # 场外基金


class TxnType(str, enum.Enum):
    buy = "buy"
    sell = "sell"


class Asset(Base):
    __tablename__ = "assets"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    code = Column(String(32), nullable=False, index=True)        # 基金代码 / 股票代码
    asset_type = Column(Enum(AssetType), nullable=False)
    market = Column(Enum(Market), nullable=False, default=Market.otc)
    platform = Column(String(64), default="")                    # 买入平台
    note = Column(Text, default="")
    watch_only = Column(Boolean, default=False)                  # 仅观察、未实质买入
    created_at = Column(DateTime, default=now_local)
    updated_at = Column(DateTime, default=now_local, onupdate=now_local)

    transactions = relationship(
        "Transaction", back_populates="asset",
        cascade="all, delete-orphan", order_by="Transaction.trade_date.asc()",
    )
    advices = relationship(
        "Advice", back_populates="asset",
        cascade="all, delete-orphan", order_by="Advice.created_at.desc()",
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    txn_type = Column(Enum(TxnType), nullable=False, default=TxnType.buy)
    shares = Column(Float, nullable=False, default=0.0)          # 份额 / 股数
    price = Column(Float, nullable=False, default=0.0)           # 单价 / 净值
    amount = Column(Float, nullable=False, default=0.0)          # 成交金额（股票）
    fee = Column(Float, nullable=False, default=0.0)             # 手续费
    trade_date = Column(DateTime, default=now_local)
    note = Column(Text, default="")

    asset = relationship("Asset", back_populates="transactions")


class AppSetting(Base):
    """KV 形式的全局配置（单用户）."""
    __tablename__ = "app_settings"

    key = Column(String(64), primary_key=True)
    value = Column(JSON, nullable=False, default={})
    updated_at = Column(DateTime, default=now_local, onupdate=now_local)


class Skill(Base):
    __tablename__ = "skills"

    id = Column(Integer, primary_key=True)
    skill_id = Column(String(128), unique=True, nullable=False)   # 来源 ID
    name = Column(String(128), nullable=False)
    description = Column(Text, default="")
    category = Column(String(64), default="finance")
    source = Column(String(255), default="builtin")               # builtin / skillhub
    enabled = Column(Boolean, default=True)
    config = Column(JSON, default={})
    installed_at = Column(DateTime, default=now_local)


class Advice(Base):
    __tablename__ = "advices"

    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=True)
    # 批次 ID：同一次 analyze_all() 执行产生的所有 advice 共享一个 batch_id，
    # 前端据此把一整批分析结果归拢到一张卡片里展示。单次分析也有自己的 batch_id。
    batch_id = Column(String(32), default="", index=True)
    # 来源：batch = 批量分析（手动/定时触发 analyze_all），single = 单标的分析（详情页里点的）
    # 用于区分"AI 建议"页该不该展示——AI 建议页只展示 batch，详情页展示全部。
    source = Column(String(16), default="batch", index=True)
    action = Column(String(16), default="hold")         # buy / hold / sell
    confidence = Column(Float, default=0.0)
    summary = Column(Text, default="")
    detail = Column(Text, default="")
    # 结构化扩展字段（score / fundamentals / macro / micro / risks / pros / advice /
    # time_horizon / target_price / stop_loss）—— 前端富卡片直接消费此字段。
    # 旧数据可能为 null 或 {}，前端务必容错。
    extra = Column(JSON, default={})
    skill_used = Column(String(128), default="")
    created_at = Column(DateTime, default=now_local)

    asset = relationship("Asset", back_populates="advices")
