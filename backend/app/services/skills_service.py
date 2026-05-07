"""Skill marketplace service.

集成 https://skillhub.cloud.tencent.com/ 财经类 Skill。
该站点暂未提供稳定公开 API，因此采用以下策略：
1. 维护一份精选的财经类 Skill 目录（CURATED）。
2. 尝试调用一个可配置的 marketplace 端点（如可达），合并结果。
3. 用户可手动安装任意 Skill（自带 Skill ID/名字）。

Skill 在本平台是一段 prompt + 元数据；安装后即可被 AI Agent 使用。
"""
from __future__ import annotations

import json
from typing import Any
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from .. import models
from ..config import settings


SKILLHUB_BASE = "https://skillhub.cloud.tencent.com"

# 精选财经类 Skill 列表（默认两条会自动安装）
CURATED: list[dict[str, Any]] = [
    {
        "skill_id": "stock-analysis",
        "name": "Stock Analysis",
        "description": "通用美股 / A 股 / 港股技术 + 基本面综合分析 Skill，输出 BUY / HOLD / SELL 建议、风险点和理由。",
        "category": "finance",
        "source": "builtin",
        "default": True,
        "prompt": (
            "你是资深的二级市场分析师。请根据用户提供的标的代码、近 30~180 日的 K 线、最近一笔成交价、"
            "持仓成本与盈亏，给出严谨、克制的投资建议。\n"
            "要求：\n"
            "1) 先用一句话给出结论：BUY / HOLD / SELL 之一，并给出 0~1 的置信度。\n"
            "2) 列出关键技术指标观察（趋势、均线、量价）。\n"
            "3) 列出至少 2 个基本面或行业事件假设。\n"
            "4) 提示风险点。\n"
            "5) 严格输出 JSON：{\"action\": \"buy|hold|sell\", \"confidence\": 0.0~1.0, "
            "\"summary\": \"...\", \"detail\": \"...\"}"
        ),
    },
    {
        "skill_id": "tushare-finance",
        "name": "Tushare Finance",
        "description": "面向 A 股的财务报表与基本面分析 Skill，结合 Tushare 风格的指标（PE/PB/ROE/营收增速）输出建议。",
        "category": "finance",
        "source": "builtin",
        "default": True,
        "prompt": (
            "你是 A 股基本面研究员。请基于持仓信息与近 1 年价格走势，从估值（PE/PB）、盈利（ROE/净利润）、"
            "成长（营收/利润同比）、行业景气、机构持仓等维度做严谨分析，输出 JSON："
            "{\"action\":\"buy|hold|sell\",\"confidence\":0.0~1.0,\"summary\":\"...\",\"detail\":\"...\"}"
        ),
    },
    {
        "skill_id": "fund-screener",
        "name": "Fund Screener",
        "description": "公募基金筛选与定投建议 Skill，关注夏普、最大回撤、阶段涨跌幅。",
        "category": "finance",
        "source": "skillhub",
        "default": False,
        "prompt": (
            "你是公募基金研究员。基于基金净值序列与持仓成本，从夏普比率、最大回撤、阶段涨跌幅角度评估，"
            "输出 JSON：{\"action\":\"buy|hold|sell\",\"confidence\":0.0~1.0,\"summary\":\"...\",\"detail\":\"...\"}"
        ),
    },
    {
        "skill_id": "macro-radar",
        "name": "Macro Radar",
        "description": "宏观风向 Skill：综合美债收益率、汇率、大宗商品、央行政策做大势研判。",
        "category": "finance",
        "source": "skillhub",
        "default": False,
        "prompt": (
            "你是宏观策略分析师。请从美债、汇率、大宗、央行政策角度做宏观研判，并对输入标的提示影响，"
            "输出 JSON：{\"action\":\"buy|hold|sell\",\"confidence\":0.0~1.0,\"summary\":\"...\",\"detail\":\"...\"}"
        ),
    },
    {
        "skill_id": "options-flow",
        "name": "Options Flow",
        "description": "美股期权大单异动跟踪 Skill。",
        "category": "finance",
        "source": "skillhub",
        "default": False,
        "prompt": (
            "你是美股期权策略师。基于标的近期波动率与假想期权大单流，给出方向判断。"
            "输出 JSON：{\"action\":\"buy|hold|sell\",\"confidence\":0.0~1.0,\"summary\":\"...\",\"detail\":\"...\"}"
        ),
    },
    {
        "skill_id": "crypto-pulse",
        "name": "Crypto Pulse",
        "description": "加密货币情绪与链上数据 Skill（仅作为辅助参考）。",
        "category": "finance",
        "source": "skillhub",
        "default": False,
        "prompt": (
            "你是加密市场分析师。请基于行情序列与链上情绪做研判，"
            "输出 JSON：{\"action\":\"buy|hold|sell\",\"confidence\":0.0~1.0,\"summary\":\"...\",\"detail\":\"...\"}"
        ),
    },
]


