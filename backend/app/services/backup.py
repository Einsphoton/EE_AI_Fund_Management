"""数据导入导出服务。

支持的格式：
- **JSON 完整备份**（推荐）：
  包含 assets + transactions + snapshots 三张表的完整结构化数据，
  带版本号（schema_version）+ 导出时间，跨机恢复无损。
- **CSV 资产明细**：扁平格式，适合 Excel 查看。只有 asset 基础字段，
  交易和快照需单独导出（各自一个 CSV）。

合并策略：
- `merge`（默认）：按 `(asset_type, code)` 键；存在则补缺字段，不覆盖非空；
  交易/快照按 (trade_date, shares, price) 去重后追加
- `replace`：清空所有业务表再全量导入（危险，需 confirm）
- `skip`：只新增，已存在的完全跳过
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .. import models
from ..tz import now_local


# 备份文件格式版本：schema 有破坏性修改时 +1，导入时做兼容处理
SCHEMA_VERSION = 1


# ============================================================
# 序列化工具
# ============================================================

def _dt_to_iso(v: Any) -> str | None:
    """DateTime → ISO8601 字符串；None 原样返回。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def _iso_to_dt(v: Any) -> datetime | None:
    """ISO8601 → DateTime；容错 'YYYY-MM-DD' / 带 Z / None。"""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _asset_to_dict(a: models.Asset) -> dict:
    return {
        "name": a.name,
        "code": a.code,
        "asset_type": a.asset_type.value,
        "market": a.market.value,
        "platform": a.platform or "",
        "note": a.note or "",
        "watch_only": bool(a.watch_only),
        "target_source": a.target_source or "manual",
        "yield_7d": a.yield_7d,

        "expected_apr": a.expected_apr,
        "start_date": _dt_to_iso(a.start_date),
        "maturity_date": _dt_to_iso(a.maturity_date),
        "principal_amount": a.principal_amount,
        "is_principal_guaranteed": bool(a.is_principal_guaranteed) if a.is_principal_guaranteed is not None else True,
        "created_at": _dt_to_iso(a.created_at),
        "updated_at": _dt_to_iso(a.updated_at),
    }


def _txn_to_dict(t: models.Transaction) -> dict:
    return {
        "txn_type": t.txn_type.value,
        "shares": t.shares,
        "price": t.price,
        "amount": t.amount,
        "fee": t.fee,
        "trade_date": _dt_to_iso(t.trade_date),
        "note": t.note or "",
    }


def _snap_to_dict(s: models.HoldingSnapshot) -> dict:
    return {
        "source": s.source or "manual",
        "snapshot_date": _dt_to_iso(s.snapshot_date),
        "shares": s.shares,
        "avg_cost": s.avg_cost,
        "market_value": s.market_value,
        "profit": s.profit,
        "profit_pct": s.profit_pct,
        "raw": s.raw or {},
        "note": s.note or "",
    }


# ============================================================
# 导出
# ============================================================

def _setting_to_dict(s: models.AppSetting) -> dict:
    return {
        "key": s.key,
        "value": s.value,
        "updated_at": _dt_to_iso(s.updated_at),
    }


def _skill_to_dict(s: models.Skill) -> dict:
    return {
        "skill_id": s.skill_id,
        "name": s.name,
        "description": s.description or "",
        "category": s.category or "finance",
        "source": s.source or "builtin",
        "enabled": bool(s.enabled),
        "config": s.config or {},
        "installed_at": _dt_to_iso(s.installed_at),
    }


