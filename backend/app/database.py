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

    # todo_items: 新增 expires_at
    if "todo_items" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("todo_items")}
        with engine.begin() as conn:
            if "expires_at" not in cols:
                conn.execute(text("ALTER TABLE todo_items ADD COLUMN expires_at DATETIME"))

    # ---------------- assets 扩展（理财/货基/现金字段） ----------------
    if "assets" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("assets")}
        with engine.begin() as conn:
            new_cols = [
                ("target_source", "VARCHAR(16) DEFAULT 'manual'"),
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
            conn.execute(text(
                "UPDATE assets SET target_source='ai' "
                "WHERE watch_only = 1 AND (note LIKE 'AI加入标的池%' OR note LIKE 'AI推荐标的%')"
            ))

        # 旧 SQLite 库的 assets / transactions 表上有 Enum CHECK 约束
        # （`asset_type IN ('fund','stock')` 等），新枚举值会被拒绝。
        # 这里检测 sqlite_master，发现 CHECK 就重建表为 native_enum=False 风格。
        _rebuild_table_if_check(conn_url=settings.db_url, table="assets",
                                check_token="asset_type IN")
        _rebuild_table_if_check(conn_url=settings.db_url, table="assets",
                                check_token="market IN")
        _rebuild_table_if_check(conn_url=settings.db_url, table="transactions",
                                check_token="txn_type IN")

    # ---------------- 数据修复：vision.max_tokens 不能为 0 ----------------
    # 早期 UI 允许把 max_tokens 设成 0（错误地暗示"0 = 不限"），
    # 但多模态服务端把 0 解释为"立即停止输出"，导致 OCR 必然失败。
    # 这里启动时一次性把 0 / null / <1024 的值统一抬到 8192。
    _fix_vision_max_tokens()

    # ---------------- 数据修复：vision 性能字段补齐合理默认值 ----------------
    # 经过多轮迭代，vision 配置加了几个新字段（concurrency / stream / auto_fill_code）。
    # 早期数据库里存的 vision 记录是没有这些字段的，运行时 .get() 会拿到老逻辑的默认值
    # （concurrency=1，强制串行；stream=True，慢路径；等等），导致 OCR 速度极差。
    # 这里启动时一次性把缺失字段补齐，让旧用户也享受新默认值。
    _fix_vision_performance_defaults()


def _fix_vision_performance_defaults() -> None:
    """补齐 vision 配置里新增的性能字段。

    目标：让从老版本升级上来的用户不需要手动改设置，OCR 速度就能恢复。

    具体补什么：
      - concurrency 缺失或 < 1 → 设成 2（与 settings_service.DEFAULTS 一致）
      - stream 字段缺失 → 设成 False（非流式比流式快 50-100%）
      - auto_fill_code 字段缺失 → 设成 False（避免每张图阻塞查码 API）

    不会覆盖用户已有的非默认值（比如用户主动把 stream 设成 true，或 concurrency=4）。
    """
    import json as _json
    try:
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT value FROM app_settings WHERE key='vision'"
            )).fetchone()
            if not row:
                return
            try:
                cfg = _json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
            except Exception:
                return
            if not isinstance(cfg, dict):
                return

            changes: list[str] = []

            # concurrency：< 1 或缺失就置 2
            try:
                cur_conc = int(cfg.get("concurrency") or 0)
            except (TypeError, ValueError):
                cur_conc = 0
            if "concurrency" not in cfg or cur_conc < 1:
                cfg["concurrency"] = 2
                changes.append(f"concurrency→2（原值 {cur_conc}）")

            # stream：字段不存在 → 默认 False。如果用户已有显式值（不管 true/false），保留
            if "stream" not in cfg:
                cfg["stream"] = False
                changes.append("stream→false（恢复非流式快路径）")

            # auto_fill_code：字段不存在 → 默认 True（让 OCR 完成后就能看到代码）
            # 已有严格超时（单条 3s、整批 5s），不会明显拖慢主流程。
            # 如果用户之前手动设成 False，尊重用户显式选择不动。
            if "auto_fill_code" not in cfg:
                cfg["auto_fill_code"] = True
                changes.append("auto_fill_code→true（OCR 完自动查代码，已有 5s 硬超时）")

            # wall_timeout：单图总耗时硬上限。旧版本默认 90s，容易与用户在设置页填的
            # timeout（例如 NVIDIA NIM 排队时设 580s）不一致；默认跟随 timeout。
            try:
                cur_timeout = int(cfg.get("timeout") or 300)
            except (TypeError, ValueError):
                cur_timeout = 300
            try:
                cur_wall_timeout = int(cfg.get("wall_timeout") or 0)
            except (TypeError, ValueError):
                cur_wall_timeout = 0
            if "wall_timeout" not in cfg or (cur_wall_timeout <= 90 and cur_timeout > cur_wall_timeout):
                cfg["wall_timeout"] = cur_timeout
                changes.append(f"wall_timeout→{cur_timeout}s（跟随视觉 Timeout，避免 90s 提前放弃）")


            # content_hardcap：content 累积字符硬上限。字段不存在 → 20000。
            if "content_hardcap" not in cfg:
                cfg["content_hardcap"] = 20000
                changes.append("content_hardcap→20000（content 累积硬截断，防复读刷屏）")

            # force_stream：想用流式必须显式开启的第二道闸门
            # 默认 False：即便用户老配置 stream=True，没有 force_stream 也不走流式
            if "force_stream" not in cfg:
                cfg["force_stream"] = False
                # 老用户 stream=True 的情况：告知已切回非流式
                if cfg.get("stream", False):
                    changes.append(
                        "force_stream→false（检测到 stream=true 但未开 force_stream，"
                        "将走非流式安全路径；想要实时输出请在设置里把 force_stream 也打开）"
                    )
                else:
                    changes.append("force_stream→false（默认禁用流式，防死循环刷屏）")

            # rpm_limit：无限制 (0) 的老配置 → 自动改成保守的 20 RPM
            # 避免 9 张图并发 × 重试叠加触发 NIM 服务端 429 雪崩
            try:
                cur_rpm = int(cfg.get("rpm_limit") or 0)
            except (TypeError, ValueError):
                cur_rpm = 0
            if "rpm_limit" not in cfg or cur_rpm <= 0:
                cfg["rpm_limit"] = 20
                changes.append("rpm_limit→20（NIM 免费档保守值，防 429 雪崩；付费档可上调）")

            if changes:
                conn.execute(text(
                    "UPDATE app_settings SET value=:v WHERE key='vision'"
                ), {"v": _json.dumps(cfg, ensure_ascii=False)})
                print(f"[migration] 补齐 vision 性能默认值：{'，'.join(changes)}")
    except Exception as e:
        print(f"[migration] _fix_vision_performance_defaults 跳过：{type(e).__name__}: {e}")


def _fix_vision_max_tokens() -> None:
    """启动时把 settings.vision.max_tokens 从 0/None/过小值修复为 8192。"""
    import json as _json
    try:
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT value FROM app_settings WHERE key='vision'"
            )).fetchone()
            if not row:
                return
            try:
                cfg = _json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
            except Exception:
                return
            if not isinstance(cfg, dict):
                return
            mt = cfg.get("max_tokens")
            need_fix = False
            try:
                if mt is None or mt == "" or int(mt) < 1024:
                    need_fix = True
            except (TypeError, ValueError):
                need_fix = True
            if need_fix:
                old = mt
                cfg["max_tokens"] = 8192
                conn.execute(text(
                    "UPDATE app_settings SET value=:v WHERE key='vision'"
                ), {"v": _json.dumps(cfg, ensure_ascii=False)})
                print(f"[migration] 修复 vision.max_tokens: {old!r} → 8192（避免 OCR 截断）")
    except Exception as e:
        # 修复是 best-effort，失败不阻塞启动
        print(f"[migration] _fix_vision_max_tokens 跳过：{type(e).__name__}: {e}")


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
