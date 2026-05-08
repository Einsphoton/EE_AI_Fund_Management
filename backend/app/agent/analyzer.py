"""Run analysis on assets and persist Advice records."""
from __future__ import annotations

import asyncio
import secrets
from typing import Any, AsyncIterator, Iterable, Optional

from sqlalchemy.orm import Session

from .. import models
from ..database import SessionLocal
from ..services import quotes as quotes_service
from ..services import holdings as holding_service
from ..services import settings_service, skills_service
from ..tz import now_local
from .hermes import run_agent


def new_batch_id() -> str:
    """生成一个可读 + 随机的 batch ID：`YYYYMMDDHHMMSS_<4hex>`。"""
    ts = now_local().strftime("%Y%m%d%H%M%S")
    return f"{ts}_{secrets.token_hex(2)}"


# ---------------------------------------------------------------------------
# 批内共享上下文：避免每个标的都重复查 Skill、重复读 settings.ai。
# ---------------------------------------------------------------------------
def _load_batch_context(db: Session) -> dict[str, Any]:
    skills = db.query(models.Skill).filter_by(enabled=True).all()
    skill_prompts = [skills_service.get_skill_prompt(s.skill_id) for s in skills]
    skill_label = ",".join(s.skill_id for s in skills) or "default"
    ai_cfg = settings_service.get(db, "ai") or {}
    return {
        "skill_prompts": skill_prompts,
        "skill_label": skill_label,
        "ai_cfg": ai_cfg,
    }


async def _analyze_one_core(
    db: Session,
    asset: models.Asset,
    batch_id: str,
    source: str,
    ctx: dict[str, Any],
) -> models.Advice:
    """实际分析逻辑：拉行情 -> 调 LLM -> 落库。"""
    quote = await quotes_service.fetch_quote(
        asset.asset_type.value, asset.market.value, asset.code, days=180,
    )
    points = quote.get("points") or []
    current = quote.get("current_price")
    holding = holding_service.summarize(asset, current)

    asset_dict = {
        "name": asset.name, "code": asset.code,
        "asset_type": asset.asset_type.value, "market": asset.market.value,
        "platform": asset.platform, "watch_only": asset.watch_only,
    }
    # 在线程池中执行同步的 OpenAI 调用，避免阻塞事件循环
    result = await asyncio.to_thread(
        run_agent,
        asset_dict, points, holding,
        ctx["skill_prompts"], ctx["ai_cfg"], ctx["skill_label"],
    )

    extra_keys = ("score", "fundamentals", "macro", "micro", "risks", "pros",
                  "advice", "commentary", "time_horizon", "target_price", "stop_loss")
    extra = {k: result.get(k) for k in extra_keys if k in result}
    advice = models.Advice(
        asset_id=asset.id,
        batch_id=batch_id,
        source=source,
        action=result.get("action", "hold"),
        confidence=float(result.get("confidence", 0.5)),
        summary=result.get("summary", ""),
        detail=result.get("detail", ""),
        extra=extra,
        skill_used=result.get("skill_used", ctx["skill_label"]),
    )
    db.add(advice)
    db.commit()
    db.refresh(advice)
    return advice


async def analyze_one(
    db: Session,
    asset: models.Asset,
    batch_id: Optional[str] = None,
    source: str = "single",
) -> models.Advice:
    """分析单个标的。

    - 默认 source="single"：手动触发（如详情页"立即分析")，结果只在该标的详情页展示
    - source="batch"：由批量分析流程调用，结果会出现在全局"AI 建议"页
    """
    batch_id = batch_id or new_batch_id()
    ctx = _load_batch_context(db)
    return await _analyze_one_core(db, asset, batch_id, source, ctx)


async def analyze_all(batch_id: Optional[str] = None) -> int:
    """供调度器调用：分析所有标的（含 watch-only），统一 source=batch。

    使用 settings.ai.batch_concurrency 控制并发度（默认 4）。
    """
    batch_id = batch_id or new_batch_id()
    db = SessionLocal()
    try:
        ctx = _load_batch_context(db)
        concurrency = _resolve_concurrency(ctx["ai_cfg"])
        assets: Iterable[models.Asset] = db.query(models.Asset).all()
        asset_list = list(assets)
        if not asset_list:
            return 0

        sem = asyncio.Semaphore(concurrency)
        analyzed = 0

        async def _run(asset_id: int):
            nonlocal analyzed
            async with sem:
                _db = SessionLocal()
                try:
                    a = _db.get(models.Asset, asset_id)
                    if a is None:
                        return
                    await _analyze_one_core(_db, a, batch_id, "batch", ctx)
                    analyzed += 1
                except Exception as e:  # pragma: no cover
                    print(f"[analyzer] asset_id={asset_id} failed: {e}")
                finally:
                    _db.close()

        await asyncio.gather(*(_run(a.id) for a in asset_list))
        return analyzed
    finally:
        db.close()


