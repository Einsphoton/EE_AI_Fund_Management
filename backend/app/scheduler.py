"""APScheduler-based periodic AI analysis."""
from __future__ import annotations

import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .database import SessionLocal
from .services import settings_service
from .services.investment_manager import run_investment_manager
from .services.target_recommender import recommend_ai_targets
from .agent.analyzer import analyze_all


_scheduler: AsyncIOScheduler | None = None
_JOB_ID = "ai-analysis-job"


PRESET_CRON = {
    "hourly": "0 * * * *",
    "every6h": "0 */6 * * *",
    "daily": "0 9 * * *",
    "weekly": "0 9 * * 1",
}


def _resolve_cron(schedule_cfg: dict) -> str | None:
    if not schedule_cfg or not schedule_cfg.get("enabled"):
        return None
    preset = (schedule_cfg.get("preset") or "").lower()
    if preset in PRESET_CRON:
        return PRESET_CRON[preset]
    return schedule_cfg.get("cron") or PRESET_CRON["daily"]


async def _job_runner():
    db = SessionLocal()
    try:
        cfg = settings_service.get(db, "schedule") or {}
    finally:
        db.close()

    try:
        n = await analyze_all()
        print(f"[scheduler] analyzed {n} assets/targets")

        if cfg.get("include_investment_plan"):
            db = SessionLocal()
            try:
                r = await run_investment_manager(db)
                print(f"[scheduler] investment todos created {r.get('created', 0)}")
            finally:
                db.close()

        if cfg.get("include_ai_targets"):
            db = SessionLocal()
            try:
                targets = await recommend_ai_targets(db, limit=5)
                print(f"[scheduler] ai targets refreshed {len(targets)}")
            finally:
                db.close()
    except Exception as e:  # pragma: no cover
        print(f"[scheduler] failed: {e}")


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    return _scheduler


def reload_schedule() -> str | None:
    """根据当前 settings 中 schedule 配置重建 job."""
    sch = get_scheduler()
    db = SessionLocal()
    try:
        cfg = settings_service.get(db, "schedule") or {}
    finally:
        db.close()
    cron = _resolve_cron(cfg)

    # 移除旧 job
    job = sch.get_job(_JOB_ID)
    if job:
        sch.remove_job(_JOB_ID)
    if not cron:
        return None
    try:
        trigger = CronTrigger.from_crontab(cron, timezone="Asia/Shanghai")
    except Exception:
        trigger = CronTrigger.from_crontab(PRESET_CRON["daily"], timezone="Asia/Shanghai")
        cron = PRESET_CRON["daily"]
    sch.add_job(_job_runner, trigger=trigger, id=_JOB_ID, replace_existing=True, max_instances=1, coalesce=True)
    return cron


def start():
    sch = get_scheduler()
    if not sch.running:
        sch.start()
    reload_schedule()


def shutdown():
    sch = get_scheduler()
    if sch.running:
        sch.shutdown(wait=False)
