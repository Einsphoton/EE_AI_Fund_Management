"""OCR 导入 API：批量上传截图 → 视觉模型解析 → 候选匹配 → 用户确认 → 入库。

两段式接口：
  1) POST /api/import/ocr/parse    上传图片 + 平台提示，返回解析结果 + 现有资产候选匹配
     （此时不写库，给前端做编辑/对账）
  2) POST /api/import/ocr/commit   提交用户已确认的清单：批量创建 Asset / 追加 Transaction / 写 Snapshot
"""
from __future__ import annotations

import difflib
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..tz import now_local
from ..agent import vision as vision_agent
from ..services import snapshot_service

router = APIRouter(prefix="/api/import", tags=["import"])


# ============================================================
# /parse: 解析阶段（不入库）
# ============================================================

def _match_candidates(db: Session, item: dict, platform_hint: str) -> list[dict]:
    """对一条 OCR 结果，找现有资产候选（用于前端下拉）。

    优先级：
    1. code 完全匹配（同 code 不同平台也算候选，但分数低）
    2. name 模糊匹配（difflib ratio > 0.55）
    """
    name = (item.get("name") or "").strip()
    code = (item.get("code") or "").strip()
    candidates: list[tuple[float, models.Asset]] = []

    if code:
        for a in db.query(models.Asset).filter(models.Asset.code == code).all():
            same_platform = (a.platform or "") == (platform_hint or "")
            score = 1.0 if same_platform else 0.85
            candidates.append((score, a))

    if name:
        # 在所有 asset 上做名字模糊匹配（小项目几十个 asset，全表扫无所谓）
        all_assets = db.query(models.Asset).all()
        for a in all_assets:
            if any(c[1].id == a.id for c in candidates):
                continue
            ratio = difflib.SequenceMatcher(None, name, a.name).ratio()
            if ratio >= 0.55:
                candidates.append((ratio, a))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "asset_id": a.id,
            "name": a.name,
            "code": a.code,
            "asset_type": a.asset_type.value,
            "platform": a.platform,
            "match_score": round(score, 2),
        }
        for score, a in candidates[:5]
    ]


def _suggest_action(item: dict, top_candidate: Optional[dict], db: Session) -> dict:
    """根据 OCR 结果与候选资产，给一个建议动作。

    返回：
    {
      "action": "create" | "append_buy" | "append_sell" | "skip" | "update_field",
      "delta_shares": <差额（追加/减仓）>,
      "delta_amount": <差额（货基/理财）>,
      "reason": "<人话解释>"
    }
    """
    if not top_candidate:
        return {"action": "create", "reason": "未匹配到现有资产，建议新建"}

    asset_id = top_candidate["asset_id"]
    asset = db.get(models.Asset, asset_id)
    if not asset:
        return {"action": "create", "reason": "候选已不存在，建议新建"}

    asset_type = (item.get("asset_type") or "").lower()

    # 货基/理财/现金：用 amount 比对
    if asset_type in ("money_fund", "wealth", "cash", "bond"):
        ocr_amount = float(item.get("amount") or item.get("market_value") or 0.0)
        cur_amount = float(asset.principal_amount or 0.0)
        diff = ocr_amount - cur_amount
        if abs(diff) < 1.0:  # 1 元以内当无变化
            return {"action": "skip", "reason": f"金额无变化（{cur_amount:.2f}）"}
        return {
            "action": "update_field",
            "delta_amount": round(diff, 2),
            "reason": f"本金从 {cur_amount:.2f} 变为 {ocr_amount:.2f}（差 {diff:+.2f}）",
        }

    # 基金/股票/ETF：用份额比对最近 snapshot 或当前持仓
    last_snap = snapshot_service.latest_snapshot(db, asset_id)
    if last_snap and last_snap.shares is not None:
        baseline = last_snap.shares
    else:
        # 没有快照，用 transactions 算当前份额
        from ..services import holdings as holding_service
        baseline = holding_service.summarize(asset, current_price=None).get("total_shares") or 0.0

    ocr_shares = float(item.get("shares") or 0.0)
    diff = ocr_shares - baseline

    # 0.001 份以内视为无变化
    if abs(diff) < 0.001:
        return {"action": "skip", "reason": f"份额无变化（{baseline:.4f}）"}
    if diff > 0:
        return {
            "action": "append_buy",
            "delta_shares": round(diff, 4),
            "reason": f"份额从 {baseline:.4f} → {ocr_shares:.4f}（追加 {diff:+.4f}）",
        }
    return {
        "action": "append_sell",
        "delta_shares": round(-diff, 4),
        "reason": f"份额从 {baseline:.4f} → {ocr_shares:.4f}（减仓 {-diff:+.4f}）",
    }


