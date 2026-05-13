"""Global AI request guard: NIM-friendly RPM/TPM/concurrency smoothing."""
from __future__ import annotations

import asyncio
import json
import math
from typing import Any, Callable

from ..logging_config import log_ai_event, safe_ai_config
from . import rate_limiter as rl_mod

CHARS_PER_TOKEN = 2.5
NIM_DEFAULT_RPM = 10
NIM_MAX_EFFECTIVE_RPM = 20
# NIM 的 429 往往同时包含 RPM/TPM/并发队列限制；用更小的预算片换取稳定性，
# 不裁剪 prompt / max_tokens，只让大请求排得更平滑。
NIM_TOKENS_PER_SLOT = 2048
NIM_MAX_UNITS = 12



def is_nim_config(cfg: dict[str, Any] | None) -> bool:
    base_url = str((cfg or {}).get("base_url") or "").lower()
    model = str((cfg or {}).get("model") or "").lower()
    return "integrate.api.nvidia.com" in base_url or "nvidia" in base_url or model.startswith(("deepseek-ai/", "moonshotai/"))


def estimate_tokens_from_messages(messages: list[dict[str, Any]] | None = None, *, prompt_chars: int = 0, max_tokens: int = 0) -> int:
    chars = max(0, int(prompt_chars or 0))
    for m in messages or []:
        content = m.get("content") if isinstance(m, dict) else ""
        if isinstance(content, str):
            chars += len(content)
        else:
            try:
                chars += len(json.dumps(content, ensure_ascii=False))
            except Exception:
                chars += len(str(content))
    return max(1, int(chars / CHARS_PER_TOKEN) + max(0, int(max_tokens or 0)))


def _nim_optimization_enabled(cfg: dict[str, Any] | None) -> bool:
    cfg = cfg or {}
    return bool(cfg.get("nim_optimization_enabled", True))


def _effective_limits(cfg: dict[str, Any] | None, *, key: str) -> tuple[int, float, bool]:
    cfg = cfg or {}
    nim = is_nim_config(cfg) and _nim_optimization_enabled(cfg)

    try:
        user_rpm = int(cfg.get("rpm_limit") or 0)
    except (TypeError, ValueError):
        user_rpm = 0
    try:
        user_interval = float(cfg.get("min_interval_sec") or 0)
    except (TypeError, ValueError):
        user_interval = 0.0

    if nim:
        rpm = user_rpm if user_rpm > 0 else NIM_DEFAULT_RPM
        rpm = max(1, min(rpm, NIM_MAX_EFFECTIVE_RPM))
        # NIM 对 burst / TPM 更敏感，强制最小间隔以避免瞬时尖峰。
        min_interval = max(user_interval, 60.0 / rpm)
        return rpm, min_interval, True

    return max(0, user_rpm), max(0.0, user_interval), False


def _request_units(total_tokens: int, *, nim: bool) -> int:
    if not nim:
        return 1
    return max(1, min(NIM_MAX_UNITS, math.ceil(max(1, total_tokens) / NIM_TOKENS_PER_SLOT)))


async def acquire_ai_budget(
    module: str,
    cfg: dict[str, Any] | None,
    *,
    key: str = "ai",
    messages: list[dict[str, Any]] | None = None,
    prompt_chars: int = 0,
    max_tokens: int = 0,
    on_log: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Reserve global request budget before calling an AI provider.

    For NIM, a large prompt counts as multiple logical slots. This is not exact TPM
    accounting, but it smooths high-token bursts across Chat / asset analysis / OCR.
    """
    rpm, min_interval, nim = _effective_limits(cfg, key=key)
    rl_mod.limiter.configure(key, rpm_limit=rpm, min_interval_sec=min_interval)

    total_tokens = estimate_tokens_from_messages(messages, prompt_chars=prompt_chars, max_tokens=max_tokens)
    units = _request_units(total_tokens, nim=nim)
    waited_total = 0.0
    last_reason = ""

    log_ai_event(
        module,
        "ai_budget_reserve_start",
        config=safe_ai_config(cfg),
        key=key,
        provider="nim" if is_nim_config(cfg) else "generic",
        nim_optimization_enabled=nim,

        effective_rpm=rpm,
        effective_min_interval=round(min_interval, 2),
        estimated_total_tokens=total_tokens,
        request_units=units,
    )

    for i in range(units):
        def _waiting(secs: float, reason: str) -> None:
            nonlocal last_reason
            last_reason = reason
            log_ai_event(
                module,
                "ai_budget_wait",
                key=key,
                unit=i + 1,
                request_units=units,
                wait_sec=round(secs, 1),
                reason=reason,
            )
            if on_log is not None:
                try:
                    r = on_log(f"⏳ AI 全局限速等待 {secs:.1f}s（{reason}，预算片 {i + 1}/{units}）")
                    if asyncio.iscoroutine(r):
                        asyncio.create_task(r)
                except Exception:
                    pass

        info = await rl_mod.limiter.wait_for_slot(key, on_waiting=_waiting, max_wait=180.0)
        waited_total += float(info.get("waited") or 0.0)

    result = {
        "key": key,
        "provider": "nim" if is_nim_config(cfg) else "generic",
        "nim_optimization_enabled": nim,
        "estimated_total_tokens": total_tokens,

        "request_units": units,
        "waited_sec": round(waited_total, 1),
        "reason": last_reason,
        "effective_rpm": rpm,
        "effective_min_interval": round(min_interval, 2),
    }
    log_ai_event(module, "ai_budget_reserved", **result)
    return result


def is_rate_or_server_error(exc: Exception) -> bool:
    msg = str(exc)
    low = msg.lower()
    typ = type(exc).__name__
    return (
        "429" in msg
        or "too many" in low
        or typ == "RateLimitError"
        or any(x in msg for x in ("500", "502", "503", "504", "Bad Gateway", "Service Unavailable", "Gateway Time"))
    )


async def penalize_from_exception(module: str, cfg: dict[str, Any] | None, exc: Exception, *, key: str = "ai") -> None:

    msg = str(exc)
    low = msg.lower()
    typ = type(exc).__name__
    is_429 = "429" in msg or "too many" in low or typ == "RateLimitError"
    is_5xx = any(x in msg for x in ("500", "502", "503", "504", "Bad Gateway", "Service Unavailable", "Gateway Time"))
    if not (is_429 or is_5xx):
        return

    pause = 30.0 if is_429 else 10.0
    try:
        resp = getattr(exc, "response", None)
        if resp is not None and hasattr(resp, "headers"):
            ra = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
            if ra:
                pause = max(1.0, min(180.0, float(ra)))
    except Exception:
        pass

    await rl_mod.limiter.penalize(key, pause_sec=pause, reason="429" if is_429 else "5xx")
    log_ai_event(
        module,
        "ai_budget_penalized",
        level="warning",
        key=key,
        provider="nim" if is_nim_config(cfg) else "generic",
        pause_sec=pause,
        error_type=typ,
        error=msg[:1000],
    )


def penalize_from_exception_sync(module: str, cfg: dict[str, Any] | None, exc: Exception, *, key: str = "ai") -> None:
    """Sync wrapper for worker threads / sync agents such as Hermes."""
    try:
        asyncio.run(penalize_from_exception(module, cfg, exc, key=key))
    except RuntimeError:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(penalize_from_exception(module, cfg, exc, key=key))
        except Exception:
            pass
    except Exception:
        pass

