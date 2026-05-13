"""Run analysis on assets and persist Advice records."""
from __future__ import annotations

import asyncio
import secrets
from datetime import timedelta
from typing import Any, AsyncIterator, Callable, Iterable, Optional


from sqlalchemy.orm import Session

from .. import models
from ..database import SessionLocal
from ..logging_config import log_ai_event, safe_ai_config
from ..services import quotes as quotes_service
from ..services import holdings as holding_service
from ..services import ai_guard, settings_service, skills_service
from ..services import ai_provider_pool
from ..services import rate_limiter as rl_mod


from ..tz import now_local
from .hermes import run_agent



def new_batch_id() -> str:
    """生成一个可读 + 随机的 batch ID：`YYYYMMDDHHMMSS_<4hex>`。"""
    ts = now_local().strftime("%Y%m%d%H%M%S")
    return f"{ts}_{secrets.token_hex(2)}"


# ---------------------------------------------------------------------------
# 批内共享上下文：避免每个标的都重复查 Skill、重复读 settings.ai。
# ---------------------------------------------------------------------------
def _load_batch_context(db: Session, user_id: int | None = None) -> dict[str, Any]:

    skills = db.query(models.Skill).filter_by(enabled=True).all()
    skill_prompts = [skills_service.get_skill_prompt(s.skill_id) for s in skills]
    skill_label = ",".join(s.skill_id for s in skills) or "default"
    ai_cfg = settings_service.get(db, "ai", user_id=user_id) or {}

    # 把 ai 的 RPM 限速也注册进全局 RateLimiter（key="ai"）。
    # configure 是幂等的：每次批量分析都会按当前 ai 配置刷新一遍参数；
    # 历史 window 会保留，避免改了配置就"忘掉"刚发出去的请求。
    rl_mod.limiter.configure(
        "ai",
        rpm_limit=int(ai_cfg.get("rpm_limit", 0) or 0),
        min_interval_sec=float(ai_cfg.get("min_interval_sec", 0) or 0),
    )
    log_ai_event(
        "analyzer",
        "batch_context_loaded",
        config=safe_ai_config(ai_cfg),
        skill_count=len(skill_prompts),
        skill_label=skill_label,
        provider_count=ai_provider_pool.provider_count(ai_cfg),
    )

    return {

        "skill_prompts": skill_prompts,
        "skill_label": skill_label,
        "ai_cfg": ai_cfg,
    }


def _provider_failure_kind(result: dict[str, Any] | None) -> str:
    """run_agent 返回启发式兜底时，识别失败类型以便切换/冷却 Provider。"""
    if not isinstance(result, dict):
        return "unknown"
    detail = str(result.get("detail") or "")
    if "[调用大模型失败]" not in detail:
        return ""
    low = detail.lower()
    if "401" in detail or "403" in detail or "authenticationerror" in low or "unauthorized" in low:
        return "auth"
    if "429" in detail or "ratelimiterror" in low or "too many" in low:
        return "rate_limit"
    if "timeout" in low or "connection" in low or "连接" in detail:
        return "transient"
    return "unknown"


def _cost_mode(ai_cfg: dict[str, Any] | None) -> str:
    mode = str((ai_cfg or {}).get("cost_mode") or "quality").lower()
    return mode if mode in {"quality", "balanced", "economy"} else "quality"


def _reuse_policy(ai_cfg: dict[str, Any] | None) -> tuple[int, float]:
    """返回 (复用小时数, 允许价格变化百分比)。quality 默认不复用。"""
    mode = _cost_mode(ai_cfg)
    defaults = {
        "quality": (0, 0.0),
        "balanced": (12, 1.5),
        "economy": (24, 2.0),
    }
    hours, pct = defaults[mode]
    try:
        if "reuse_recent_advice_hours" in (ai_cfg or {}):
            hours = int((ai_cfg or {}).get("reuse_recent_advice_hours") or 0)
    except (TypeError, ValueError):
        pass
    try:
        if "reuse_price_change_pct" in (ai_cfg or {}):
            pct = float((ai_cfg or {}).get("reuse_price_change_pct") or 0)
    except (TypeError, ValueError):
        pass
    return max(0, hours), max(0.0, pct)


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _find_reusable_advice(
    db: Session,
    asset_id: int,
    current_price: Any,
    ai_cfg: dict[str, Any],
) -> tuple[models.Advice | None, str]:
    hours, price_change_pct = _reuse_policy(ai_cfg)
    if hours <= 0:
        return None, ""
    cutoff = now_local() - timedelta(hours=hours)
    latest = (
        db.query(models.Advice)
        .filter(models.Advice.asset_id == asset_id, models.Advice.created_at >= cutoff)
        .order_by(models.Advice.created_at.desc())
        .first()
    )
    if latest is None:
        return None, ""
    if "[调用大模型失败]" in str(latest.detail or "") or str(latest.skill_used or "").startswith("fallback"):
        return None, "最近结果是兜底/失败结果，不复用"

    current = _float_or_none(current_price)
    extra = latest.extra or {}
    previous = _float_or_none(extra.get("analysis_current_price") or extra.get("current_price"))
    if current is not None and previous is not None and previous > 0 and price_change_pct > 0:
        change = abs((current - previous) / previous * 100)
        if change > price_change_pct:
            return None, f"价格变化 {change:.2f}% 超过阈值 {price_change_pct:.2f}%"
        return latest, f"{hours} 小时内已分析，价格变化 {change:.2f}% ≤ {price_change_pct:.2f}%"
    return latest, f"{hours} 小时内已分析，复用近期结果"


