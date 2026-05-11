"""管理类 API：高危操作，做了双重确认。

端点：
- GET  /api/admin/ping                    探活
- POST /api/admin/wipe-all                清空业务数据
- GET  /api/admin/export?format=json|csv  导出数据
- GET  /api/admin/export-csv-transactions 单独导交易 CSV
- POST /api/admin/import                  导入 JSON

设计要点：
- 走独立 prefix `/api/admin`，与业务 API 隔离
- 高危操作必须传 confirm 字符串（避免误调用 / CSRF 误触发）
- 导入支持三种合并策略：merge / replace / skip
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db, engine
from .. import scheduler as scheduler_mod
from ..services import backup as backup_service

router = APIRouter(prefix="/api/admin", tags=["admin"])


# 必填的 confirm 字符串，避免误触
_WIPE_CONFIRM = "I_UNDERSTAND_DELETE_EVERYTHING"

# 业务数据表（默认清理范围）
_BIZ_TABLES = ["transactions", "holding_snapshots", "advices", "todo_items", "assets"]
# 设置/扩展表（仅在 include_settings=True 时清）
_SETTINGS_TABLES = ["app_settings", "skills"]


def _table_rowcount(db: Session, table: str) -> int:
    """安全地数表行数。表不存在或失败返回 0。"""
    try:
        row = db.execute(text(f"SELECT COUNT(*) FROM {table}")).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _truncate_table(db: Session, table: str) -> int:
    """清空一张表并返回删除前的行数。

    SQLite 没有真正的 TRUNCATE，用 DELETE FROM 即可（速度对个人量级数据足够）。
    """
    before = _table_rowcount(db, table)
    if before == 0:
        return 0
    try:
        db.execute(text(f"DELETE FROM {table}"))
        return before
    except Exception:
        return 0


@router.get("/ping")
def admin_ping():
    """探活端点：浏览器访问 /api/admin/ping，返回 {ok:true} 即说明本路由模块已装载。

    用于排查"清空失败：Not Found"这类问题——如果连 ping 都 404，说明
    uvicorn 进程没拿到 admin.py 这个新文件，需要完全重启（不是 reload）。
    """
    return {"ok": True, "module": "admin", "wipe_endpoint": "/api/admin/wipe-all"}


@router.post("/wipe-all")
def wipe_all_data(
    confirm: str,
    include_settings: bool = False,
    db: Session = Depends(get_db),
):
    """**危险操作**：清空数据库里的业务数据。

    Parameters
    ----------
    confirm : str
        必须等于 `I_UNDERSTAND_DELETE_EVERYTHING`，否则拒绝。
    include_settings : bool
        False（默认）= 只清业务数据：资产、交易、持仓快照、AI 建议
        True = 还把 AppSettings（含 AI / 视觉模型配置 / API Key）和 Skills 也清掉

    返回
    ----
    `{"ok": True, "deleted": {表名: 行数}, "include_settings": ...}`
    """
    if confirm != _WIPE_CONFIRM:
        raise HTTPException(
            400,
            f"confirm 校验失败。需要传 confirm={_WIPE_CONFIRM}（这是高危操作的双重确认）",
        )

    # 先停掉定时器，避免清表过程中被调度器又写入
    try:
        scheduler_mod.shutdown()
    except Exception:
        pass

    deleted: dict[str, int] = {}
    targets = list(_BIZ_TABLES)
    if include_settings:
        targets.extend(_SETTINGS_TABLES)

    # 注意顺序：transactions / snapshots / advices 都依赖 assets，先清子表再清父表
    # 即便外键不是 ON DELETE CASCADE 也不会因外键约束失败
    try:
        for t in targets:
            deleted[t] = _truncate_table(db, t)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"清空过程出错：{type(e).__name__}: {e}")

    # SQLite VACUUM 把空间还给操作系统（让 .db 文件实际变小）
    try:
        with engine.begin() as conn:
            conn.execute(text("VACUUM"))
    except Exception:
        # VACUUM 失败不影响数据已清的事实
        pass

    # 重启调度器（即便清了 settings，scheduler 会读默认值正常拉起）
    try:
        scheduler_mod.start()
    except Exception:
        pass

    total = sum(deleted.values())
    return {
        "ok": True,
        "include_settings": include_settings,
        "total_rows_deleted": total,
        "deleted": deleted,
        "message": (
            f"已清空 {total} 行数据"
            + ("（含设置 / Skills 元数据）" if include_settings else "（仅业务数据；设置已保留）")
        ),
    }


# ============================================================
# 导出 / 导入
# ============================================================

@router.get("/export")
def export_data(
    format: str = "json",
    include_snapshots: bool = True,
    include_settings: bool = False,
    db: Session = Depends(get_db),
):
    """导出所有资产数据。


    format:
      - `json`（推荐）：完整备份，含 assets + transactions + snapshots，可直接用于恢复
      - `csv`: 资产扁平表（只含 asset 基础字段，不含交易/快照），适合 Excel 查看

    响应为文件下载：Content-Disposition 带 filename + 时间戳。
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if format == "json":
        data = backup_service.export_json(
            db,
            include_snapshots=include_snapshots,
            include_settings=include_settings,
        )
        body = json.dumps(data, ensure_ascii=False, indent=2)

        # 用 PlainTextResponse + JSON content-type；直接 JSONResponse 会被 FastAPI 去掉
        # indent 并压成一行，不适合用户人眼阅读/手改
        return PlainTextResponse(
            content=body,
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="fund_{"full_backup" if include_settings else "backup"}_{ts}.json"',
                "Cache-Control": "no-cache",
            },

        )
    elif format == "csv":
        body = backup_service.export_csv_assets(db)
        return PlainTextResponse(
            content=body,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="fund_assets_{ts}.csv"',
                "Cache-Control": "no-cache",
            },
        )
    else:
        raise HTTPException(400, f"不支持的格式: {format}（可选 json / csv）")


