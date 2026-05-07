"""KV settings helper."""
from __future__ import annotations

from typing import Any
from sqlalchemy.orm import Session

from .. import models


DEFAULTS: dict[str, Any] = {
    "ai": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "temperature": 0.4,
        # 批量分析的最大并发度（1=串行，云端模型建议 3-6，自建 Ollama 建议 1-2）
        "batch_concurrency": 4,
        # 单次 LLM 响应的最大 token 数（0 = 不限制）。结构化 JSON 输出用 800 已足够
        "max_tokens": 800,
        # HTTP 超时（秒）。本地 Ollama 吐丰富 JSON 可能较慢
        "timeout": 180,
        # 投资者性格：见 agent/profiles.py INVESTOR_PROFILES
        # balanced / conservative / aggressive / income / growth / value / trader
        "investor_profile": "balanced",
        # 分析报告风格：pro（专业）/ beginner（新手）
        "report_style": "pro",
        # Cloudflare Access Service Token（用于调用受 CF Zero Trust 保护的自建 API）
        # 留空表示不启用；非空时会在请求 Header 中注入 CF-Access-Client-Id/Secret
        "cf_access_client_id": "",
        "cf_access_client_secret": "",
        # 逗号分隔的域名列表，只有 base_url 包含其中任意一项时才注入上面的 CF Header
        # 默认为空 = 只要配置了 Client Id/Secret，对所有请求都注入
        "cf_access_hosts": "",
    },
    "schedule": {
        "enabled": False,
        "cron": "0 9 * * *",      # 每天 9:00
        "preset": "daily",         # daily | every6h | weekly | custom
    },
    "ui": {
        "currency": "CNY",
        "theme": "dark",
    },
}


def get(db: Session, key: str) -> Any:
    row = db.query(models.AppSetting).filter_by(key=key).first()
    if row is None:
        return DEFAULTS.get(key)
    return row.value


def get_all(db: Session) -> dict[str, Any]:
    out = {**DEFAULTS}
    for row in db.query(models.AppSetting).all():
        out[row.key] = row.value
    return out


def set_value(db: Session, key: str, value: Any) -> Any:
    row = db.query(models.AppSetting).filter_by(key=key).first()
    if row is None:
        row = models.AppSetting(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    db.commit()
    return value
