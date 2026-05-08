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

    # advices: 新增 batch_id / source / extra
    if "advices" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("advices")}
        with engine.begin() as conn:
            if "batch_id" not in cols:
                conn.execute(text("ALTER TABLE advices ADD COLUMN batch_id VARCHAR(32) DEFAULT ''"))
                conn.execute(text(
                    "UPDATE advices SET batch_id = "
                    "'legacy_' || strftime('%Y%m%d%H%M%S', created_at) "
                    "WHERE batch_id = '' OR batch_id IS NULL"
                ))
            if "source" not in cols:
                conn.execute(text("ALTER TABLE advices ADD COLUMN source VARCHAR(16) DEFAULT 'batch'"))
                conn.execute(text(
                    "UPDATE advices SET source = 'batch' "
                    "WHERE source = '' OR source IS NULL"
                ))
            if "extra" not in cols:
                conn.execute(text("ALTER TABLE advices ADD COLUMN extra TEXT DEFAULT '{}'"))
                conn.execute(text(
                    "UPDATE advices SET extra = '{}' "
                    "WHERE extra IS NULL OR extra = ''"
                ))

    # ---------------- assets 扩展（理财/货基/现金字段） ----------------
    if "assets" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("assets")}
        with engine.begin() as conn:
            new_cols = [
                ("yield_7d", "FLOAT"),
                ("expected_apr", "FLOAT"),
                ("start_date", "DATETIME"),
                ("maturity_date", "DATETIME"),
                ("principal_amount", "FLOAT"),
                ("is_principal_guaranteed", "BOOLEAN DEFAULT 1"),
            ]
            for col, ddl in new_cols:
                if col not in cols:
                    conn.execute(text(f"ALTER TABLE assets ADD COLUMN {col} {ddl}"))

        # 旧 SQLite 库的 assets / transactions 表上有 Enum CHECK 约束
        # （`asset_type IN ('fund','stock')` 等），新枚举值会被拒绝。
        # 这里检测 sqlite_master，发现 CHECK 就重建表为 native_enum=False 风格。
        _rebuild_table_if_check(conn_url=settings.db_url, table="assets",
                                check_token="asset_type IN")
        _rebuild_table_if_check(conn_url=settings.db_url, table="assets",
                                check_token="market IN")
        _rebuild_table_if_check(conn_url=settings.db_url, table="transactions",
                                check_token="txn_type IN")


def _rebuild_table_if_check(conn_url: str, table: str, check_token: str) -> None:
    """如果 SQLite 表的 schema 里包含指定 CHECK 子串，就把该约束剥离。

    SQLite 不支持 ALTER TABLE DROP CONSTRAINT，常见做法：
      1) CREATE TABLE _new AS SELECT * FROM old (但这会丢索引/PK 信息)
    保险起见这里改用：
      1) PRAGMA writable_schema = 1 直接改 sqlite_master 中保存的 CREATE 语句
      2) 用正则去掉单条 CHECK 子句
    这种做法对 SQLite 是合规的（PRAGMA writable_schema 是官方提供的入口）。
    """
    if not conn_url.startswith("sqlite"):
        return
    import re
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=:t"
        ), {"t": table}).fetchone()
        if not row:
            return
        sql = row[0] or ""
        if check_token not in sql:
            return
        # 去掉一个 CHECK ( ... ) 子句（含可选的列限定符）
        pattern = re.compile(
            r",?\s*CHECK\s*\(\s*[^()]*?" + re.escape(check_token) + r"[^()]*?\)\s*",
            re.IGNORECASE,
        )
        new_sql = pattern.sub(" ", sql, count=1)
        # 清掉可能残留的 ", )" 末尾
        new_sql = re.sub(r",\s*\)", ")", new_sql)
        if new_sql == sql:
            return
        conn.execute(text("PRAGMA writable_schema = 1"))
        conn.execute(text(
            "UPDATE sqlite_master SET sql=:sql WHERE type='table' AND name=:t"
        ), {"sql": new_sql, "t": table})
        conn.execute(text("PRAGMA writable_schema = 0"))
        # 触发 schema 重新加载（不重启进程也生效）
        conn.execute(text("VACUUM"))