def export_json(db: Session, *, include_snapshots: bool = True, include_settings: bool = False, user_id: int | None = None) -> dict:

    """导出所有资产 + 交易 + 可选快照为一份 JSON 文档。


    结构：
    ```
    {
      "schema_version": 1,
      "exported_at": "2026-05-09T20:50:00+08:00",
      "assets": [
        {
          "name": "...", "code": "...", ...,
          "transactions": [{...}, ...],
          "snapshots": [{...}, ...]  # 可选
        },
        ...
      ],
      "stats": {"assets": 12, "transactions": 35, "snapshots": 58}
    }
    ```
    """
    assets_q = db.query(models.Asset)
    if user_id is not None:
        assets_q = assets_q.filter(models.Asset.user_id == user_id)
    assets = assets_q.order_by(models.Asset.id).all()

    out_assets: list[dict] = []
    total_txns = 0
    total_snaps = 0
    for a in assets:
        d = _asset_to_dict(a)
        d["transactions"] = [_txn_to_dict(t) for t in a.transactions]
        total_txns += len(d["transactions"])
        if include_snapshots:
            d["snapshots"] = [_snap_to_dict(s) for s in a.snapshots]
            total_snaps += len(d["snapshots"])
        out_assets.append(d)
    out = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": _dt_to_iso(now_local()),
        "assets": out_assets,
        "stats": {
            "assets": len(out_assets),
            "transactions": total_txns,
            "snapshots": total_snaps,
            "settings": 0,
            "skills": 0,
        },
    }
    if include_settings:
        settings_q = db.query(models.AppSetting)
        if user_id is not None:
            settings_q = settings_q.filter(models.AppSetting.key.startswith(f"u:{user_id}:"))
        else:
            settings_q = settings_q.filter(~models.AppSetting.key.startswith("u:"))
        settings = settings_q.order_by(models.AppSetting.key).all()

        skills = db.query(models.Skill).order_by(models.Skill.id).all()
        out["app_settings"] = [_setting_to_dict(s) for s in settings]
        out["skills"] = [_skill_to_dict(s) for s in skills]
        out["stats"]["settings"] = len(settings)
        out["stats"]["skills"] = len(skills)
    return out



def export_csv_assets(db: Session, user_id: int | None = None) -> str:

    """导出资产扁平表。交易请单独导 CSV。"""
    buf = io.StringIO()
    # utf-8-sig：让 Excel 打开 CSV 时中文不乱码（加 BOM）
    buf.write("\ufeff")
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow([
        "name", "code", "asset_type", "market", "platform", "note",
        "watch_only", "yield_7d_pct", "expected_apr_pct",
        "start_date", "maturity_date",
        "principal_amount", "is_principal_guaranteed",
        "created_at",
    ])
    assets_q = db.query(models.Asset)
    if user_id is not None:
        assets_q = assets_q.filter(models.Asset.user_id == user_id)
    for a in assets_q.order_by(models.Asset.id).all():

        w.writerow([
            a.name, a.code, a.asset_type.value, a.market.value,
            a.platform or "", a.note or "",
            "1" if a.watch_only else "0",
            a.yield_7d if a.yield_7d is not None else "",
            a.expected_apr if a.expected_apr is not None else "",
            _dt_to_iso(a.start_date) or "",
            _dt_to_iso(a.maturity_date) or "",
            a.principal_amount if a.principal_amount is not None else "",
            "1" if a.is_principal_guaranteed else "0",
            _dt_to_iso(a.created_at) or "",
        ])
    return buf.getvalue()


def export_csv_transactions(db: Session, user_id: int | None = None) -> str:

    """导出所有交易流水扁平表。"""
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow([
        "asset_name", "asset_code", "asset_type",
        "txn_type", "shares", "price", "amount", "fee",
        "trade_date", "note",
    ])
    txns_q = (
        db.query(models.Transaction)
        .join(models.Asset, models.Transaction.asset_id == models.Asset.id)
    )
    if user_id is not None:
        txns_q = txns_q.filter(models.Asset.user_id == user_id)
    txns = txns_q.order_by(models.Transaction.trade_date.asc()).all()

    for t in txns:
        a = t.asset
        w.writerow([
            a.name, a.code, a.asset_type.value,
            t.txn_type.value,
            t.shares, t.price, t.amount, t.fee,
            _dt_to_iso(t.trade_date) or "",
            t.note or "",
        ])
    return buf.getvalue()


