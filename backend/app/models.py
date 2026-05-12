"""ORM models for assets, transactions, settings, skills, advice, snapshots."""
from __future__ import annotations

import enum
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Enum, ForeignKey, Text, Boolean, JSON,
)
from sqlalchemy.orm import relationship

from .database import Base
from .tz import now_local


class AssetType(str, enum.Enum):
    """资产大类——已扩展为 7 类，覆盖个人理财场景。

    迁移注意：使用 native_enum=False 把列存为 VARCHAR + 应用层校验，
    避免 SQLite 上 CHECK 约束阻止旧库写入新值。
    """
    fund = "fund"               # OTC 场外基金（普通公募开放式基金）
    stock = "stock"             # 股票
    etf = "etf"                 # 场内基金 / ETF / LOF（统一与"股票"区分）
    money_fund = "money_fund"   # 货币基金 / 活期类（余额宝、朝朝宝、零钱通…）
    wealth = "wealth"           # 银行/平台理财（定期、净值型、结构性存款）
    cash = "cash"               # 现金 / 活期存款（不计息或微利）
    bond = "bond"               # 债券 / 国债逆回购


class Market(str, enum.Enum):
    cn = "A"        # A 股
    hk = "HK"       # 港股
    us = "US"       # 美股
    otc = "OTC"     # 场外基金
    cny = "CNY"     # 人民币现金/理财（无市场概念，复用 market 字段标识币种）
    usd = "USD"     # 美元现金/理财
    hkd = "HKD"     # 港币现金/理财


class TxnType(str, enum.Enum):
    buy = "buy"
    sell = "sell"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(32), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=now_local)
    updated_at = Column(DateTime, default=now_local, onupdate=now_local)

    assets = relationship("Asset", back_populates="user")


