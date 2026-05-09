"""通用滑动窗口 RPM 限速器（异步）。

使用场景：
- vision OCR：避免在 NVIDIA NIM / Kimi / Qwen-VL 上撞 RPM 上限
- AI 批量分析：avoid 一次性把全部标的并发推给同一个 LLM 触发限流

设计要点：
- 滑动窗口（不是固定窗口）：维护"过去 60 秒内已发起的请求时间戳"队列
- 双层兜底：rpm_limit (主) + min_interval_sec (兜底)，谁严格谁说了算
- 跨调用共享：用 `key` 区分不同实例（vision / ai 各一份）
- 取消友好：waited 期间允许外部传入 cancel_callback / 通过 asyncio.CancelledError 中止
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class _RateState:
    rpm_limit: int = 0           # 0 = 不限
    min_interval: float = 0.0     # 0 = 不限
    window: list[float] = field(default_factory=list)  # 过去 60s 的请求时间戳
    last_call_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RateLimiter:
    """支持多 key 的滑动窗口限速器。

    用法：
        rl = RateLimiter()
        rl.configure("vision", rpm_limit=35, min_interval_sec=0)
        async with rl.acquire("vision") as wait_info:
            if wait_info.waited > 0:
                log(f"等待了 {wait_info.waited:.1f}s（{wait_info.reason}）")
            await call_model(...)

    或者用更直接的 API：
        info = await rl.wait_for_slot("vision", on_waiting=lambda s, r: log(...))
        await call_model(...)
    """

    def __init__(self) -> None:
        self._states: dict[str, _RateState] = {}
        self._configure_lock = asyncio.Lock()

    def configure(self, key: str, *, rpm_limit: int = 0, min_interval_sec: float = 0.0) -> None:
        """配置某个 key 的限速参数。可重复调用更新。

        重新配置时**保留 window 历史**——避免用户改了一下设置就把刚发出去的请求"忘了"，
        以为可以立即再发一波。
        """
        s = self._states.get(key)
        if s is None:
            s = _RateState()
            self._states[key] = s
        s.rpm_limit = max(0, int(rpm_limit or 0))
        s.min_interval = max(0.0, float(min_interval_sec or 0))

    def is_active(self, key: str) -> bool:
        s = self._states.get(key)
        if not s:
            return False
        return s.rpm_limit > 0 or s.min_interval > 0

    async def wait_for_slot(
        self,
        key: str,
        *,
        on_waiting: Optional[Callable[[float, str], "asyncio.Future | None"]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        max_wait: float = 65.0,
    ) -> dict:
        """等待一个限速槽位，返回前会把"当次请求时刻"记进 window。

        重要：lock 的持有时间必须**极短**——只在"读 window + 决定要等多久 + 立刻
        预占一个槽位"那一瞬间持有，**不能在 lock 里 sleep**！否则两个并发请求会
        被强行串行（第二个等第一个把 sleep 在 lock 里跑完才能进），让 OCR 并发=2
        瞬间退化成串行，速度直接砍半。

        正确流程：
          1. 进 lock：清窗口、判断要不要等 N 秒、立即把"放行预约时刻"压进 window 占位
          2. 出 lock：在 lock 外面睡 N 秒（这期间其他请求能继续进 lock 占自己的位）

        这样既保证了 RPM 严格控制（占位是原子的），又不把并发请求互相阻塞。

        Parameters
        ----------
        on_waiting : callable(seconds, reason)
            等待开始时回调一次（给上层打日志用）。返回 None 即可，也可返回 awaitable。
        cancel_check : callable -> bool
            每 0.5s 调一次，True 时立即抛 asyncio.CancelledError。
        max_wait : float
            单次 acquire 最多等多久（防止 RPM 配置错误导致死等）。

        Returns
        -------
        dict
            {"waited": <实际等待秒数>, "reason": <文本>, "rpm_used": <窗口内已发起>}
        """
        s = self._states.get(key)
        if s is None or (s.rpm_limit <= 0 and s.min_interval <= 0):
            return {"waited": 0.0, "reason": "", "rpm_used": 0}

        # === 第 1 阶段：原子地决定"放行时刻"并预占槽位 ===
        # 这段必须在 lock 里，但禁止任何 await 阻塞调用（除了 lock 本身）
        async with s.lock:
            now = time.time()
            wait_secs = 0.0
            reason = ""

            # RPM 滑动窗口
            if s.rpm_limit > 0:
                cutoff = now - 60.0
                while s.window and s.window[0] < cutoff:
                    s.window.pop(0)
                if len(s.window) >= s.rpm_limit:
                    need = (s.window[0] + 60.0) - now + 0.05  # 0.05s 余量
                    if need > wait_secs:
                        wait_secs = need
                        reason = f"RPM 已达上限 {s.rpm_limit}/分钟（窗口内 {len(s.window)} 次）"

            # min_interval 兜底：相对 last_call_at（上一次"放行时刻"）算
            if s.min_interval > 0 and s.last_call_at > 0:
                gap = now - s.last_call_at
                if gap < s.min_interval:
                    need = s.min_interval - gap
                    if need > wait_secs:
                        wait_secs = need
                        reason = f"距上次调用 {gap:.1f}s < 最小间隔 {s.min_interval}s"

            wait_secs = min(max_wait, wait_secs)
            release_at = now + wait_secs  # 这次请求"实际放行"的预约时刻

            # 立即把"放行时刻"压入 window —— 后续并发请求看到的 window 就含这一条，
            # 据此算出自己的等待时间，互不干扰、严格守住 rpm_limit 上限
            if s.rpm_limit > 0:
                s.window.append(release_at)
            s.last_call_at = release_at

        # === 第 2 阶段：在 lock 外面慢慢睡 ===
        if wait_secs > 0:
            if on_waiting is not None:
                try:
                    r = on_waiting(wait_secs, reason)
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    pass
            slept = 0.0
            while slept < wait_secs:
                if cancel_check is not None:
                    try:
                        if cancel_check():
                            # 取消时把刚才占的位让出来，避免错杀其他请求的配额
                            async with s.lock:
                                if s.rpm_limit > 0 and s.window and s.window[-1] == release_at:
                                    s.window.pop()
                            raise asyncio.CancelledError()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass
                step = min(0.5, wait_secs - slept)
                await asyncio.sleep(step)
                slept += step

        return {
            "waited": wait_secs,
            "reason": reason,
            "rpm_used": len(s.window) if s.rpm_limit > 0 else 0,
        }

    def reset(self, key: Optional[str] = None) -> None:
        """清空某个 key（或全部）的窗口记录。仅测试用。"""
        if key is None:
            self._states.clear()
            return
        s = self._states.get(key)
        if s is not None:
            s.window.clear()
            s.last_call_at = 0.0

    async def penalize(self, key: str, *, pause_sec: float, reason: str = "429") -> None:
        """服务端报 429 / 显式限流后，把"强制静默期"注入限速器，让所有并发请求一起等。

        具体做法：把 `last_call_at` 和 window 里**所有条目**都后推到 `now + pause_sec`。
        这样后续任意请求算 wait_secs 时都会看到「刚刚有 rpm_limit 次请求在 now+pause 时刻」
        → 至少得等到 `now + pause_sec + 60/rpm_limit` 才能发下一条。

        同时也尊重 min_interval：强制把 last_call_at 推到 `now + pause_sec`。

        用途：vision / ai 模块遇到 429 时调用本方法，把 NIM 要求的 Retry-After 时间
        统一同步给**所有**并发请求，避免"前一张图刚 429、后一张图立刻又撞枪"。
        """
        s = self._states.get(key)
        if s is None or pause_sec <= 0:
            return
        async with s.lock:
            now = time.time()
            target = now + pause_sec
            s.last_call_at = max(s.last_call_at, target)
            if s.rpm_limit > 0:
                # 把 window 填满（至少 rpm_limit 条）并全部后推到 target
                # 这样任何新请求都会发现"60s 内已经满配额"，必须等到 target 之后
                new_window = [target] * max(s.rpm_limit, len(s.window))
                s.window = new_window


# 进程级单例：vision / ai 两个 key 共用同一个限速器
limiter = RateLimiter()