# ============================================================
# 导入
# ============================================================

class ImportResult:
    def __init__(self):
        self.assets_created = 0
        self.assets_updated = 0
        self.assets_skipped = 0
        self.transactions_added = 0
        self.snapshots_added = 0
        self.settings_imported = 0
        self.skills_imported = 0
        self.errors: list[str] = []
        self.replaced_counts: dict = {}

    def to_dict(self) -> dict:
        return {
            "ok": True,
            "assets_created": self.assets_created,
            "assets_updated": self.assets_updated,
            "assets_skipped": self.assets_skipped,
            "transactions_added": self.transactions_added,
            "snapshots_added": self.snapshots_added,
            "settings_imported": self.settings_imported,
            "skills_imported": self.skills_imported,
            "errors": self.errors,
            "replaced_counts": self.replaced_counts,
        }



def _find_existing_asset(db: Session, asset_type: str, code: str, user_id: int | None = None) -> models.Asset | None:

    """按 (asset_type, code) 组合查找已存在的资产。code 为空时返回 None。"""
    if not code or not code.strip():
        return None
    try:
        at = models.AssetType(asset_type)
    except ValueError:
        return None
    q = db.query(models.Asset).filter(
        models.Asset.asset_type == at,
        models.Asset.code == code.strip(),
    )
    if user_id is not None:
        q = q.filter(models.Asset.user_id == user_id)
    return q.first()



def _txn_dedupe_key(t_dict: dict) -> tuple:
    """交易去重键：(trade_date[:10], txn_type, shares, price)。

    精度足够：同一天、同方向、同份额、同价格的交易几乎一定是同一笔。
    """
    date_str = (t_dict.get("trade_date") or "")[:10]
    return (
        date_str,
        t_dict.get("txn_type") or "buy",
        round(float(t_dict.get("shares") or 0), 4),
        round(float(t_dict.get("price") or 0), 4),
    )


def _snap_dedupe_key(s_dict: dict) -> tuple:
    date_str = (s_dict.get("snapshot_date") or "")[:10]
    return (
        date_str,
        round(float(s_dict.get("shares") or 0), 4),
        round(float(s_dict.get("market_value") or 0), 2),
    )


def _import_settings(db: Session, payload: dict, result: ImportResult, user_id: int | None = None) -> None:

    items = payload.get("app_settings") or []
    if not isinstance(items, list):
        result.errors.append("app_settings 结构非法，已跳过")
        return
    for item in items:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        raw_key = str(item.get("key"))
        if user_id is not None and raw_key.startswith(f"u:{user_id}:"):
            key = raw_key
        elif user_id is not None:
            key = f"u:{user_id}:{raw_key.split(':', 2)[-1] if raw_key.startswith('u:') else raw_key}"
        else:
            key = raw_key

        setting = db.get(models.AppSetting, key)
        if setting is None:
            db.add(models.AppSetting(
                key=key,
                value=item.get("value") if item.get("value") is not None else {},
                updated_at=_iso_to_dt(item.get("updated_at")) or now_local(),
            ))
        else:
            setting.value = item.get("value") if item.get("value") is not None else {}
            setting.updated_at = _iso_to_dt(item.get("updated_at")) or now_local()
        result.settings_imported += 1


def _import_skills(db: Session, payload: dict, result: ImportResult) -> None:
    items = payload.get("skills") or []
    if not isinstance(items, list):
        result.errors.append("skills 结构非法，已跳过")
        return
    for item in items:
        if not isinstance(item, dict) or not item.get("skill_id"):
            continue
        skill_id = str(item.get("skill_id"))
        skill = db.query(models.Skill).filter(models.Skill.skill_id == skill_id).first()
        data = {
            "name": item.get("name") or skill_id,
            "description": item.get("description") or "",
            "category": item.get("category") or "finance",
            "source": item.get("source") or "import",
            "enabled": bool(item.get("enabled", True)),
            "config": item.get("config") or {},
            "installed_at": _iso_to_dt(item.get("installed_at")) or now_local(),
        }
        if skill is None:
            db.add(models.Skill(skill_id=skill_id, **data))
        else:
            for k, v in data.items():
                setattr(skill, k, v)
        result.skills_imported += 1


