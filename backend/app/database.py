"""Database session and base model."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False} if settings.db_url.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations() -> None:
    """Light-weight schema migration for SQLite.

    SQLAlchemy 的 `create_all` 只建表不加列；项目早期没用 Alembic，
    所以在这里用 `ALTER TABLE ADD COLUMN` 给已有表补新字段。
    新加字段必须是 nullable 或带 DEFAULT，否则 SQLite 会拒绝。
    """
    insp = inspect(engine)

    # advices: 新增 batch_id / source
    if "advices" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("advices")}
        with engine.begin() as conn:
            if "batch_id" not in cols:
                conn.execute(text("ALTER TABLE advices ADD COLUMN batch_id VARCHAR(32) DEFAULT ''"))
                # 给历史记录一个兜底 batch_id：用 created_at 截到秒作为批次（毫秒粒度足够散）
                conn.execute(text(
                    "UPDATE advices SET batch_id = "
                    "'legacy_' || strftime('%Y%m%d%H%M%S', created_at) "
                    "WHERE batch_id = '' OR batch_id IS NULL"
                ))
            if "source" not in cols:
                conn.execute(text("ALTER TABLE advices ADD COLUMN source VARCHAR(16) DEFAULT 'batch'"))
                # 历史数据全部视为 batch（过去没区分，这样在 AI 建议页可见）
                conn.execute(text(
                    "UPDATE advices SET source = 'batch' "
                    "WHERE source = '' OR source IS NULL"
                ))
            if "extra" not in cols:
                # SQLite 对 JSON 列用 TEXT 存储，DEFAULT '{}' 作为合法空对象
                conn.execute(text("ALTER TABLE advices ADD COLUMN extra TEXT DEFAULT '{}'"))
                conn.execute(text(
                    "UPDATE advices SET extra = '{}' "
                    "WHERE extra IS NULL OR extra = ''"
                ))
