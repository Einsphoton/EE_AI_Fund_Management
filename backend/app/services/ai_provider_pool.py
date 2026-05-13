"""AI provider pool utilities for OpenAI-compatible endpoints.

The legacy `settings.ai` shape remains valid. Optional `settings.ai.providers`
adds extra provider/key entries for batch asset analysis, each with its own
rate-limit key so independent API quotas can be used safely.
"""
from __future__ import annotations

import threading
import time
from copy import deepcopy
from typing import Any

_POOL_LOCK = threading.Lock()
_POOL_CURSOR: dict[str, int] = {}
_PROVIDER_COOLDOWN_UNTIL: dict[str, float] = {}
_PROVIDER_COOLDOWN_REASON: dict[str, str] = {}
_PROVIDER_INFLIGHT: dict[str, int] = {}
_PROVIDER_SELECTED_COUNT: dict[str, int] = {}



_PROVIDER_OVERRIDE_KEYS = {
    "id",
    "name",
    "enabled",
    "base_url",
    "api_key",
    "model",
    "temperature",
    "max_tokens",
    "timeout",
    "rpm_limit",
    "min_interval_sec",
    "nim_optimization_enabled",
    "thinking_mode",
    "thinking_budget",
    "reasoning_effort",
    "weight",
}


def _clean_id(value: Any, fallback: str) -> str:
    raw = str(value or "").strip() or fallback
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in raw)
    return safe[:80] or fallback


def _as_positive_int(value: Any, fallback: int = 1, upper: int = 20) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = fallback
    return max(1, min(upper, n))


def _base_config(ai_cfg: dict[str, Any] | None) -> dict[str, Any]:
    cfg = deepcopy(ai_cfg or {})
    cfg.pop("providers", None)
    return cfg


def _has_endpoint(cfg: dict[str, Any]) -> bool:
    return bool(str(cfg.get("base_url") or "").strip() and str(cfg.get("api_key") or "").strip())