@router.post("/ocr/parse")
async def parse_screenshots(
    files: list[UploadFile] = File(..., description="持仓页截图（支持多张）"),
    platform_hint: str = Form("", description="平台提示，例如 微信理财通 / 招商银行 / 富途"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """上传 N 张截图，逐张走视觉模型解析，返回每张图的 items + 匹配候选 + 建议动作。

    不入库；前端拿这份结果做对账，再调 /commit 真正写入。
    """
    if not files:
        raise HTTPException(400, "至少上传一张截图")

    images: list[tuple[bytes, str, str]] = []
    file_names: list[str] = []
    for f in files:
        b = await f.read()
        if not b:
            continue
        mime = f.content_type or "image/jpeg"
        images.append((b, mime, platform_hint))
        file_names.append(f.filename or "unknown.jpg")

    raw_results = await vision_agent.parse_images_concurrently(db, images)

    # 给每条 item 附上候选与建议
    out: list[dict] = []
    for i, r in enumerate(raw_results):
        items = r.get("items") or []
        for it in items:
            cands = _match_candidates(db, it, r.get("platform") or platform_hint)
            top = cands[0] if cands else None
            suggestion = _suggest_action(it, top, db)
            it["_candidates"] = cands
            it["_suggestion"] = suggestion
        out.append({
            "file": file_names[i] if i < len(file_names) else "",
            "platform": r.get("platform"),
            "screenshot_date": r.get("screenshot_date"),
            "items": items,
            "error": r.get("error"),
        })
    return {"results": out, "total": sum(len(r["items"]) for r in out)}


# ============================================================
# /commit: 提交阶段（事务性入库）
# ============================================================

class CommitItem(BaseModel):
    """前端提交的单条决策（已经过用户编辑）。"""
    action: str                          # create / append_buy / append_sell / update_field / skip
    asset_id: Optional[int] = None       # 追加/减仓/更新时必填
    # 资产元信息（创建时必填；追加时可选，会更新现有 asset 的可选字段）
    name: Optional[str] = None
    code: Optional[str] = None
    asset_type: Optional[str] = None
    market: Optional[str] = "OTC"
    platform: Optional[str] = ""
    note: Optional[str] = ""
    # 新建/扩展字段
    yield_7d: Optional[float] = None
    expected_apr: Optional[float] = None
    start_date: Optional[datetime] = None
    maturity_date: Optional[datetime] = None
    principal_amount: Optional[float] = None
    is_principal_guaranteed: Optional[bool] = True
    # 交易/快照数据
    shares: Optional[float] = None       # OCR 当前持有份额
    delta_shares: Optional[float] = None # 追加/减仓的份额差
    delta_amount: Optional[float] = None # 货基/理财本金差
    avg_cost: Optional[float] = None
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    profit: Optional[float] = None
    profit_pct: Optional[float] = None
    snapshot_date: Optional[datetime] = None
    raw: Optional[dict] = None


class CommitRequest(BaseModel):
    items: list[CommitItem]


@router.post("/ocr/commit")
def commit_decisions(
    payload: CommitRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """事务性写入用户确认后的导入决策。"""
    created = 0
    appended = 0
    skipped = 0
    errors: list[str] = []

    try:
        for idx, it in enumerate(payload.items):
            try:
                if it.action == "skip":
                    skipped += 1
                    continue

                if it.action == "create":
                    if not it.name or not it.code or not it.asset_type:
                        errors.append(f"#{idx} 创建失败：缺少 name/code/asset_type")
                        continue
                    try:
                        a_enum = models.AssetType(it.asset_type)
                    except ValueError:
                        errors.append(f"#{idx} 未知 asset_type: {it.asset_type}")
                        continue
                    try:
                        m_enum = models.Market(it.market or "OTC")
                    except ValueError:
                        m_enum = models.Market.otc
                    asset = models.Asset(
                        name=it.name, code=it.code, asset_type=a_enum, market=m_enum,
                        platform=it.platform or "", note=it.note or "",
                        yield_7d=it.yield_7d, expected_apr=it.expected_apr,
                        start_date=it.start_date, maturity_date=it.maturity_date,
                        principal_amount=it.principal_amount,
                        is_principal_guaranteed=it.is_principal_guaranteed if it.is_principal_guaranteed is not None else True,
                    )
                    db.add(asset)
                    db.flush()
                    asset_id = asset.id

                    # 行情类资产：如果有 shares + avg_cost，建一笔初始买入交易
                    if a_enum.value in ("fund", "stock", "etf") and it.shares and it.avg_cost:
                        db.add(models.Transaction(
                            asset_id=asset_id, txn_type=models.TxnType.buy,
                            shares=it.shares, price=it.avg_cost,
                            amount=(it.shares or 0) * (it.avg_cost or 0),
                            fee=0.0,
                            trade_date=it.snapshot_date or now_local(),
                            note="OCR 导入·初始买入",
                        ))
                    created += 1

                elif it.action in ("append_buy", "append_sell"):
                    if not it.asset_id:
                        errors.append(f"#{idx} 追加失败：缺少 asset_id")
                        continue
                    asset = db.get(models.Asset, it.asset_id)
                    if not asset:
                        errors.append(f"#{idx} 资产 #{it.asset_id} 不存在")
                        continue
                    delta = abs(it.delta_shares or 0)
                    if delta <= 0:
                        skipped += 1
                        continue
                    txn_type = models.TxnType.buy if it.action == "append_buy" else models.TxnType.sell
                    price = it.current_price or it.avg_cost or 0.0
                    db.add(models.Transaction(
                        asset_id=asset.id, txn_type=txn_type,
                        shares=delta, price=price,
                        amount=delta * price,
                        fee=0.0,
                        trade_date=it.snapshot_date or now_local(),
                        note=f"OCR 导入·{'追加' if txn_type == models.TxnType.buy else '减仓'}",
                    ))
                    if asset.watch_only:
                        asset.watch_only = False
                    appended += 1

                elif it.action == "update_field":
                    # 仅货基/理财/现金/债券：直接更新 principal_amount + yield/apr 等
                    if not it.asset_id:
                        errors.append(f"#{idx} 更新失败：缺少 asset_id")
                        continue
                    asset = db.get(models.Asset, it.asset_id)
                    if not asset:
                        errors.append(f"#{idx} 资产 #{it.asset_id} 不存在")
                        continue
                    if it.principal_amount is not None:
                        asset.principal_amount = it.principal_amount
                    elif it.delta_amount is not None:
                        asset.principal_amount = float(asset.principal_amount or 0) + it.delta_amount
                    if it.yield_7d is not None:
                        asset.yield_7d = it.yield_7d
                    if it.expected_apr is not None:
                        asset.expected_apr = it.expected_apr
                    if it.maturity_date is not None:
                        asset.maturity_date = it.maturity_date
                    appended += 1

                else:
                    errors.append(f"#{idx} 未知 action: {it.action}")
                    continue

                # 任何写入操作后都打一份 snapshot（追溯用）
                if it.action != "skip":
                    target_asset_id = it.asset_id or (asset.id if 'asset' in locals() else None)
                    if target_asset_id:
                        snapshot_service.create_snapshot(
                            db, target_asset_id,
                            shares=it.shares or 0.0,
                            avg_cost=it.avg_cost,
                            market_value=it.market_value,
                            profit=it.profit,
                            profit_pct=it.profit_pct,
                            source="ocr",
                            snapshot_date=it.snapshot_date,
                            raw=it.raw or {},
                            note=f"OCR 导入·{it.action}",
                        )
            except Exception as e:
                errors.append(f"#{idx} 处理异常：{type(e).__name__}: {str(e)[:120]}")

        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"提交失败：{e}")

    return {"created": created, "appended": appended, "skipped": skipped, "errors": errors}