def _persist_reused_advice(
    db: Session,
    latest: models.Advice,
    *,
    asset_id: int,
    batch_id: str,
    source: str,
    current_price: Any,
    holding: dict[str, Any],
    ai_cfg: dict[str, Any],
    reason: str,
) -> models.Advice:
    extra = dict(latest.extra or {})
    extra.update({
        "reused": True,
        "reused_from_advice_id": latest.id,
        "reuse_reason": reason,
        "analysis_current_price": current_price,
        "analysis_profit_pct": holding.get("profit_pct"),
        "cost_mode": _cost_mode(ai_cfg),
    })
    detail_prefix = f"【Token 节省】{reason}，本次未重新调用大模型。"
    advice = models.Advice(
        asset_id=asset_id,
        batch_id=batch_id,
        source=source,
        action=latest.action,
        confidence=latest.confidence,
        summary=latest.summary,
        detail=(detail_prefix + "\n\n" + str(latest.detail or "")).strip(),
        extra=extra,
        skill_used=(f"reused:{latest.skill_used}" if latest.skill_used else "reused")[:128],
    )
    db.add(advice)
    db.commit()
    db.refresh(advice)
    return advice


async def _analyze_one_core(

    db: Session,

    asset: models.Asset,
    batch_id: str,
    source: str,
    ctx: dict[str, Any],
    on_log: Optional[Callable[[str], Any]] = None,
    user_id: int | None = None,
) -> models.Advice:

    """实际分析逻辑：拉行情 -> 限速 -> 调 LLM -> 落库。

    on_log : 可选回调（同步或 awaitable），用于把"限速等待"等事件实时报给上层
             （流式 API 会把它接到 SSE 队列）。批量调度场景可不传。
    """
    quote_sources = settings_service.get(db, "quote_sources", user_id=user_id) or {}
    log_ai_event(
        "analyzer",
        "asset_analysis_prepare",
        batch_id=batch_id,
        source=source,
        asset_id=asset.id,
        asset_name=asset.name,
        asset_code=asset.code,
        asset_type=asset.asset_type.value,
        market=asset.market.value,
    )

    quote = await quotes_service.fetch_quote(

        asset.asset_type.value, asset.market.value, asset.code, days=180,
        quote_sources=quote_sources,
    )

    points = quote.get("points") or []
    current = quote.get("current_price")
    holding = holding_service.summarize(asset, current)

    if source == "batch":
        reusable, reuse_reason = _find_reusable_advice(db, asset.id, current, ctx["ai_cfg"])
        if reusable is not None:
            advice = _persist_reused_advice(
                db,
                reusable,
                asset_id=asset.id,
                batch_id=batch_id,
                source=source,
                current_price=current,
                holding=holding,
                ai_cfg=ctx["ai_cfg"],
                reason=reuse_reason,
            )
            log_ai_event(
                "analyzer",
                "asset_analysis_reused",
                batch_id=batch_id,
                asset_id=asset.id,
                reused_from_advice_id=reusable.id,
                advice_id=advice.id,
                reason=reuse_reason,
                cost_mode=_cost_mode(ctx["ai_cfg"]),
            )
            if on_log is not None:
                try:
                    r = on_log(f"♻️ {reuse_reason}，复用上一条 AI 分析，节省本次模型调用。")
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    pass
            return advice

    asset_dict = {

        "name": asset.name, "code": asset.code,
        "asset_type": asset.asset_type.value, "market": asset.market.value,
        "platform": asset.platform, "watch_only": asset.watch_only,
        "note": asset.note,
        "yield_7d": asset.yield_7d,
        "expected_apr": asset.expected_apr,
        "start_date": asset.start_date.isoformat() if asset.start_date else None,
        "maturity_date": asset.maturity_date.isoformat() if asset.maturity_date else None,
        "principal_amount": asset.principal_amount,
        "is_principal_guaranteed": asset.is_principal_guaranteed,
    }

    # AI Provider 池：批量资产分析按权重轮询起始 Provider；遇到限流/超时等
    # run_agent 内部兜底结果时，自动切到下一个 Provider 再试。
    provider_sequence = ai_provider_pool.choose_provider_sequence(ctx["ai_cfg"], purpose="asset-analysis")
    prompt_chars = len(str(asset_dict)) + len(str(points[-60:] if len(points) > 60 else points)) + sum(len(p or "") for p in ctx["skill_prompts"])
    result: dict[str, Any] | None = None
    attempted_provider_labels: list[str] = []

    for idx, provider_cfg in enumerate(provider_sequence):
        provider_label = ai_provider_pool.provider_label(provider_cfg)
        runtime = ai_provider_pool.reserve_provider(provider_cfg)
        attempted_provider_labels.append(provider_label)
        if on_log is not None and len(provider_sequence) > 1:
            try:
                r = on_log(
                    f"🔁 使用 AI Provider：{provider_label}（{idx + 1}/{len(provider_sequence)}，"
                    f"当前运行中 {runtime.get('inflight', 0)}）"
                )
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                pass
        log_ai_event(
            "analyzer",
            "provider_selected",
            batch_id=batch_id,
            asset_id=asset.id,
            provider=provider_label,
            provider_index=idx + 1,
            provider_total=len(provider_sequence),
            provider_runtime=runtime,
            config=safe_ai_config(provider_cfg),
        )

        try:
            # 全局 AI 预算守卫：每个 Provider 使用独立 key，因此多个合法 API Key 可各自按 RPM 排队。
            try:
                raw_mt = int((provider_cfg or {}).get("max_tokens") or 4096)
            except (TypeError, ValueError):
                raw_mt = 4096
            try:
                _ = await ai_guard.acquire_ai_budget(
                    "analyzer",
                    provider_cfg,
                    key=ai_provider_pool.provider_rate_key(provider_cfg),
                    prompt_chars=prompt_chars,
                    max_tokens=max(1024, min(raw_mt, 8192)),
                    on_log=on_log,
                )
            except asyncio.CancelledError:
                raise

            # 在线程池中执行同步的 OpenAI 调用，避免阻塞事件循环
            result = await asyncio.to_thread(
                run_agent,
                asset_dict, points, holding,
                ctx["skill_prompts"], provider_cfg, ctx["skill_label"],
            )
        finally:
            ai_provider_pool.release_provider(provider_cfg)

        failure_kind = _provider_failure_kind(result)
        if not failure_kind:
            ai_provider_pool.clear_provider_cooldown(provider_cfg)
            break

        cooldown_sec = 6 * 3600 if failure_kind == "auth" else (180.0 if failure_kind == "rate_limit" else 45.0)
        ai_provider_pool.mark_provider_unhealthy(
            provider_cfg,
            cooldown_sec=cooldown_sec,
            reason=failure_kind,
        )
        if failure_kind == "rate_limit":
            await rl_mod.limiter.penalize(
                ai_provider_pool.provider_rate_key(provider_cfg),
                pause_sec=120.0,
                reason="provider 429 fallback",
            )
        log_ai_event(
            "analyzer",
            "provider_unhealthy",
            level="warning",
            batch_id=batch_id,
            asset_id=asset.id,
            provider=provider_label,
            failure_kind=failure_kind,
            cooldown_sec=cooldown_sec,
        )
        if idx == len(provider_sequence) - 1:
            break
        log_ai_event(
            "analyzer",
            "provider_failover",
            level="warning",
            batch_id=batch_id,
            asset_id=asset.id,
            failed_provider=provider_label,
            failure_kind=failure_kind,
            next_provider=ai_provider_pool.provider_label(provider_sequence[idx + 1]),
        )
        if on_log is not None:
            try:
                r = on_log(f"⚠️ {provider_label} 调用失败（{failure_kind}），切换到下一个 AI Provider…")
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                pass


    if result is None:
        result = run_agent(asset_dict, points, holding, ctx["skill_prompts"], ctx["ai_cfg"], ctx["skill_label"])
    if attempted_provider_labels:
        result["provider_used"] = attempted_provider_labels[-1]


    extra_keys = ("score", "fundamentals", "macro", "micro", "risks", "pros",
                  "advice", "commentary", "profile_note", "investor_profile",
                  "time_horizon", "target_price", "stop_loss", "provider_used")

    extra = {k: result.get(k) for k in extra_keys if k in result}
    extra.update({
        "analysis_current_price": current,
        "analysis_profit_pct": holding.get("profit_pct"),
        "cost_mode": _cost_mode(ctx["ai_cfg"]),
    })
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
    log_ai_event(
        "analyzer",
        "asset_analysis_persisted",
        batch_id=batch_id,
        source=source,
        asset_id=asset.id,
        advice_id=advice.id,
        action=advice.action,
        confidence=advice.confidence,
        skill_used=advice.skill_used,
    )
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
    user_id = asset.user_id
    ctx = _load_batch_context(db, user_id=user_id)

    return await _analyze_one_core(db, asset, batch_id, source, ctx, user_id=user_id)