def _provider_from_entry(base: dict[str, Any], raw: dict[str, Any], index: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or raw.get("enabled") is False:
        return None

    cfg = deepcopy(base)
    for key in _PROVIDER_OVERRIDE_KEYS:
        if key not in raw or key in {"id", "name", "enabled", "weight"}:
            continue
        value = raw.get(key)
        if value is None:
            continue
        # Empty provider fields inherit the primary AI setting; this keeps it easy
        # to add multiple NIM keys that share the same base_url/model.
        if isinstance(value, str) and value.strip() == "":
            continue
        cfg[key] = value

    pid = _clean_id(raw.get("id") or raw.get("name"), f"provider-{index + 1}")
    cfg["_provider_id"] = pid
    cfg["_provider_name"] = str(raw.get("name") or pid).strip() or pid
    cfg["_provider_weight"] = _as_positive_int(raw.get("weight"), 1)
    cfg["_provider_rate_key"] = f"ai-provider:{pid}"
    cfg["_provider_pool"] = True
    return cfg if _has_endpoint(cfg) else None


def build_provider_pool(ai_cfg: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return enabled providers, preserving legacy single-config behavior."""
    ai_cfg = ai_cfg or {}
    base = _base_config(ai_cfg)
    providers: list[dict[str, Any]] = []

    include_primary = bool(ai_cfg.get("pool_include_primary", True))
    if include_primary and _has_endpoint(base):
        primary = deepcopy(base)
        primary["_provider_id"] = "primary"
        primary["_provider_name"] = str(ai_cfg.get("pool_primary_name") or "主配置").strip() or "主配置"
        primary["_provider_weight"] = 1
        primary["_provider_rate_key"] = "ai-provider:primary"
        primary["_provider_pool"] = True
        providers.append(primary)

    for idx, raw in enumerate(ai_cfg.get("providers") or []):
        p = _provider_from_entry(base, raw, idx)
        if p is not None:
            providers.append(p)

    if providers:
        return providers

    # Legacy fallback: keep old heuristic behavior when the user has not configured
    # an API key yet, so run_agent can still return the local heuristic result.
    legacy = deepcopy(base)
    legacy["_provider_id"] = "legacy"
    legacy["_provider_name"] = "单一配置"
    legacy["_provider_weight"] = 1
    legacy["_provider_rate_key"] = "ai"
    legacy["_provider_pool"] = False
    return [legacy]


def provider_count(ai_cfg: dict[str, Any] | None) -> int:
    return len([p for p in build_provider_pool(ai_cfg) if _has_endpoint(p)])


def cooldown_remaining(cfg: dict[str, Any] | None) -> float:
    cfg = cfg or {}
    pid = str(cfg.get("_provider_id") or "")
    if not pid:
        return 0.0
    with _POOL_LOCK:
        until = _PROVIDER_COOLDOWN_UNTIL.get(pid, 0.0)
    return max(0.0, until - time.time())


def cooldown_reason(cfg: dict[str, Any] | None) -> str:
    cfg = cfg or {}
    pid = str(cfg.get("_provider_id") or "")
    with _POOL_LOCK:
        return _PROVIDER_COOLDOWN_REASON.get(pid, "")


def mark_provider_unhealthy(cfg: dict[str, Any] | None, *, cooldown_sec: float, reason: str) -> None:
    cfg = cfg or {}
    pid = str(cfg.get("_provider_id") or "")
    if not pid or cooldown_sec <= 0:
        return
    with _POOL_LOCK:
        _PROVIDER_COOLDOWN_UNTIL[pid] = max(_PROVIDER_COOLDOWN_UNTIL.get(pid, 0.0), time.time() + cooldown_sec)
        _PROVIDER_COOLDOWN_REASON[pid] = reason


def clear_provider_cooldown(cfg: dict[str, Any] | None) -> None:
    cfg = cfg or {}
    pid = str(cfg.get("_provider_id") or "")
    if not pid:
        return
    with _POOL_LOCK:
        _PROVIDER_COOLDOWN_UNTIL.pop(pid, None)
        _PROVIDER_COOLDOWN_REASON.pop(pid, None)


def _provider_id(cfg: dict[str, Any] | None) -> str:
    cfg = cfg or {}
    return str(cfg.get("_provider_id") or "")


def provider_runtime_status(cfg: dict[str, Any] | None) -> dict[str, Any]:
    pid = _provider_id(cfg)
    now = time.time()
    with _POOL_LOCK:
        until = _PROVIDER_COOLDOWN_UNTIL.get(pid, 0.0)
        return {
            "inflight": _PROVIDER_INFLIGHT.get(pid, 0),
            "selected_count": _PROVIDER_SELECTED_COUNT.get(pid, 0),
            "cooldown_remaining": max(0.0, until - now),
            "cooldown_reason": _PROVIDER_COOLDOWN_REASON.get(pid, ""),
        }



def reserve_provider(cfg: dict[str, Any] | None) -> dict[str, Any]:
    pid = _provider_id(cfg)
    if not pid:
        return {"inflight": 0, "selected_count": 0}
    with _POOL_LOCK:
        _PROVIDER_INFLIGHT[pid] = _PROVIDER_INFLIGHT.get(pid, 0) + 1
        _PROVIDER_SELECTED_COUNT[pid] = _PROVIDER_SELECTED_COUNT.get(pid, 0) + 1
        return {
            "inflight": _PROVIDER_INFLIGHT[pid],
            "selected_count": _PROVIDER_SELECTED_COUNT[pid],
        }


def release_provider(cfg: dict[str, Any] | None) -> None:
    pid = _provider_id(cfg)
    if not pid:
        return
    with _POOL_LOCK:
        cur = _PROVIDER_INFLIGHT.get(pid, 0)
        if cur <= 1:
            _PROVIDER_INFLIGHT.pop(pid, None)
        else:
            _PROVIDER_INFLIGHT[pid] = cur - 1


def choose_provider_sequence(ai_cfg: dict[str, Any] | None, *, purpose: str = "asset-analysis") -> list[dict[str, Any]]:
    """Choose providers by least in-flight load + weighted fair count.

    This is intentionally not pure round-robin: long NIM generations can keep a
    key busy for 1-3 minutes. New assets should prefer idle keys first, then use
    weighted selected-count as a fairness tie-breaker.
    """
    pool = build_provider_pool(ai_cfg)
    active_pool = [p for p in pool if cooldown_remaining(p) <= 0]
    if active_pool:
        pool = active_pool
    if len(pool) <= 1:
        return pool

    cursor_key = purpose
    with _POOL_LOCK:
        cursor = _POOL_CURSOR.get(cursor_key, 0)
        _POOL_CURSOR[cursor_key] = cursor + 1
        decorated: list[tuple[float, float, int, dict[str, Any]]] = []
        size = len(pool)
        for pos, p in enumerate(pool):
            pid = str(p.get("_provider_id") or "")
            weight = _as_positive_int(p.get("_provider_weight"), 1)
            inflight = _PROVIDER_INFLIGHT.get(pid, 0)
            selected = _PROVIDER_SELECTED_COUNT.get(pid, 0)
            rotated_pos = (pos - cursor) % size
            decorated.append((inflight / weight, selected / weight, rotated_pos, p))
    decorated.sort(key=lambda x: (x[0], x[1], x[2]))
    return [p for *_score, p in decorated]



def provider_label(cfg: dict[str, Any] | None) -> str:

    cfg = cfg or {}
    return str(cfg.get("_provider_name") or cfg.get("_provider_id") or "AI").strip() or "AI"


def provider_rate_key(cfg: dict[str, Any] | None) -> str:
    cfg = cfg or {}
    return str(cfg.get("_provider_rate_key") or "ai")