@router.get("/export/transactions.csv")
def export_transactions_csv(db: Session = Depends(get_db)):
    """单独导出交易流水 CSV（扁平，便于 Excel 筛选）。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    body = backup_service.export_csv_transactions(db)
    return PlainTextResponse(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="fund_transactions_{ts}.csv"',
            "Cache-Control": "no-cache",
        },
    )


# replace 模式需要 confirm 字符串，避免误调用清空库
_IMPORT_REPLACE_CONFIRM = "I_UNDERSTAND_REPLACE_ALL"


@router.post("/import")
async def import_data(
    file: UploadFile = File(..., description="导出的 JSON 备份文件"),
    mode: str = Form("merge", description="merge / replace / skip"),
    include_transactions: bool = Form(True),
    include_snapshots: bool = Form(True),
    include_settings: bool = Form(False),
    confirm: str = Form("", description="mode=replace 必填 I_UNDERSTAND_REPLACE_ALL"),
    db: Session = Depends(get_db),
):

    """从导出的 JSON 文件恢复资产数据。

    mode:
      - `merge`（默认，推荐）：按 (asset_type, code) 键合并。
        已存在的 asset 只补"空字段"，不会覆盖非空；
        交易/快照按 (日期, 类型, 份额, 价格) 去重追加
      - `replace`: 先清空所有业务表再全量导入。
        **必须传 confirm=I_UNDERSTAND_REPLACE_ALL**，否则拒绝
      - `skip`: 已存在的 asset 完全跳过（连交易快照都不追加），只新建缺少的

    返回统计：新建/更新/跳过资产数、追加交易/快照数、错误列表等。
    """
    if mode not in ("merge", "replace", "skip"):
        raise HTTPException(400, f"mode 必须是 merge/replace/skip，收到 {mode}")
    if mode == "replace" and confirm != _IMPORT_REPLACE_CONFIRM:
        raise HTTPException(
            400,
            f"replace 模式需要传 confirm={_IMPORT_REPLACE_CONFIRM}（这会清空现有所有数据）",
        )

    # 读取文件
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "上传文件为空")
    # 容错 BOM
    try:
        text_body = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text_body = raw.decode("gbk")
        except UnicodeDecodeError:
            raise HTTPException(400, "文件不是 UTF-8/GBK 文本，无法解析")

    try:
        payload = json.loads(text_body)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON 解析失败：{e.msg}（行 {e.lineno} 列 {e.colno}）")

    if not isinstance(payload, dict):
        raise HTTPException(400, "JSON 顶层必须是 object（{\"assets\":[...]}）")

    result = backup_service.import_json(
        db, payload,
        mode=mode,
        include_transactions=include_transactions,
        include_snapshots=include_snapshots,
        include_settings=include_settings,
    )


    return JSONResponse(content=result.to_dict())