def import_json(

    db: Session,
    payload: dict,
    *,
    mode: str = "merge",
    include_transactions: bool = True,
    include_snapshots: bool = True,
    include_settings: bool = False,
    user_id: int | None = None,
) -> ImportResult:


    """从 JSON 文档导入。

    mode:
      - 'merge'（默认）：按 (asset_type, code) 合并；已存在只补缺字段；
        交易/快照去重追加（同日同方向同份额同价 视为同一条）
      - 'replace'：先清空所有业务表再全量导入（极度危险，调用方要二次确认）
      - 'skip'：已存在就完全跳过（交易/快照也不追加）
    """
    result = ImportResult()

    if not isinstance(payload, dict) or not isinstance(payload.get("assets"), list):
        result.errors.append("JSON 结构非法：缺 assets 数组")
        return result

    # ── replace 模式：先清空 ──
    if mode == "replace":
        from sqlalchemy import text
        try:
            before = {
                "transactions": db.query(models.Transaction).join(models.Asset, models.Transaction.asset_id == models.Asset.id).filter(models.Asset.user_id == user_id).count() if user_id is not None else db.query(models.Transaction).count(),
                "holding_snapshots": db.query(models.HoldingSnapshot).join(models.Asset, models.HoldingSnapshot.asset_id == models.Asset.id).filter(models.Asset.user_id == user_id).count() if user_id is not None else db.query(models.HoldingSnapshot).count(),
                "advices": db.query(models.Advice).join(models.Asset, models.Advice.asset_id == models.Asset.id).filter(models.Asset.user_id == user_id).count() if user_id is not None else db.query(models.Advice).count(),
                "todo_items": db.query(models.TodoItem).join(models.Asset, models.TodoItem.asset_id == models.Asset.id).filter(models.Asset.user_id == user_id).count() if user_id is not None else db.query(models.TodoItem).count(),
                "assets": db.query(models.Asset).filter(models.Asset.user_id == user_id).count() if user_id is not None else db.query(models.Asset).count(),

            }
            if include_settings:
                before["app_settings"] = db.query(models.AppSetting).count()
                before["skills"] = db.query(models.Skill).count()
            # 顺序重要：子表先清（虽然有 CASCADE，但保险起见）
            if user_id is not None:
                db.execute(text("DELETE FROM transactions WHERE asset_id IN (SELECT id FROM assets WHERE user_id=:uid)"), {"uid": user_id})
                db.execute(text("DELETE FROM holding_snapshots WHERE asset_id IN (SELECT id FROM assets WHERE user_id=:uid)"), {"uid": user_id})
                db.execute(text("DELETE FROM advices WHERE asset_id IN (SELECT id FROM assets WHERE user_id=:uid)"), {"uid": user_id})
                db.execute(text("DELETE FROM todo_items WHERE asset_id IN (SELECT id FROM assets WHERE user_id=:uid)"), {"uid": user_id})
                db.execute(text("DELETE FROM assets WHERE user_id=:uid"), {"uid": user_id})
            else:
                db.execute(text("DELETE FROM transactions"))
                db.execute(text("DELETE FROM holding_snapshots"))
                db.execute(text("DELETE FROM advices"))
                db.execute(text("DELETE FROM todo_items"))
                db.execute(text("DELETE FROM assets"))
            if include_settings:
                if user_id is not None:
                    db.execute(text("DELETE FROM app_settings WHERE key LIKE :prefix"), {"prefix": f"u:{user_id}:%"})
                else:
                    db.execute(text("DELETE FROM app_settings"))

                db.execute(text("DELETE FROM skills"))
            db.flush()

            result.replaced_counts = before
        except Exception as e:
            db.rollback()
            result.errors.append(f"清空失败：{type(e).__name__}: {e}")
            return result

    # ── 逐资产导入 ──
    for idx, a_data in enumerate(payload["assets"]):
        if not isinstance(a_data, dict):
            result.errors.append(f"#{idx} 不是对象，已跳过")
            continue
        try:
            _import_one_asset(
                db, a_data, mode, result,
                include_transactions=include_transactions,
                include_snapshots=include_snapshots,
                user_id=user_id,

            )
        except Exception as e:
            result.errors.append(
                f"#{idx}「{a_data.get('name', '?')}」导入失败：{type(e).__name__}: {str(e)[:150]}"
            )

    if include_settings:
        _import_settings(db, payload, result, user_id=user_id)

        _import_skills(db, payload, result)

    try:
        db.commit()
    except Exception as e:

        db.rollback()
        result.errors.append(f"提交事务失败：{type(e).__name__}: {e}")
    return result


