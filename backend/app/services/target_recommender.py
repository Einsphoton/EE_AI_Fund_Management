"""AI target recommender for the watch-only target pool."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from .. import models
from ..agent.hermes import _get_openai_client, _parse_json
from ..agent.profiles import get_profile_prompt, get_profile_public
from ..logging_config import log_ai_event, safe_ai_config
from ..services import ai_guard, settings_service


from ..services.investment_manager import get_budget_status
from ..tz import now_local


SYSTEM_PROMPT = """
你是专业投资经理，请根据用户投资者性格、平台预算和已有资产，为“我的标的”观察池推荐或更新可跟踪资产。
只推荐场外基金 fund、股票 stock、ETF/场内基金 etf。
推荐必须符合 allowed_pairs 中的平台、币种和 asset_types 约束。
已有用户手动标的和已有持仓不能覆盖；已有 AI 推荐标的可以继续返回同一 code+market+platform，用最新理由更新它。
严格输出纯 JSON：
{
  "targets": [
    {"name":"名称", "code":"代码", "asset_type":"fund/stock/etf", "market":"OTC/A/HK/US", "platform":"购买平台", "reason":"加入或继续观察的理由，80字内"}
  ]
}
""".strip()


class TargetRecommendationError(RuntimeError):
    """Raised when AI target recommendation cannot run."""


def _to_enum(asset_type: str, market: str) -> tuple[models.AssetType, models.Market] | None:
    try:
        return models.AssetType(asset_type), models.Market(market)
    except ValueError:
        return None


def _currency_matches(market: str, currency: str) -> bool:
    market = (market or "").upper()
    currency = (currency or "").upper()
    return (
        (market in ("HK", "HKD") and currency == "HKD")
        or (market in ("US", "USD") and currency == "USD")
        or (market in ("A", "OTC", "CNY") and currency == "CNY")
    )


def _asset_key(asset: models.Asset) -> tuple[str, str, str]:
    return (asset.code.strip().upper(), asset.market.value, (asset.platform or "").strip())


def _is_ai_target(asset: models.Asset) -> bool:
    source = (getattr(asset, "target_source", "") or "").strip().lower()
    note = (asset.note or "").strip()
    return bool(asset.watch_only) and (source == "ai" or note.startswith("AI加入标的池") or note.startswith("AI推荐标的"))


async def recommend_ai_targets(db: Session, limit: int = 5, user_id: int | None = None) -> list[models.Asset]:

    """Recommend targets and upsert existing AI-created watch-only targets.

    Existing AI targets are refreshed by matching `code + market + platform`; manual targets
    and holdings are never overwritten.
    """
    ai_cfg = settings_service.get(db, "ai", user_id=user_id) or {}

    base_url = ai_cfg.get("base_url") or ""
    api_key = ai_cfg.get("api_key") or ""
    if not base_url or not api_key:
        raise TargetRecommendationError("请先配置 AI 大模型 base_url / api_key")

    profile_id = ai_cfg.get("investor_profile")
    profile_meta = get_profile_public(profile_id)
    profile_prompt = get_profile_prompt(profile_id) or get_profile_prompt(profile_meta.get("id"))
    budget_status = get_budget_status(db, user_id=user_id)

    allowed_pairs = [
        {
            "platform": b.get("platform"),
            "currency": b.get("currency"),
            "asset_types": b.get("asset_types") or [],
            "remaining_budget": b.get("remaining_budget", 0),
        }
        for b in budget_status
        if (b.get("remaining_budget") or 0) > 0
    ]
    if not allowed_pairs:
        raise TargetRecommendationError("请先在设置中配置有剩余额度的平台月投资预算")

    assets_q = db.query(models.Asset)
    if user_id is not None:
        assets_q = assets_q.filter(models.Asset.user_id == user_id)
    assets = assets_q.all()

    existing = [
        {
            "id": a.id,
            "name": a.name,
            "code": a.code,
            "asset_type": a.asset_type.value,
            "market": a.market.value,
            "platform": a.platform,
            "watch_only": a.watch_only,
            "target_source": getattr(a, "target_source", "manual") or "manual",
            "note": a.note,
        }
        for a in assets
    ]
    user_payload = {
        "limit": limit,
        "investor_profile": profile_meta,
        "investor_profile_prompt": profile_prompt,
        "allowed_pairs": allowed_pairs,
        "existing_assets_and_targets": existing,
        "existing_ai_targets": [x for x in existing if x.get("watch_only") and x.get("target_source") == "ai"],
        "instruction": "请返回本次应保留/新增/更新的 AI 推荐标的；已有 AI 标的若仍值得观察，请返回同一 code+market+platform 及最新 reason。",
    }

    try:
        timeout_sec = int(ai_cfg.get("timeout") or 180)
    except (TypeError, ValueError):
        timeout_sec = 180
    client = _get_openai_client(base_url, api_key, timeout_sec, ai_cfg)
    log_ai_event(
        "target_recommender",
        "target_recommend_start",
        config=safe_ai_config(ai_cfg),
        limit=limit,
        existing_count=len(existing),
        allowed_pair_count=len(allowed_pairs),
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    max_tokens = int(ai_cfg.get("max_tokens") or 4096)
    try:
        await ai_guard.acquire_ai_budget(
            "target_recommender",
            ai_cfg,
            key="ai",
            messages=messages,
            max_tokens=max_tokens,
        )
        resp = client.chat.completions.create(
            model=ai_cfg.get("model") or "deepseek-chat",
            messages=messages,
            temperature=float(ai_cfg.get("temperature", 0.4) or 0.4),
            max_tokens=max_tokens,
        )

        text = resp.choices[0].message.content if resp.choices else ""
        parsed = _parse_json(text or "") or {}
        log_ai_event(
            "target_recommender",
            "target_recommend_response",
            model=ai_cfg.get("model") or "deepseek-chat",
            text_len=len(text or ""),
            parsed=bool(parsed),
        )
    except Exception as e:
        await ai_guard.penalize_from_exception("target_recommender", ai_cfg, e, key="ai")
        log_ai_event(
            "target_recommender",
            "target_recommend_failed",
            level="error",
            config=safe_ai_config(ai_cfg),
            error_type=type(e).__name__,
            error=str(e),
        )
        raise TargetRecommendationError(f"AI 更新推荐标的失败：{type(e).__name__}: {e}") from e


    targets = parsed.get("targets") if isinstance(parsed, dict) else []

    if not isinstance(targets, list):
        targets = []

    existing_by_key = {_asset_key(a): a for a in assets}
    changed: list[models.Asset] = []
    now = now_local()
    for item in targets[:limit]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        code = str(item.get("code") or "").strip()
        asset_type = str(item.get("asset_type") or "").strip().lower()
        market = str(item.get("market") or "").strip().upper()
        platform = str(item.get("platform") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not name or not code or not platform or asset_type not in ("fund", "stock", "etf"):
            continue
        parsed_enum = _to_enum(asset_type, market)
        if parsed_enum is None:
            continue
        a_type, mkt = parsed_enum
        allowed = any(
            b.get("platform") == platform
            and asset_type in (b.get("asset_types") or [])
            and _currency_matches(mkt.value, str(b.get("currency") or ""))
            for b in allowed_pairs
        )
        if not allowed:
            continue

        key = (code.upper(), mkt.value, platform)
        note = f"AI推荐标的：{reason}" if reason else "AI推荐标的"
        existing_asset = existing_by_key.get(key)
        if existing_asset:
            if not _is_ai_target(existing_asset):
                continue
            existing_asset.name = name
            existing_asset.asset_type = a_type
            existing_asset.market = mkt
            existing_asset.platform = platform
            existing_asset.note = note
            existing_asset.watch_only = True
            existing_asset.target_source = "ai"
            existing_asset.updated_at = now
            changed.append(existing_asset)
            continue

        asset = models.Asset(
            user_id=user_id,
            name=name,

            code=code,
            asset_type=a_type,
            market=mkt,
            platform=platform,
            note=note,
            watch_only=True,
            target_source="ai",
        )
        db.add(asset)
        changed.append(asset)
        existing_by_key[key] = asset

    if changed:
        db.commit()
        for asset in changed:
            db.refresh(asset)
    return changed