async def analyze_all(batch_id: Optional[str] = None, user_id: int | None = None) -> int:

    """供调度器调用：分析所有资产以及标的（含 watch-only），统一 source=batch。

    使用 settings.ai.batch_concurrency 控制并发度（默认 4）。
    """
    batch_id = batch_id or new_batch_id()
    db = SessionLocal()
    try:
        ctx = _load_batch_context(db, user_id=user_id)
        concurrency = _resolve_concurrency(ctx["ai_cfg"])
        assets_q = db.query(models.Asset)
        if user_id is not None:
            assets_q = assets_q.filter(models.Asset.user_id == user_id)
        assets: Iterable[models.Asset] = assets_q.all()

        asset_list = list(assets)
        log_ai_event(
            "analyzer",
            "batch_analysis_start",
            batch_id=batch_id,
            total=len(asset_list),
            concurrency=concurrency,
            config=safe_ai_config(ctx["ai_cfg"]),
        )
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
                    await _analyze_one_core(_db, a, batch_id, "batch", ctx, user_id=user_id)

                    analyzed += 1
                except Exception as e:  # pragma: no cover
                    log_ai_event(
                        "analyzer",
                        "batch_asset_failed",
                        level="error",
                        batch_id=batch_id,
                        asset_id=asset_id,
                        error_type=type(e).__name__,
                        error=str(e),
                    )
                finally:

                    _db.close()

        await asyncio.gather(*(_run(a.id) for a in asset_list))
        log_ai_event(
            "analyzer",
            "batch_analysis_done",
            batch_id=batch_id,
            analyzed=analyzed,
            total=len(asset_list),
        )
        return analyzed

    finally:
        db.close()