def _resolve_concurrency(ai_cfg: dict[str, Any]) -> int:
    """从 ai 配置中读出并发度，做合法性夹断（1-16）。

    默认 1（串行）：reasoning 模型（R1/Qwen3-thinking 等）单次请求就需要 60-90 秒，
    且常经 Cloudflare（120s 超时硬限）。并发请求会让后端排队累积，整体反而更慢，
    并且普遍触发 524 超时。
    如果你用的是非 reasoning 模型（普通 Chat）+ 内网直连，可以手动调到 4-8。
    """
    try:
        n = int((ai_cfg or {}).get("batch_concurrency") or 1)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(16, n))


# ---- 流式版：并发执行，谁先完成谁先向调用方推送事件 ----
async def analyze_all_stream() -> AsyncIterator[dict]:
    """分析所有标的并流式产出进度事件（并发版）。

    事件类型：
    - {"type":"start", "batch_id":..., "total":N, "concurrency":C,
       "assets":[{id,name,code,market}...]}
    - {"type":"asset_start", "asset_id":..., "name":..., "index":i, "total":N}
    - {"type":"log", "text":"...", "asset_id":..., "name":...}
    - {"type":"asset_done", "asset_id":..., "name":..., "index":i,
         "action":..., "confidence":..., "summary":..., "advice_id":..., "skill_used":...}
    - {"type":"asset_error", "asset_id":..., "name":..., "error":...}
    - {"type":"done", "batch_id":..., "analyzed":N, "failed":M}

    备注：由于任务并发执行，`index` 字段表示"该标的在初始列表中的原始序号"，
    而非"第几个完成的"——前端可以据此稳定定位 asset。完成计数由 `asset_done.done_count`
    给出（若前端需要）。
    """
    batch_id = new_batch_id()
    main_db = SessionLocal()
    try:
        ctx = _load_batch_context(main_db)
        concurrency = _resolve_concurrency(ctx["ai_cfg"])

        assets = main_db.query(models.Asset).all()
        total = len(assets)
        yield {
            "type": "start",
            "batch_id": batch_id,
            "total": total,
            "concurrency": concurrency,
            "assets": [
                {"id": a.id, "name": a.name, "code": a.code, "market": a.market.value}
                for a in assets
            ],
        }
        if total == 0:
            yield {"type": "done", "batch_id": batch_id, "analyzed": 0, "failed": 0}
            return

        # 通过 asyncio.Queue 汇聚各 worker 的事件，保证"谁先完成谁先推"
        queue: asyncio.Queue[dict] = asyncio.Queue()
        sem = asyncio.Semaphore(concurrency)

        async def _worker(asset_id: int, name: str, code: str, index: int):
            async with sem:
                await queue.put({
                    "type": "asset_start",
                    "asset_id": asset_id, "name": name, "code": code,
                    "index": index, "total": total,
                })
                await queue.put({
                    "type": "log",
                    "text": f"📥 拉取 {name}（{code}）近 180 日行情…",
                    "asset_id": asset_id, "name": name,
                })
                _db = SessionLocal()
                try:
                    a = _db.get(models.Asset, asset_id)
                    if a is None:
                        await queue.put({
                            "type": "asset_error",
                            "asset_id": asset_id, "name": name, "code": code,
                            "index": index, "total": total,
                            "error": "asset not found",
                        })
                        return
                    await queue.put({
                        "type": "log",
                        "text": "🧠 Hermes-Lite 基于已启用 Skill 进行多因子分析…",
                        "asset_id": asset_id, "name": name,
                    })
                    advice = await _analyze_one_core(_db, a, batch_id, "batch", ctx)
                    await queue.put({
                        "type": "asset_done",
                        "asset_id": asset_id, "name": name, "code": code,
                        "index": index, "total": total,
                        "advice_id": advice.id,
                        "action": advice.action,
                        "confidence": advice.confidence,
                        "summary": advice.summary,
                        "skill_used": advice.skill_used,
                    })
                except Exception as e:  # pragma: no cover
                    await queue.put({
                        "type": "asset_error",
                        "asset_id": asset_id, "name": name, "code": code,
                        "index": index, "total": total,
                        "error": str(e),
                    })
                finally:
                    _db.close()

        # 启动所有 worker（受 Semaphore 限流，实际并发 = concurrency）
        tasks = [
            asyncio.create_task(_worker(a.id, a.name, a.code, i + 1))
            for i, a in enumerate(assets)
        ]

        # 后台协程：等所有任务完成后塞一个 sentinel，通知消费循环退出
        async def _drain():
            await asyncio.gather(*tasks, return_exceptions=True)
            await queue.put({"__sentinel__": True})

        drainer = asyncio.create_task(_drain())

        analyzed = 0
        failed = 0
        try:
            while True:
                ev = await queue.get()
                if ev.get("__sentinel__"):
                    break
                if ev["type"] == "asset_done":
                    analyzed += 1
                elif ev["type"] == "asset_error":
                    failed += 1
                yield ev
        finally:
            # 若调用方提前断开，取消所有 worker，避免泄漏
            if not drainer.done():
                for t in tasks:
                    if not t.done():
                        t.cancel()
                try:
                    await drainer
                except Exception:
                    pass

        yield {
            "type": "done",
            "batch_id": batch_id,
            "analyzed": analyzed,
            "failed": failed,
        }
    finally:
        main_db.close()
