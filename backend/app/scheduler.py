"""APScheduler-based periodic AI analysis."""
from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import models
from .database import SessionLocal
from .services import settings_service
from .services.investment_manager import run_investment_manager
from .services.target_recommender import recommend_ai_targets
from .agent.analyzer import analyze_all


_scheduler: AsyncIOScheduler | None = None
_JOB_PREFIX = "ai-analysis-job"
_LEGACY_JOB_ID = _JOB_PREFIX


PRESET_CRON = {
    "hourly": "0 * * * *",
    "every6h": "0 */6 * * *",
    "daily": "0 9 * * *",
    "weekly": "0 9 * * 1",
}


def _resolve_cron(schedule_cfg: dict | None) -> str | None:
    if not schedule_cfg or not schedule_cfg.get("enabled"):
        return None
    preset = (schedule_cfg.get("preset") or "").lower()
    if preset in PRESET_CRON:
        return PRESET_CRON[preset]
    return schedule_cfg.get("cron") or PRESET_CRON["daily"]


def _job_id(user_id: int | None) -> str:
    return f"{_JOB_PREFIX}:u:{user_id}" if user_id is not None else _LEGACY_JOB_ID


def _remove_existing_job(sch: AsyncIOScheduler, job_id: str) -> None:
    job = sch.get_job(job_id)
    if job:
        sch.remove_job(job_id)


def _active_user_ids(db) -> list[int]:
    rows = db.query(models.User.id).filter(models.User.is_active.is_(True)).all()
    return [int(row[0]) for row in rows]


def _add_or_replace_job(
    sch: AsyncIOScheduler,
    *,
    user_id: int | None,
    schedule_cfg: dict | None,
) -> str | None:
    job_id = _job_id(user_id)
    _remove_existing_job(sch, job_id)

    cron = _resolve_cron(schedule_cfg)
    if not cron:
        return None
    try:
        trigger = CronTrigger.from_crontab(cron, timezone="Asia/Shanghai")
    except Exception:
        trigger = CronTrigger.from_crontab(PRESET_CRON["daily"], timezone="Asia/Shanghai")
        cron = PRESET_CRON["daily"]

    sch.add_job(
        _job_runner,
        trigger=trigger,
        id=job_id,
        args=[user_id],
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return cron


async def _job_runner(user_id: int | None = None):
    db = SessionLocal()
    try:
        if user_id is not None:
            db.info["user_id"] = user_id
        cfg = settings_service.get(db, "schedule", user_id=user_id) or {}
    finally:
        db.close()

    if not cfg.get("enabled"):
        return

    label = f"user {user_id}" if user_id is not None else "global"
    try:
        n = await analyze_all(user_id=user_id)
        print(f"[scheduler] {label} analyzed {n} assets/targets")

        if cfg.get("include_investment_plan"):
            db = SessionLocal()
            try:
                if user_id is not None:
                    db.info["user_id"] = user_id
                r = await run_investment_manager(db, user_id=user_id)
                print(f"[scheduler] {label} investment todos created {r.get('created', 0)}")
            finally:
                db.close()

        if cfg.get("include_ai_targets"):
            db = SessionLocal()
            try:
                if user_id is not None:
                    db.info["user_id"] = user_id
                targets = await recommend_ai_targets(db, limit=5, user_id=user_id)
                print(f"[scheduler] {label} ai targets refreshed {len(targets)}")
            finally:
                db.close()
    except Exception as e:  # pragma: no cover
        print(f"[scheduler] {label} failed: {e}")


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    return _scheduler


def reload_schedule(user_id: int | None = None) -> str | None:
    """重建指定用户的定时 AI 分析 job；user_id 为空时重建全部 job。"""
    if user_id is None:
        reload_all_schedules()
        return None

    sch = get_scheduler()
    db = SessionLocal()
    try:
        cfg = settings_service.get(db, "schedule", user_id=user_id) or {}
        return _add_or_replace_job(sch, user_id=user_id, schedule_cfg=cfg)
    finally:
        db.close()


def reload_all_schedules() -> dict[str, str]:
    """启动时为每个已启用用户级 schedule 创建独立 job。"""
    sch = get_scheduler()
    for job in list(sch.get_jobs()):
        if job.id == _LEGACY_JOB_ID or job.id.startswith(f"{_JOB_PREFIX}:"):
            sch.remove_job(job.id)

    applied: dict[str, str] = {}
    db = SessionLocal()
    try:
        for uid in _active_user_ids(db):
            cfg = settings_service.get(db, "schedule", user_id=uid) or {}
            cron = _add_or_replace_job(sch, user_id=uid, schedule_cfg=cfg)
            if cron:
                applied[f"u:{uid}"] = cron

        # 兼容早期单用户/全局配置：仅在没有任何用户级定时 job 时启用，避免重复分析。
        if not applied:
            global_cfg = settings_service.get(db, "schedule", user_id=None) or {}
            cron = _add_or_replace_job(sch, user_id=None, schedule_cfg=global_cfg)
            if cron:
                applied["global"] = cron
        return applied
    finally:
        db.close()


def start():
    sch = get_scheduler()
    if not sch.running:
        sch.start()
    reload_all_schedules()


def shutdown():
    sch = get_scheduler()
    if sch.running:
        sch.shutdown(wait=False)

