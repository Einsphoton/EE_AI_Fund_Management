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
        # 批量分析的最大并发度（1=串行）。
        # 默认 1：reasoning 模型（R1/Qwen3-thinking）单次请求耗时 60-90s，
        # 经 Cloudflare 时有 120s 硬超时，并发越高越容易踩 524。
        # 普通对话模型 + 内网直连可以手动调到 3-6。
        "batch_concurrency": 1,
        # 单次 LLM 响应的最大 token 数（0 = 不限制）。
        # 默认 4096：
        # - 普通对话模型只用其中 1500-2500 写完整 JSON（够用）
        # - reasoning 模型 reasoning 段会吃 2000-3000，再加 content 1000，4096 是平衡点
        # 设得太低（如 800）reasoning 模型会被截断，永远写不出 content；设得太高
        # （如 8192+）经 CF 时会因生成耗时过长触发 524 超时。
        "max_tokens": 4096,
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
    "vision": {
        # 多模态视觉模型，用于截图 OCR 解析持仓页。
        # 留空 base_url/api_key 表示未配置 → 前端会引导用户去填。
        # 推荐：阿里通义 qwen-vl-max（中文金融 App 截图识别非常准）
        # 备选：智谱 GLM-4V / OpenAI gpt-4o / 本地 Ollama qwen2.5-vl
        "base_url": "",
        "api_key": "",
        "model": "",
        "temperature": 0.1,
        # 单图 token 上限（视觉模型 token 计数包含图片，4096 足够）
        "max_tokens": 4096,
        "timeout": 180,
        # OCR 批量处理的并发度（视觉模型计费贵，建议 1-2）
        "concurrency": 2,
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