class Asset(Base):

    __tablename__ = "assets"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String(128), nullable=False)

    code = Column(String(32), nullable=False, index=True)        # 基金代码 / 股票代码 / 理财产品编号
    # native_enum=False：VARCHAR 存储，方便扩展新枚举值无需迁移
    asset_type = Column(Enum(AssetType, native_enum=False, length=16), nullable=False)
    market = Column(Enum(Market, native_enum=False, length=8), nullable=False, default=Market.otc)
    platform = Column(String(64), default="")                    # 买入平台
    note = Column(Text, default="")
    watch_only = Column(Boolean, default=False)                  # 仅观察、未实质买入
    target_source = Column(String(16), default="manual", index=True)  # manual / ai，用于区分标的来源

    # ---- 理财/货基/现金扩展字段（fund/stock 类型留空） ----
    # 货基：当前 7 日年化（百分比，如 1.85 表示 1.85%），无需每日抓行情
    yield_7d = Column(Float, nullable=True)
    # 理财：预期年化收益率（百分比）
    expected_apr = Column(Float, nullable=True)
    # 理财：起息日 / 到期日（用于按日累计收益）
    start_date = Column(DateTime, nullable=True)
    maturity_date = Column(DateTime, nullable=True)
    # 现金/理财：本金金额（不走 Transaction 流程，直接配置好金额，方便快速录入）
    # 对于 fund/stock 该字段为 None，市值仍由持仓 × 行情计算
    principal_amount = Column(Float, nullable=True)
    # 货基/理财/现金：是否非保本（影响风险评分）
    is_principal_guaranteed = Column(Boolean, default=True)

    created_at = Column(DateTime, default=now_local)
    updated_at = Column(DateTime, default=now_local, onupdate=now_local)

    user = relationship("User", back_populates="assets")
    transactions = relationship(

        "Transaction", back_populates="asset",
        cascade="all, delete-orphan", order_by="Transaction.trade_date.asc()",
    )
    advices = relationship(
        "Advice", back_populates="asset",
        cascade="all, delete-orphan", order_by="Advice.created_at.desc()",
    )
    snapshots = relationship(
        "HoldingSnapshot", back_populates="asset",
        cascade="all, delete-orphan", order_by="HoldingSnapshot.snapshot_date.desc()",
    )
    todo_items = relationship(
        "TodoItem", back_populates="asset",
        cascade="all, delete-orphan", order_by="TodoItem.created_at.desc()",
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    txn_type = Column(Enum(TxnType, native_enum=False, length=8), nullable=False, default=TxnType.buy)
    shares = Column(Float, nullable=False, default=0.0)          # 份额 / 股数
    price = Column(Float, nullable=False, default=0.0)           # 单价 / 净值
    amount = Column(Float, nullable=False, default=0.0)          # 成交金额（股票）
    fee = Column(Float, nullable=False, default=0.0)             # 手续费
    trade_date = Column(DateTime, default=now_local)
    note = Column(Text, default="")

    asset = relationship("Asset", back_populates="transactions")


class HoldingSnapshot(Base):
    """持仓快照：OCR 导入时记录"那一刻"的持仓状态，便于下次导入对账。

    与 Transaction 的区别：
    - Transaction 记录"动作"（买/卖），是事件流
    - HoldingSnapshot 记录"状态"（份额=X，市值=Y），是状态点
    OCR 重复上传时，对比最近一次同 asset 的 snapshot 来判断是追加 / 减仓 / 不变。
    """
    __tablename__ = "holding_snapshots"

    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    # 快照来源：ocr / manual / batch_import
    source = Column(String(16), default="ocr", index=True)
    # 快照时点（用户截图时刻；通常 = 上传日期）
    snapshot_date = Column(DateTime, default=now_local, index=True)
    shares = Column(Float, default=0.0)                # 当时持有份额（货基/现金可能用 amount 表示）
    avg_cost = Column(Float, nullable=True)            # 当时平均成本
    market_value = Column(Float, nullable=True)        # 当时市值
    profit = Column(Float, nullable=True)              # 当时累计收益
    profit_pct = Column(Float, nullable=True)          # 当时收益率（%）
    # OCR 提取的原始 JSON（含模型置信度、原始字符串等），便于排错
    raw = Column(JSON, default={})
    note = Column(Text, default="")
    created_at = Column(DateTime, default=now_local)

    asset = relationship("Asset", back_populates="snapshots")


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


class TodoItem(Base):
    """用户待确认动作：定投到期、追投、调仓、建仓、卖出等统一进入这里。"""
    __tablename__ = "todo_items"

    id = Column(Integer, primary_key=True)
    todo_type = Column(String(32), default="manual", index=True)       # dca_due / rebalance / buy / sell ...
    status = Column(String(16), default="pending", index=True)         # pending / accepted / rejected
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=True, index=True)
    title = Column(String(160), nullable=False)
    description = Column(Text, default="")
    action = Column(String(32), default="")                            # buy / sell / hold / skip
    payload = Column(JSON, default={})                                  # 建议详情与默认交易参数
    result = Column(JSON, default={})                                   # 用户确认后的结果
    due_date = Column(DateTime, default=now_local, index=True)
    expires_at = Column(DateTime, nullable=True, index=True)                # 到期未处理则自动视为不采纳
    created_at = Column(DateTime, default=now_local)
    updated_at = Column(DateTime, default=now_local, onupdate=now_local)
    resolved_at = Column(DateTime, nullable=True)

    asset = relationship("Asset", back_populates="todo_items")


class Advice(Base):
    __tablename__ = "advices"

    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=True)
    batch_id = Column(String(32), default="", index=True)
    source = Column(String(16), default="batch", index=True)
    action = Column(String(16), default="hold")         # buy / hold / sell
    confidence = Column(Float, default=0.0)
    summary = Column(Text, default="")
    detail = Column(Text, default="")
    extra = Column(JSON, default={})
    skill_used = Column(String(128), default="")
    created_at = Column(DateTime, default=now_local)

    asset = relationship("Asset", back_populates="advices")
