"""AI provider pool utilities for OpenAI-compatible endpoints.

The legacy `settings.ai` shape remains valid. Optional `settings.ai.providers`
adds extra provider/key entries for batch asset analysis, each with its own
rate-limit key so independent API quotas can be used safely.
"""
from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any

_POOL_LOCK = threading.Lock()
_POOL_CURSOR: dict[str, int] = {}

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


def choose_provider_sequence(ai_cfg: dict[str, Any] | None, *, purpose: str = "asset-analysis") -> list[dict[str, Any]]:
    """Pick a weighted round-robin start provider, then append failover candidates."""
    pool = build_provider_pool(ai_cfg)
    if len(pool) <= 1:
        return pool

    weighted: list[dict[str, Any]] = []
    for p in pool:
        weighted.extend([p] * _as_positive_int(p.get("_provider_weight"), 1))
    if not weighted:
        return pool

    cursor_key = purpose
    with _POOL_LOCK:
        i = _POOL_CURSOR.get(cursor_key, 0) % len(weighted)
        _POOL_CURSOR[cursor_key] = i + 1
    start_id = weighted[i].get("_provider_id")

    start_index = 0
    for idx, p in enumerate(pool):
        if p.get("_provider_id") == start_id:
            start_index = idx
            break
    return pool[start_index:] + pool[:start_index]


def provider_label(cfg: dict[str, Any] | None) -> str:
    cfg = cfg or {}
    return str(cfg.get("_provider_name") or cfg.get("_provider_id") or "AI").strip() or "AI"


def provider_rate_key(cfg: dict[str, Any] | None) -> str:
    cfg = cfg or {}
    return str(cfg.get("_provider_rate_key") or "ai")