# ─── asset 字段写入辅助：只在目标字段为空时填（用于 merge 模式）──
_ASSET_SCALAR_FIELDS = (
    "name", "platform", "note", "target_source",
    "yield_7d", "expected_apr",
    "principal_amount",
)

_ASSET_DATE_FIELDS = ("start_date", "maturity_date")
_ASSET_BOOL_FIELDS = ("watch_only", "is_principal_guaranteed")


def _apply_asset_fields(target: models.Asset, src: dict, *, only_if_empty: bool) -> bool:
    """把 src dict 的字段写到 target asset 上。

    only_if_empty=True（merge 模式）：仅当 target 该字段为空/None/空串时才覆盖。
    only_if_empty=False（新建模式）：全部写入。
    返回是否有任何字段被更新。
    """
    changed = False
    for f in _ASSET_SCALAR_FIELDS:
        if f not in src:
            continue
        cur = getattr(target, f, None)
        is_empty = cur is None or (isinstance(cur, str) and cur.strip() == "")
        if only_if_empty and not is_empty:
            continue
        new_val = src.get(f)
        if new_val is None or (isinstance(new_val, str) and new_val.strip() == ""):
            continue
        setattr(target, f, new_val)
        changed = True
    for f in _ASSET_DATE_FIELDS:
        if f not in src:
            continue
        cur = getattr(target, f, None)
        if only_if_empty and cur is not None:
            continue
        parsed = _iso_to_dt(src.get(f))
        if parsed is not None:
            setattr(target, f, parsed)
            changed = True
    for f in _ASSET_BOOL_FIELDS:
        if f not in src or src.get(f) is None:
            continue
        # bool 字段没有"empty"概念，merge 模式默认不覆盖
        if only_if_empty:
            continue
        setattr(target, f, bool(src.get(f)))
        changed = True
    return changed