def _resolve_concurrency(ai_cfg: dict[str, Any]) -> int:
    """从 ai 配置中读出并发度，做合法性夹断（1-16）。

    默认 2：与 vision 一致，既能加速明显（1.6-1.8x）又不容易触发 RPM 限流。
    服务端 RPM 紧张时把『每分钟最大请求数 (rpm_limit)』调小即可，limiter 会自动排队。
    本地 Ollama 等无 RPM 限制 + 内网直连场景可手动调到 4-8。
    """
    try:
        n = int((ai_cfg or {}).get("batch_concurrency") or 2)
    except (TypeError, ValueError):
        n = 2
    return max(1, min(16, n))


# ---- 流式版：并发执行，谁先完成谁先向调用方推送事件 ----
async def analyze_all_stream(user_id: int | None = None) -> AsyncIterator[dict]:

    """分析所有资产以及标的并流式产出进度事件（并发版）。

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
        ctx = _load_batch_context(main_db, user_id=user_id)
        concurrency = _resolve_concurrency(ctx["ai_cfg"])

        assets_q = main_db.query(models.Asset)
        if user_id is not None:
            assets_q = assets_q.filter(models.Asset.user_id == user_id)
        assets = assets_q.all()

        total = len(assets)
        log_ai_event(
            "analyzer",
            "stream_batch_analysis_start",
            batch_id=batch_id,
            total=total,
            concurrency=concurrency,
            config=safe_ai_config(ctx["ai_cfg"]),
        )
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

                    # 把限速等待事件实时推给前端
                    async def _on_wait_log(text: str) -> None:
                        await queue.put({
                            "type": "log", "text": text,
                            "asset_id": asset_id, "name": name,
                        })

                    advice = await _analyze_one_core(
                        _db, a, batch_id, "batch", ctx,
                        on_log=_on_wait_log,
                        user_id=user_id,
                    )

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
                    log_ai_event(
                        "analyzer",
                        "stream_batch_asset_failed",
                        level="error",
                        batch_id=batch_id,
                        asset_id=asset_id,
                        asset_name=name,
                        asset_code=code,
                        error_type=type(e).__name__,
                        error=str(e),
                    )
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

        log_ai_event(
            "analyzer",
            "stream_batch_analysis_done",
            batch_id=batch_id,
            analyzed=analyzed,
            failed=failed,
            total=total,
        )
        yield {
            "type": "done",
            "batch_id": batch_id,
            "analyzed": analyzed,
            "failed": failed,
        }

    finally:
        main_db.close()
