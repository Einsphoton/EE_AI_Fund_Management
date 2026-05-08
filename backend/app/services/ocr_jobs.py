"""OCR 异步任务管理器：内存级 Job Store + SSE 事件队列。

设计动机：
- 原本 /parse 是同步阻塞接口，前端切换路由就丢失结果；现在改成后台 Task。
- 用户回到页面要能"挂回去"看进度 → Job 持有完整事件历史 + 当前快照。
- 多客户端订阅同一 Job → 给每个 subscriber 一个 asyncio.Queue。

并发模型：
- 单进程内存 dict（个人本地工具，不需要 Redis/RQ）。
- Job 创建后 spawn `asyncio.create_task(_run_job)`，跑完写入 result。
- SSE 端点订阅 → 先 replay 历史事件，再持续推送新事件。
- 任务超时/完成后保留 30 分钟，超时自动 GC。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from ..agent import vision as vision_agent

# 任务保留时长（秒）
_JOB_TTL = 30 * 60


@dataclass
class OcrJob:
    job_id: str
    status: str = "pending"  # pending / parsing / done / error
    total: int = 0
    finished: int = 0
    platform_hint: str = ""
    file_names: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)  # 完整事件历史，用于重连 replay
    result: Optional[dict] = None  # 最终 results（含 _candidates / _suggestion）
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    # 订阅者队列（同一个 job 可能有多个前端 tab 订阅）
    subscribers: list[asyncio.Queue] = field(default_factory=list)

    def snapshot(self) -> dict:
        """前端首次拉取 / 列表展示用的概要。"""
        return {
            "job_id": self.job_id,
            "status": self.status,
            "total": self.total,
            "finished": self.finished,
            "platform_hint": self.platform_hint,
            "file_names": self.file_names,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "has_result": self.result is not None,
        }


class _JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, OcrJob] = {}
        self._lock = asyncio.Lock()

    def create(self, *, total: int, platform_hint: str, file_names: list[str]) -> OcrJob:
        job_id = uuid.uuid4().hex[:12]
        job = OcrJob(
            job_id=job_id,
            total=total,
            platform_hint=platform_hint,
            file_names=file_names,
        )
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Optional[OcrJob]:
        return self._jobs.get(job_id)

    def list_recent(self, limit: int = 10) -> list[dict]:
        items = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return [j.snapshot() for j in items[:limit]]

    def gc(self) -> None:
        """清理超过 TTL 的已完成任务。"""
        now = time.time()
        expired = [
            jid for jid, j in self._jobs.items()
            if j.status in ("done", "error")
            and j.finished_at and now - j.finished_at > _JOB_TTL
        ]
        for jid in expired:
            self._jobs.pop(jid, None)

    async def emit(self, job: OcrJob, event: dict) -> None:
        """记录到事件历史 + 广播给所有订阅者。"""
        event = {**event, "ts": time.time()}
        job.events.append(event)
        # 控制事件历史最大长度（避免长任务爆内存）
        if len(job.events) > 2000:
            job.events = job.events[-1500:]
        # 广播
        for q in list(job.subscribers):
            try:
                q.put_nowait(event)
            except Exception:
                pass

    async def subscribe(self, job: OcrJob) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        # 先 replay 全部历史
        for ev in list(job.events):
            try:
                q.put_nowait(ev)
            except Exception:
                break
        job.subscribers.append(q)
        return q

    def unsubscribe(self, job: OcrJob, q: asyncio.Queue) -> None:
        try:
            job.subscribers.remove(q)
        except ValueError:
            pass


manager = _JobManager()


# ============================================================
# 后台任务实际执行体
# ============================================================

async def run_parse_job(
    job: OcrJob,
    images: list[tuple[bytes, str, str]],
    db_factory,
    match_fn,
    suggest_fn,
):
    """后台跑视觉模型 + 候选匹配。

    Parameters
    ----------
    db_factory : Callable[[], Session]
        每张图开一个新 Session（避免长事务），用完关闭。
    match_fn : Callable[[Session, dict, str], list[dict]]
        从 import_api 注入的候选匹配函数。
    suggest_fn : Callable[[dict, Optional[dict], Session], dict]
        从 import_api 注入的建议动作函数。
    """
    job.status = "parsing"
    await manager.emit(job, {
        "type": "start",
        "total": job.total,
        "platform_hint": job.platform_hint,
        "files": job.file_names,
    })

    cfg = None
    # 取一份 vision 配置用于日志（model 名等）
    try:
        from ..services import settings_service
        db_probe = db_factory()
        try:
            cfg = settings_service.get(db_probe, "vision") or {}
            if cfg.get("use_ai"):
                ai = settings_service.get(db_probe, "ai") or {}
                cfg = {**cfg, "model": ai.get("model"), "_use_ai": True}
        finally:
            db_probe.close()
    except Exception:
        pass

    if cfg:
        await manager.emit(job, {
            "type": "thought",
            "text": f"使用模型：{cfg.get('model') or '未配置'}"
                    f"{'（复用 AI 大模型）' if cfg.get('_use_ai') else ''}，"
                    f"并发：{cfg.get('concurrency', 2)}",
        })

    concurrency = max(1, int((cfg or {}).get("concurrency", 2)))
    sem = asyncio.Semaphore(concurrency)

    out_results: list[dict] = [None] * len(images)  # type: ignore

    async def _one(idx: int, item: tuple[bytes, str, str]):
        b, mime, hint = item
        fname = job.file_names[idx] if idx < len(job.file_names) else f"img_{idx}"
        async with sem:
            await manager.emit(job, {
                "type": "image_start",
                "index": idx, "total": job.total, "file": fname,
            })
            await manager.emit(job, {
                "type": "thought",
                "text": f"[{fname}] 开始调用视觉模型，图片大小 {len(b)//1024} KB...",
                "file": fname,
            })

            t0 = time.time()
            db = db_factory()
            try:
                # 把 emit 当 logger 注入到 vision agent
                async def on_log(msg: str):
                    await manager.emit(job, {
                        "type": "thought",
                        "text": f"[{fname}] {msg}",
                        "file": fname,
                    })

                r = await vision_agent.parse_image(
                    db, b, mime=mime, platform_hint=hint, on_log=on_log,
                )
                cost = time.time() - t0

                items = r.get("items") or []
                # 候选匹配（命中已有资产）
                hits = 0
                for it in items:
                    cands = match_fn(db, it, r.get("platform") or hint)
                    top = cands[0] if cands else None
                    suggestion = suggest_fn(it, top, db)
                    it["_candidates"] = cands
                    it["_suggestion"] = suggestion
                    if top:
                        hits += 1

                r_out = {
                    "file": fname,
                    "platform": r.get("platform"),
                    "screenshot_date": r.get("screenshot_date"),
                    "items": items,
                    "error": r.get("error"),
                }
                out_results[idx] = r_out

                if r.get("error"):
                    await manager.emit(job, {
                        "type": "image_error",
                        "index": idx, "file": fname,
                        "error": r["error"],
                        "elapsed": round(cost, 2),
                    })
                else:
                    await manager.emit(job, {
                        "type": "image_done",
                        "index": idx, "file": fname,
                        "platform": r.get("platform"),
                        "items_count": len(items),
                        "matched_count": hits,
                        "elapsed": round(cost, 2),
                    })
            except Exception as e:
                out_results[idx] = {
                    "file": fname, "platform": "错误", "items": [],
                    "error": f"{type(e).__name__}: {str(e)[:200]}",
                }
                await manager.emit(job, {
                    "type": "image_error",
                    "index": idx, "file": fname,
                    "error": f"{type(e).__name__}: {str(e)[:200]}",
                })
            finally:
                db.close()
                job.finished += 1
                await manager.emit(job, {
                    "type": "progress",
                    "finished": job.finished, "total": job.total,
                })

    try:
        await asyncio.gather(*[_one(i, im) for i, im in enumerate(images)])

        out = [r for r in out_results if r is not None]
        total_items = sum(len(r["items"]) for r in out)
        job.result = {"results": out, "total": total_items}
        job.status = "done"
        job.finished_at = time.time()
        await manager.emit(job, {
            "type": "done",
            "total_items": total_items,
            "files": len(out),
            "errors": sum(1 for r in out if r.get("error")),
        })
    except Exception as e:
        job.status = "error"
        job.error = f"{type(e).__name__}: {e}"
        job.finished_at = time.time()
        await manager.emit(job, {"type": "fatal", "error": job.error})

    # GC 旧任务
    manager.gc()
