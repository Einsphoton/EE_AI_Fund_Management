"""Structured application logging for Docker/NAS diagnostics."""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import socket
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .config import settings

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_user_id: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="-")

_SENSITIVE_KEY_RE = re.compile(
    r"(^|[_-])(api[_-]?key|authorization|bearer|access[_-]?token|refresh[_-]?token|client[_-]?secret|secret|password|passwd|cookie)([_-]|$)",
    re.I,
)
_SECRET_VALUE_RE = re.compile(

    r"(?i)(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{12,}|[A-Za-z0-9]{32,}\.access|[a-f0-9]{48,})"
)
_STANDARD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "processName", "process", "message",
    "asctime", "taskName",
}


def log_dir() -> Path:
    path = Path(settings.data_dir) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def set_log_context(*, request_id: str | None = None, user_id: int | str | None = None) -> None:
    if request_id is not None:
        _request_id.set(str(request_id))
    if user_id is not None:
        _user_id.set(str(user_id))


def get_request_id() -> str:
    return _request_id.get()


def redact_text(text: str) -> str:
    if not text:
        return text
    text = _SECRET_VALUE_RE.sub("***REDACTED***", str(text))
    text = re.sub(r"(?i)(api[_-]?key|authorization|token|secret|password)(\s*[:=]\s*)[^\s,;)}]+", r"\1\2***REDACTED***", text)
    return text


def redact_obj(value: Any, *, max_str: int = 1200) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        s = redact_text(value)
        return s if len(s) <= max_str else s[:max_str] + "…"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if _SENSITIVE_KEY_RE.search(key) and not key.lower().startswith("has_"):
                out[key] = "***REDACTED***" if v else ""
            else:
                out[key] = redact_obj(v, max_str=max_str)

        return out
    if isinstance(value, (list, tuple, set)):
        return [redact_obj(v, max_str=max_str) for v in list(value)[:50]]
    return redact_text(str(value))


def safe_ai_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    cfg = cfg or {}
    return {
        "base_url": cfg.get("base_url") or "",
        "model": cfg.get("model") or "",
        "temperature": cfg.get("temperature"),
        "timeout": cfg.get("timeout"),
        "max_tokens": cfg.get("max_tokens"),
        "batch_concurrency": cfg.get("batch_concurrency"),
        "rpm_limit": cfg.get("rpm_limit"),
        "min_interval_sec": cfg.get("min_interval_sec"),
        "nim_optimization_enabled": cfg.get("nim_optimization_enabled"),
        "thinking_mode": cfg.get("thinking_mode"),

        "thinking_budget": cfg.get("thinking_budget"),
        "reasoning_effort": cfg.get("reasoning_effort"),
        "cf_access_hosts": cfg.get("cf_access_hosts") or "",
        "has_api_key": bool(cfg.get("api_key")),
        "has_cf_access_client_id": bool(cfg.get("cf_access_client_id")),
        "has_cf_access_client_secret": bool(cfg.get("cf_access_client_secret")),
    }


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        record.user_id = _user_id.get()
        return True


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "line": record.lineno,
            "request_id": getattr(record, "request_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
            "message": redact_text(record.getMessage()),
        }
        for k, v in record.__dict__.items():
            if k in _STANDARD_ATTRS or k.startswith("_") or k in data:
                continue
            data[k] = redact_obj(v)
        if record.exc_info:
            data["exception"] = redact_text("".join(traceback.format_exception(*record.exc_info))[-6000:])
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class RedactingTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def _max_bytes() -> int:
    try:
        return int(os.getenv("LOG_MAX_BYTES", "10485760"))
    except ValueError:
        return 10 * 1024 * 1024


def _backup_count() -> int:
    try:
        return int(os.getenv("LOG_BACKUP_COUNT", "5"))
    except ValueError:
        return 5


def _file_handler(filename: str, level: int) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        log_dir() / filename,
        maxBytes=_max_bytes(),
        backupCount=_backup_count(),
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(RedactingJsonFormatter())
    handler.addFilter(ContextFilter())
    return handler


def setup_logging() -> None:
    if getattr(setup_logging, "_configured", False):
        return
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(RedactingTextFormatter("%(asctime)s %(levelname)s [%(name)s] [rid=%(request_id)s uid=%(user_id)s] %(message)s"))
    console.addFilter(ContextFilter())
    root.addHandler(console)
    root.addHandler(_file_handler("app.log", level))
    root.addHandler(_file_handler("errors.log", logging.ERROR))

    ai_logger = logging.getLogger("app.ai")
    ai_logger.setLevel(level)
    ai_logger.addHandler(_file_handler("ai.log", level))
    ai_logger.propagate = True

    for noisy in ("httpx", "httpcore", "urllib3", "apscheduler"):
        logging.getLogger(noisy).setLevel(os.getenv("LOG_NOISY_LEVEL", "WARNING").upper())

    logging.getLogger("app").info(
        "logging_initialized",
        extra={
            "event": "logging_initialized",
            "log_dir": str(log_dir()),
            "host": socket.gethostname(),
            "level_configured": level_name,
        },
    )
    setup_logging._configured = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)


def get_ai_logger(module: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"app.ai.{module}")


def log_ai_event(module: str, event: str, *, level: str = "info", message: str | None = None, **fields: Any) -> None:
    logger = get_ai_logger(module)
    log_fn = getattr(logger, level.lower(), logger.info)
    safe_fields = {k: redact_obj(v) for k, v in fields.items()}
    log_fn(message or event, extra={"event": event, **safe_fields})