def _curated_dict() -> dict[str, dict[str, Any]]:
    return {s["skill_id"]: s for s in CURATED}


async def list_marketplace(category: str = "finance", keyword: str = "") -> list[dict[str, Any]]:
    """查询可安装 Skill 列表。

    优先返回精选 + 合并 skillhub 远程结果（若可用）。
    """
    items = [s for s in CURATED if s["category"] == category or category in ("", "all")]

    # 尝试请求 skillhub（公开站点，未提供稳定 API；这里做容错探测）
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(
                f"{SKILLHUB_BASE}/api/skills",
                params={"category": category, "keyword": keyword},
                headers={"User-Agent": "ee-fund/1.0"},
            )
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                remote = r.json().get("data") or []
                exist_ids = {s["skill_id"] for s in items}
                for it in remote:
                    sid = it.get("id") or it.get("skill_id")
                    if not sid or sid in exist_ids:
                        continue
                    items.append({
                        "skill_id": sid,
                        "name": it.get("name") or sid,
                        "description": it.get("description", ""),
                        "category": it.get("category", "finance"),
                        "source": "skillhub",
                        "default": False,
                    })
    except Exception:
        pass

    if keyword:
        kw = keyword.lower()
        items = [s for s in items if kw in s["name"].lower() or kw in s["description"].lower()]
    return items


def get_skill_prompt(skill_id: str) -> str:
    s = _curated_dict().get(skill_id)
    if s:
        return s.get("prompt", "")
    # 从安装目录读取
    p = Path(settings.skills_dir) / f"{skill_id}.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get("prompt", "")
        except Exception:
            return ""
    return ""


def install_skill(db: Session, payload: dict[str, Any]) -> models.Skill:
    skill_id = payload["skill_id"]
    existing = db.query(models.Skill).filter_by(skill_id=skill_id).first()
    if existing:
        existing.enabled = True
        db.commit()
        db.refresh(existing)
        return existing

    skill = models.Skill(
        skill_id=skill_id,
        name=payload.get("name", skill_id),
        description=payload.get("description", ""),
        category=payload.get("category", "finance"),
        source=payload.get("source", "skillhub"),
        enabled=True,
        config={},
    )
    db.add(skill)

    # 持久化 prompt 到 skills_installed 目录
    cur = _curated_dict().get(skill_id)
    if cur:
        Path(settings.skills_dir).mkdir(parents=True, exist_ok=True)
        (Path(settings.skills_dir) / f"{skill_id}.json").write_text(
            json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    db.commit()
    db.refresh(skill)
    return skill


def uninstall_skill(db: Session, skill_id: str) -> bool:
    row = db.query(models.Skill).filter_by(skill_id=skill_id).first()
    if not row:
        return False
    db.delete(row)
    db.commit()
    p = Path(settings.skills_dir) / f"{skill_id}.json"
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass
    return True


def ensure_default_skills(db: Session) -> None:
    """首次启动时安装默认 Skill。"""
    for s in CURATED:
        if not s.get("default"):
            continue
        if not db.query(models.Skill).filter_by(skill_id=s["skill_id"]).first():
            install_skill(db, s)
