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
from . import rate_limiter as rl_mod

# 任务保留时长（秒）
_JOB_TTL = 30 * 60


@dataclass
class OcrJob:
    job_id: str
    status: str = "pending"  # pending / parsing / done / error / cancelled
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
    # 取消信号：用户点"停止识别"时 set，后台任务/视觉模型流式循环都监听这个
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

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
            "cancelled": self.cancel_event.is_set(),
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
# 工具：OCR 解析后即时补 fund/etf 代码
# ============================================================

async def _auto_fill_fund_codes(items: list[dict], on_log) -> None:
    """对一批 OCR items 里『缺 code』的 fund/etf/stock 项，并发查多源 API 补 code。

    设计原则：
    - **绝不阻塞主流程**：单条 3s 超时上限，整批最多 5s 全部并发
    - 静默失败：任何超时/异常都不抛，保持 code=None；前端点"查码"兜底
    - 不调用 LLM：API 没有的项就让用户自己处理，避免 LLM 烧 RPM
    - 限制并发：一图最多 8 个查码并行（API 端没强限流）
    - 写回 item dict：成功就把 code 填回原 dict，并打 thought 日志
    - **多源融合**：fund/etf 走 _enrich_fund_code（4 源并行：天天基金+腾讯+新浪+雪球），
      stock 走 _enrich_stock_code（3 源并行：腾讯+新浪+雪球）
    """
    targets = [
        it for it in items
        if isinstance(it, dict)
        and (it.get("asset_type") in ("fund", "etf", "stock"))
        and not (it.get("code") or "").strip()
        and (it.get("name") or "").strip()
    ]
    if not targets:
        return

    try:
        from . import enrichment
    except Exception:
        return

    sem = asyncio.Semaphore(8)
    PER_REQ_TIMEOUT = 3.0      # 单条最多等 3s
    BATCH_TIMEOUT = 5.0        # 整批最多 5s（避免某条卡死把整批拖 8s+）

    async def _one(it: dict):
        name = (it.get("name") or "").strip()
        atype = (it.get("asset_type") or "").lower()
        async with sem:
            try:
                if atype == "stock":
                    sug = await asyncio.wait_for(
                        enrichment._enrich_stock_code(name),
                        timeout=PER_REQ_TIMEOUT,
                    )
                else:
                    # fund / etf 都走基金搜索
                    sug = await asyncio.wait_for(
                        enrichment._enrich_fund_code(name),
                        timeout=PER_REQ_TIMEOUT,
                    )
            except (asyncio.TimeoutError, Exception):
                # 静默：单条失败/超时不影响整批
                return
        if sug and sug.get("code"):
            it["code"] = sug["code"]
            it.setdefault("_code_source", sug.get("source") or "")
            it.setdefault("_code_score", sug.get("score") or 0.0)
            it.setdefault("_code_matched_name", sug.get("matched_name") or "")
            try:
                await on_log(
                    f"  ↪ 自动查码 [{name}] → {sug['code']}"
                    f"（{sug.get('source','?')} · {(sug.get('score') or 0) * 100:.0f}% · "
                    f"匹配「{sug.get('matched_name') or name}」）"
                )
            except Exception:
                pass

    try:
        await on_log(f"🔎 自动查码：{len(targets)} 项缺代码的产品（多源并行，最多 {BATCH_TIMEOUT}s）…")
    except Exception:
        pass

    try:
        await asyncio.wait_for(
            asyncio.gather(*[_one(it) for it in targets], return_exceptions=True),
            timeout=BATCH_TIMEOUT,
        )
    except asyncio.TimeoutError:
        # 整批超时：让没查到的保持 code=None，用户后面点查码按钮兜底
        try:
            await on_log(f"  ↪ 自动查码整批超时（>{BATCH_TIMEOUT}s），未查到的保持空 code")
        except Exception:
            pass


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
        concurrency_log = cfg.get("concurrency", 2)
        rpm_limit_log = cfg.get("rpm_limit", 0) or 0
        interval_log = cfg.get("min_interval_sec", 0) or 0
        stream_mode = "流式（含死循环检测）" if cfg.get("stream", False) else "非流式（更快，推荐）"
        auto_fill_mode = "自动查码" if cfg.get("auto_fill_code", True) else "不自动查码"
        await manager.emit(job, {
            "type": "thought",
            "text": f"使用模型：{cfg.get('model') or '未配置'}"
                    f"{'（复用 AI 大模型）' if cfg.get('_use_ai') else ''}，"
                    f"并发：{concurrency_log}，调用模式：{stream_mode}，"
                    f"代码补全：{auto_fill_mode}，"
                    f"RPM 上限：{rpm_limit_log if rpm_limit_log > 0 else '不限'}"
                    + (f"，最小间隔：{interval_log}s" if interval_log > 0 else ""),
        })

    # 与 settings_service.DEFAULTS["vision"]["concurrency"]=2 保持一致；
    # 历史 bug：默认 1 会让 OCR 串行
    concurrency = max(1, int((cfg or {}).get("concurrency", 2)))
    sem = asyncio.Semaphore(concurrency)
    # ── 限速控制：交给共享 RateLimiter（vision / ai 各占一个 key，同进程隔离）──
    rpm_limit = max(0, int((cfg or {}).get("rpm_limit", 0) or 0))
    min_interval = max(0.0, float((cfg or {}).get("min_interval_sec", 0) or 0))
    rl_mod.limiter.configure("vision", rpm_limit=rpm_limit, min_interval_sec=min_interval)

    async def _wait_for_slot(fname: str) -> None:
        """统一的限速等待：把 RateLimiter 等待事件转成 thought 日志推给前端。"""
        if not rl_mod.limiter.is_active("vision"):
            return

        async def _on_waiting(secs: float, reason: str) -> None:
            await manager.emit(job, {
                "type": "thought",
                "text": f"[{fname}] ⏳ 限速等待 {secs:.1f}s（{reason}）",
                "file": fname,
            })

        try:
            await rl_mod.limiter.wait_for_slot(
                "vision",
                on_waiting=_on_waiting,
                cancel_check=job.cancel_event.is_set,
            )
        except asyncio.CancelledError:
            # 上层会再判 cancel_event 走标准取消路径
            return

    out_results: list[dict] = [None] * len(images)  # type: ignore

    # ── 连续失败熔断器 ──
    # 设计目的：当服务端进入"账号被封禁/免费额度耗尽/模型整段不可用"等持久性问题时，
    # 单图重试 4 次都不会成功，rate limiter 又会让后续图等 60s 再发——结果就是
    # 9 张图依次卡 60s × 9 = 9 分钟，全部失败。
    # 熔断条件：连续 N 张图都因为 **限流/服务端拒绝类** 错误失败，认定服务端不可用，
    # 后续所有图直接标记为"跳过：服务端持续不可用"，立即结束 job。
    consecutive_rate_errors = {"count": 0, "tripped": False, "first_err": ""}
    CIRCUIT_BREAKER_THRESHOLD = 3

    def _is_rate_or_server_unavailable(err_msg: str) -> bool:
        s = err_msg.lower()
        return (
            "429" in err_msg
            or "ratelimit" in s.replace(" ", "").replace("_", "")
            or "too many" in s
            or "503" in err_msg
            or "service unavailable" in s
            or "quota" in s
            or "exceeded" in s
        )

    def _trip_circuit_breaker():
        """触发熔断器：标记 tripped + 重置限速器 window。

        重置 window 的意义：用户看到熔断提示后会去换模型/换 key/等几小时再点重试；
        重试时如果限速器还残留着 35 条"被 429 撞红的"过期记录，会让首张图被迫等
        59s 才发——体验差。直接重置成 0 让用户重试时立即上手。
        """
        consecutive_rate_errors["tripped"] = True
        try:
            rl_mod.limiter.reset("vision")
        except Exception:
            pass

    async def _one(idx: int, item: tuple[bytes, str, str]):
        b, mime, hint = item
        fname = job.file_names[idx] if idx < len(job.file_names) else f"img_{idx}"

        # 熔断器优先：如果已经 trip 过，所有后续图直接跳过，不再重试也不再 wait
        if consecutive_rate_errors["tripped"]:
            out_results[idx] = {
                "file": fname, "platform": "服务端不可用", "items": [],
                "error": (
                    "因前序连续 OCR 失败已触发熔断器（详见 job 历史）。"
                    "常见原因：免费档额度耗尽 / 当前时段排队 / 账号风控。"
                    "建议：1) 切到智谱 GLM-4V-Flash 或阿里 Qwen-VL-Plus；"
                    "2) 等几小时再试；3) 升级付费档位。"
                    f"首图错误样本：{consecutive_rate_errors['first_err'][:160]}"
                ),
            }
            await manager.emit(job, {
                "type": "image_error",
                "index": idx, "file": fname,
                "error": "服务端持续不可用，已跳过（熔断器已触发）",
            })
            job.finished += 1
            await manager.emit(job, {
                "type": "progress",
                "finished": job.finished, "total": job.total,
            })
            return

        # 进入信号量前先看一眼：如果已经被取消，就直接跳过这张
        if job.cancel_event.is_set():
            await manager.emit(job, {
                "type": "image_cancelled",
                "index": idx, "file": fname,
            })
            out_results[idx] = {
                "file": fname, "platform": "已取消", "items": [],
                "error": "用户取消",
            }
            job.finished += 1
            await manager.emit(job, {
                "type": "progress",
                "finished": job.finished, "total": job.total,
            })
            return

        async with sem:
            # 抢到信号量后再看一次（前面排队的图跑完时用户可能刚点了取消）
            if job.cancel_event.is_set():
                await manager.emit(job, {
                    "type": "image_cancelled",
                    "index": idx, "file": fname,
                })
                out_results[idx] = {
                    "file": fname, "platform": "已取消", "items": [],
                    "error": "用户取消",
                }
                job.finished += 1
                await manager.emit(job, {
                    "type": "progress",
                    "finished": job.finished, "total": job.total,
                })
                return

            # 抢到信号量 + 检查取消后，再做限速节流（RPM 窗口 + min_interval 双层）
            await _wait_for_slot(fname)
            # 节流期间用户可能点了取消
            if job.cancel_event.is_set():
                await manager.emit(job, {
                    "type": "image_cancelled",
                    "index": idx, "file": fname,
                })
                out_results[idx] = {
                    "file": fname, "platform": "已取消", "items": [],
                    "error": "用户取消",
                }
                job.finished += 1
                await manager.emit(job, {
                    "type": "progress",
                    "finished": job.finished, "total": job.total,
                })
                return

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

                # 把 vision 调用放进一个独立 task，并在它和 cancel watcher 之间
                # 做 race。即便 vision 调用因 SDK 内部卡住没及时返回 cancelled，
                # 我们也能在 cancel_event 触发后最多 1 秒内推进，让 _one 提前结束。
                vision_task = asyncio.create_task(vision_agent.parse_image(
                    db, b, mime=mime, platform_hint=hint, on_log=on_log,
                    cancel_event=job.cancel_event,
                ))

                async def _wait_cancel():
                    # 轮询 cancel_event；命中后给 vision_task 1 秒做收尾
                    while not job.cancel_event.is_set():
                        await asyncio.sleep(0.2)
                    return "cancelled"

                cancel_watch = asyncio.create_task(_wait_cancel())
                done, pending = await asyncio.wait(
                    {vision_task, cancel_watch},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if vision_task in done:
                    cancel_watch.cancel()
                    r = vision_task.result()
                else:
                    # 取消信号先到：再等 vision_task 最多 1 秒优雅返回
                    try:
                        r = await asyncio.wait_for(asyncio.shield(vision_task), timeout=1.0)
                    except asyncio.TimeoutError:
                        # vision 卡住了：强制 cancel 这个 task，组装一个 cancelled 结果
                        vision_task.cancel()
                        await manager.emit(job, {
                            "type": "thought",
                            "text": f"[{fname}] vision 调用未在 1s 内响应取消，强制中断",
                            "file": fname,
                        })
                        r = {"platform": "已取消", "items": [], "cancelled": True}
                cost = time.time() - t0

                # 取消导致的提前返回
                if r.get("cancelled"):
                    out_results[idx] = {
                        "file": fname, "platform": "已取消", "items": [],
                        "error": "用户取消",
                    }
                    await manager.emit(job, {
                        "type": "image_cancelled",
                        "index": idx, "file": fname,
                        "elapsed": round(cost, 2),
                    })
                else:
                    items = r.get("items") or []
                    # 自动补码（默认开，可配置关）：fund/etf 类型缺 code 的项调天天基金 API。
                    # 严格超时：单条 3s、整批 5s —— 即便 API 全挂，最多给每张图加 5s。
                    # 用户可以在『设置 → 视觉模型』里把 auto_fill_code 改成 false 关掉，
                    # 或在前端确认清单里点"🔍 查码"按钮按需补全。
                    auto_fill = bool((cfg or {}).get("auto_fill_code", True))
                    enrich_cost = 0.0
                    if auto_fill:
                        t_enrich = time.time()
                        await _auto_fill_fund_codes(items, on_log)
                        enrich_cost = time.time() - t_enrich

                    # 候选匹配（命中已有资产）
                    t_match = time.time()
                    hits = 0
                    for it in items:
                        cands = match_fn(db, it, r.get("platform") or hint)
                        top = cands[0] if cands else None
                        suggestion = suggest_fn(it, top, db)
                        it["_candidates"] = cands
                        it["_suggestion"] = suggestion
                        if top:
                            hits += 1
                    match_cost = time.time() - t_match

                    r_out = {
                        "file": fname,
                        "platform": r.get("platform"),
                        "screenshot_date": r.get("screenshot_date"),
                        "items": items,
                        "error": r.get("error"),
                    }
                    out_results[idx] = r_out

                    if r.get("error"):
                        # 熔断器累计：连续 N 张限流类错误 → 触发熔断
                        err_text = str(r.get("error") or "")
                        if _is_rate_or_server_unavailable(err_text):
                            consecutive_rate_errors["count"] += 1
                            if not consecutive_rate_errors["first_err"]:
                                consecutive_rate_errors["first_err"] = err_text
                            if consecutive_rate_errors["count"] >= CIRCUIT_BREAKER_THRESHOLD:
                                consecutive_rate_errors["tripped"] = True
                                await manager.emit(job, {
                                    "type": "thought",
                                    "text": (
                                        f"🛑 已连续 {consecutive_rate_errors['count']} 张图被服务端限流/拒绝，"
                                        f"判定服务端持续不可用 → **触发熔断器**，后续 {len(images) - idx - 1} 张图直接跳过。\n"
                                        f"  常见原因：免费档额度耗尽 / 当前时段排队 / 账号风控。\n"
                                        f"  建议：1) 设置 → 视觉模型，换成智谱 GLM-4V-Flash（完全免费）；"
                                        f"2) 阿里 Qwen-VL-Plus；3) 等几小时再试。"
                                    ),
                                })
                        else:
                            consecutive_rate_errors["count"] = 0
                        await manager.emit(job, {
                            "type": "image_error",
                            "index": idx, "file": fname,
                            "error": r["error"],
                            "elapsed": round(cost, 2),
                        })
                    else:
                        # 成功：重置熔断计数器
                        consecutive_rate_errors["count"] = 0
                        # 端到端计时分解：让用户看清『谁是瓶颈』
                        # vision 模型耗时 = cost - 后处理；查码 + 候选匹配通常 < 1s
                        # 若发现 enrich_cost / match_cost 异常大，立即就能定位
                        await manager.emit(job, {
                            "type": "image_done",
                            "index": idx, "file": fname,
                            "platform": r.get("platform"),
                            "items_count": len(items),
                            "matched_count": hits,
                            "elapsed": round(cost, 2),
                        })
                        # 把后处理耗时单独打日志，慢瓶颈一眼可见
                        if enrich_cost > 0.5 or match_cost > 0.5:
                            await manager.emit(job, {
                                "type": "thought",
                                "text": (
                                    f"[{fname}] 后处理耗时 查码={enrich_cost:.2f}s "
                                    f"匹配={match_cost:.2f}s（vision 模型耗时={cost - enrich_cost - match_cost:.2f}s）"
                                ),
                                "file": fname,
                            })
            except Exception as e:
                err_text = f"{type(e).__name__}: {str(e)[:200]}"
                # 熔断器：异常路径同样判定（如 RateLimitError 直接 raise 出来）
                if _is_rate_or_server_unavailable(err_text):
                    consecutive_rate_errors["count"] += 1
                    if not consecutive_rate_errors["first_err"]:
                        consecutive_rate_errors["first_err"] = err_text
                    if consecutive_rate_errors["count"] >= CIRCUIT_BREAKER_THRESHOLD:
                        _trip_circuit_breaker()
                        await manager.emit(job, {
                            "type": "thought",
                            "text": (
                                f"🛑 已连续 {consecutive_rate_errors['count']} 张图被服务端限流/拒绝，"
                                f"判定服务端持续不可用 → **触发熔断器**，后续图直接跳过。\n"
                                f"  建议切换视觉模型：智谱 GLM-4V-Flash（完全免费）/ 阿里 Qwen-VL-Plus。"
                            ),
                        })
                else:
                    consecutive_rate_errors["count"] = 0
                out_results[idx] = {
                    "file": fname, "platform": "错误", "items": [],
                    "error": err_text,
                }
                await manager.emit(job, {
                    "type": "image_error",
                    "index": idx, "file": fname,
                    "error": err_text,
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
        # 即使被取消，也把已识别的部分组装成 result，让用户能看到/确认前面已成功的
        job.result = {"results": out, "total": total_items}
        job.finished_at = time.time()

        if job.cancel_event.is_set():
            job.status = "cancelled"
            cancelled_count = sum(1 for r in out if r.get("platform") == "已取消")
            await manager.emit(job, {
                "type": "cancelled",
                "total_items": total_items,
                "files": len(out),
                "cancelled_files": cancelled_count,
                "errors": sum(1 for r in out if r.get("error") and r.get("platform") != "已取消"),
            })
        else:
            job.status = "done"
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