def _import_one_asset(
    db: Session,
    a_data: dict,
    mode: str,
    result: ImportResult,
    *,
    include_transactions: bool,
    include_snapshots: bool,
    user_id: int | None = None,
) -> None:

    """导入一个 asset 及其子记录。"""
    name = (a_data.get("name") or "").strip()
    code = (a_data.get("code") or "").strip()
    asset_type = (a_data.get("asset_type") or "").strip()
    market_str = (a_data.get("market") or "OTC").strip()

    if not name or not asset_type:
        result.errors.append(f"缺 name 或 asset_type：{a_data.get('name')!r}")
        return

    try:
        at_enum = models.AssetType(asset_type)
    except ValueError:
        result.errors.append(f"未知 asset_type={asset_type}「{name}」")
        return
    try:
        m_enum = models.Market(market_str)
    except ValueError:
        m_enum = models.Market.otc

    existing = _find_existing_asset(db, asset_type, code, user_id=user_id) if mode != "replace" else None


    if existing is not None:
        if mode == "skip":
            result.assets_skipped += 1
            return
        # merge：只补缺字段
        _apply_asset_fields(existing, a_data, only_if_empty=True)
        result.assets_updated += 1
        target = existing
    else:
        # 新建 asset
        target = models.Asset(
            user_id=user_id,
            name=name,

            code=code or f"{asset_type}_imported_{abs(hash(name)) & 0xffffff:06x}",
            asset_type=at_enum,
            market=m_enum,
            platform=a_data.get("platform") or "",
            note=a_data.get("note") or "",
            watch_only=bool(a_data.get("watch_only", False)),
            target_source=a_data.get("target_source") or "manual",
            yield_7d=a_data.get("yield_7d"),

            expected_apr=a_data.get("expected_apr"),
            start_date=_iso_to_dt(a_data.get("start_date")),
            maturity_date=_iso_to_dt(a_data.get("maturity_date")),
            principal_amount=a_data.get("principal_amount"),
            is_principal_guaranteed=bool(a_data.get("is_principal_guaranteed", True)),
        )
        db.add(target)
        db.flush()  # 让 target.id 生效，供子表外键引用
        result.assets_created += 1

    # ── 导入子表：交易 ──
    if include_transactions:
        txns = a_data.get("transactions") or []
        if isinstance(txns, list) and txns:
            # 计算已有交易的去重键集合（仅对已存在的 asset 有意义）
            existing_keys: set = set()
            if existing is not None:
                for t in target.transactions:
                    existing_keys.add((
                        _dt_to_iso(t.trade_date)[:10] if t.trade_date else "",
                        t.txn_type.value,
                        round(t.shares or 0, 4),
                        round(t.price or 0, 4),
                    ))
            for t_data in txns:
                if not isinstance(t_data, dict):
                    continue
                k = _txn_dedupe_key(t_data)
                if k in existing_keys:
                    continue
                try:
                    ttype = models.TxnType(t_data.get("txn_type") or "buy")
                except ValueError:
                    ttype = models.TxnType.buy
                shares = float(t_data.get("shares") or 0)
                price = float(t_data.get("price") or 0)
                amount = float(t_data.get("amount") or (shares * price))
                db.add(models.Transaction(
                    asset_id=target.id,
                    txn_type=ttype,
                    shares=shares,
                    price=price,
                    amount=amount,
                    fee=float(t_data.get("fee") or 0),
                    trade_date=_iso_to_dt(t_data.get("trade_date")) or now_local(),
                    note=t_data.get("note") or "",
                ))
                existing_keys.add(k)
                result.transactions_added += 1

    # ── 导入子表：快照 ──
    if include_snapshots:
        snaps = a_data.get("snapshots") or []
        if isinstance(snaps, list) and snaps:
            existing_snap_keys: set = set()
            if existing is not None:
                for s in target.snapshots:
                    existing_snap_keys.add((
                        _dt_to_iso(s.snapshot_date)[:10] if s.snapshot_date else "",
                        round(s.shares or 0, 4),
                        round(s.market_value or 0, 2),
                    ))
            for s_data in snaps:
                if not isinstance(s_data, dict):
                    continue
                k = _snap_dedupe_key(s_data)
                if k in existing_snap_keys:
                    continue
                db.add(models.HoldingSnapshot(
                    asset_id=target.id,
                    source=s_data.get("source") or "import",
                    snapshot_date=_iso_to_dt(s_data.get("snapshot_date")) or now_local(),
                    shares=float(s_data.get("shares") or 0),
                    avg_cost=s_data.get("avg_cost"),
                    market_value=s_data.get("market_value"),
                    profit=s_data.get("profit"),
                    profit_pct=s_data.get("profit_pct"),
                    raw=s_data.get("raw") or {},
                    note=s_data.get("note") or "",
                ))
                existing_snap_keys.add(k)
                result.snapshots_added += 1
