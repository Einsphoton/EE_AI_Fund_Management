"""Settings API."""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel

from ..auth import get_current_user
from ..database import get_db
from .. import models

from ..services import settings_service
from ..agent.profiles import list_profiles_public, list_report_styles_public
from ..logging_config import log_ai_event, safe_ai_config
from .. import scheduler as scheduler_mod


router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/profiles")
def get_profiles():
    """返回所有可用的「投资性格」与「报告风格」预设，供设置页渲染。"""
    return {
        "investor_profiles": list_profiles_public(),
        "report_styles": list_report_styles_public(),
    }


class UpdatePayload(BaseModel):
    value: Any


@router.get("")
def get_all(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return settings_service.get_all(db, user_id=current_user.id)



@router.get("/debug/cf-access")
def debug_cf_access(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):

    """只读诊断接口：查看 DB 中 CF Access 配置是否真的存进去了。

    出于安全考虑，只返回是否存在 + Client Id 脱敏，不会泄漏 Secret。
    """
    import os
    ai = settings_service.get(db, "ai", user_id=current_user.id) or {}

    cf_id = str(ai.get("cf_access_client_id") or "").strip()
    cf_sec = str(ai.get("cf_access_client_secret") or "").strip()
    cf_hosts = str(ai.get("cf_access_hosts") or "").strip()
    env_id = os.getenv("CF_ACCESS_CLIENT_ID", "").strip()
    env_sec = os.getenv("CF_ACCESS_CLIENT_SECRET", "").strip()
    return {
        "db": {
            "has_client_id": bool(cf_id),
            "has_client_secret": bool(cf_sec),
            "client_id_prefix": cf_id[:8] if cf_id else "",
            "client_id_suffix": cf_id[-8:] if cf_id else "",
            "client_id_length": len(cf_id),
            "client_secret_length": len(cf_sec),
            "hosts": cf_hosts,
        },
        "env_fallback": {
            "has_client_id": bool(env_id),
            "has_client_secret": bool(env_sec),
        },
        "base_url": (ai.get("base_url") or ""),
        "will_inject": bool(
            (cf_id or env_id) and (cf_sec or env_sec)
        ),
    }


@router.put("/{key}")
def put_setting(
    key: str,
    payload: UpdatePayload,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    settings_service.set_value(db, key, payload.value, user_id=current_user.id)

    if key == "schedule":
        cron = scheduler_mod.reload_schedule()
        return {"ok": True, "applied_cron": cron}
    return {"ok": True}


class TestAiPayload(BaseModel):
    base_url: str
    api_key: str = ""
    model: str = ""
    cf_access_client_id: str = ""
    cf_access_client_secret: str = ""
    cf_access_hosts: str = ""


@router.post("/test-ai")
async def test_ai(p: TestAiPayload):
    """诊断大模型 API 连接性 + 模型是否存在.

    1) GET {base}/models  (OpenAI-compatible) — 列出可用模型
    2) 若失败，尝试 GET {base/v1 -> base}/api/tags  (Ollama 原生)
    """
    base = p.base_url.rstrip("/")
    log_ai_event(
        "settings",
        "test_ai_start",
        config=safe_ai_config(p.model_dump()),
    )
    headers: dict[str, str] = {}

    if p.api_key:
        headers["Authorization"] = f"Bearer {p.api_key}"

    # 注入 Cloudflare Access Service Token（若配置了）
    cf_id = (p.cf_access_client_id or "").strip()
    cf_sec = (p.cf_access_client_secret or "").strip()
    cf_hosts_raw = (p.cf_access_hosts or "").strip()
    if cf_id and cf_sec:
        if cf_hosts_raw:
            cf_hosts = [h.strip().lower() for h in cf_hosts_raw.split(",") if h.strip()]
            hit = any(h in base.lower() for h in cf_hosts)
        else:
            hit = True
        if hit:
            headers["CF-Access-Client-Id"] = cf_id
            headers["CF-Access-Client-Secret"] = cf_sec

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
        # 1) OpenAI 兼容 /models —— 带最多 3 次手动跟随重定向，每次都重新带 Header
        try:
            target = f"{base}/models"
            r = None
            redirect_trail: list[str] = []
            for _ in range(4):
                r = await client.get(target, headers=headers)
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location", "")
                    if not loc:
                        break
                    # 处理相对路径
                    if loc.startswith("/"):
                        from urllib.parse import urlparse
                        u = urlparse(target)
                        loc = f"{u.scheme}://{u.netloc}{loc}"
                    redirect_trail.append(f"{r.status_code} → {loc}")
                    # 检测到登录页直接跳出
                    if "cloudflareaccess.com" in loc or "/cdn-cgi/access/" in loc:
                        break
                    # 检测到死循环跳出
                    if loc == target:
                        break
                    target = loc
                    continue
                break

            assert r is not None
            if r.status_code == 200:
                data = r.json()
                ids: list[str] = []

                def _append_ids(payload: Any) -> None:
                    if not isinstance(payload, dict):
                        return
                    for it in payload.get("data") or []:
                        if isinstance(it, dict) and it.get("id"):
                            mid = str(it["id"])
                            if mid not in ids:
                                ids.append(mid)

                _append_ids(data)

                # 兼容部分 OpenAI-compatible 服务的分页形态。NVIDIA NIM 的模型列表较长，
                # 旧实现只返回前 50 个，用户会误以为模型不全；这里尽量拉全且不再截断。
                page_target = target
                page_payload = data if isinstance(data, dict) else {}
                for _page in range(8):
                    next_url = ""
                    if isinstance(page_payload, dict):
                        links = page_payload.get("links") or {}
                        raw_next = page_payload.get("next") or (links.get("next") if isinstance(links, dict) else "")
                        if isinstance(raw_next, str):
                            next_url = raw_next

                        elif page_payload.get("has_more") and ids:
                            from urllib.parse import urlencode
                            sep = "&" if "?" in target else "?"
                            next_url = f"{target}{sep}{urlencode({'after': ids[-1], 'limit': 1000})}"
                    if not next_url:
                        break
                    if next_url.startswith("/"):
                        from urllib.parse import urlparse
                        u = urlparse(page_target)
                        next_url = f"{u.scheme}://{u.netloc}{next_url}"
                    if next_url == page_target:
                        break
                    page_target = next_url
                    r_next = await client.get(page_target, headers=headers)
                    if r_next.status_code != 200:
                        break
                    try:
                        page_payload = r_next.json()
                    except Exception:
                        break
                    before = len(ids)
                    _append_ids(page_payload)
                    if len(ids) == before:
                        break

                model_ok = (not p.model) or (p.model in ids) if ids else False
                hint_parts = []
                if redirect_trail:
                    hint_parts.append("（经过重定向：" + " → ".join(redirect_trail) + "）")
                if ids:
                    hint_parts.append(f"已从模型列表接口读取 {len(ids)} 个模型；不会再截断为前 50 个。")
                if p.model and not model_ok:
                    hint_parts.append(f"Model `{p.model}` 不在可用列表中。请确认服务商返回的真实模型名。")
                return {
                    "ok": True,
                    "endpoint": target,
                    "models": ids,
                    "model_exists": model_ok,
                    "hint": "\n".join(hint_parts),
                }


            # 仍然是重定向状态码 → 说明循环了或最终进了登录页
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("location", "")
                cf_hint = ""
                ray_id = r.headers.get("cf-ray", "")
                server = r.headers.get("server", "")
                trail_str = (" | 轨迹：" + " → ".join(redirect_trail)) if redirect_trail else ""

                if "cloudflareaccess.com" in loc or "/cdn-cgi/access/" in loc:
                    cf_hint = (
                        "\n\n🔴 检测到 Cloudflare Access 登录跳转——"
                        "Service Token 未被识别。请确认：\n"
                        "  1) Zero Trust → Access → Applications → 该应用的策略 Action "
                        "必须是 `Service Auth`（服务身份验证），不是 `Allow`。\n"
                        "  2) 策略的 Include 里必须把这个 Service Token 加进去。\n"
                        "  3) Client Id / Secret 没写反（Id 以 `.access` 结尾，Secret 是 64 位十六进制）。"
                    )
                elif loc == target or (redirect_trail and redirect_trail[-1].endswith(target)):
                    cf_hint = (
                        "\n\n⚠️ 死循环重定向：跳转目标和原 URL 完全一样。\n"
                        "  常见原因：\n"
                        "  - Base URL 结尾多写了 `/`（正确写法：https://ollama.einsphoton.ren/v1，不要结尾斜杠）\n"
                        "  - Base URL 协议写成了 `http://`（应该是 https）\n"
                        "  - Cloudflare 的 SSL 模式是 Full 但源站其实返回 http 重定向"
                    )
                else:
                    cf_hint = f"\n\n⚠️ 未知重定向目标：{loc[:200]}"

                err = (
                    f"HTTP {r.status_code}: 被重定向到 `{loc[:150]}`"
                    f"{trail_str}"
                    f"{(' | CF-Ray=' + ray_id) if ray_id else ''}"
                    f"{(' | Server=' + server) if server else ''}"
                    f"{cf_hint}"
                )
            elif r.status_code == 401:
                err = (
                    f"HTTP 401: {r.text[:200]}\n\n"
                    "🔴 Cloudflare Access 拒绝了 Service Token：\n"
                    "  - 最可能：Client Secret 填错、被重置、或带了前后空格。\n"
                    "  - 其次：Service Token 已过期（去 Zero Trust → Service credentials 看有效期）。"
                )
            elif r.status_code == 403:
                err = (
                    f"HTTP 403: {r.text[:200]}\n\n"
                    "🔴 Token 有效但策略不允许访问此资源，检查 Application 的 domain 是否覆盖当前路径。"
                )
            else:
                err = f"HTTP {r.status_code}: {r.text[:200]}"
        except httpx.ConnectError as e:
            err = f"无法连接：{e}（Ollama 默认只监听 127.0.0.1，需要 OLLAMA_HOST=0.0.0.0:11434 重启）"
        except httpx.TimeoutException:
            err = "请求超时（端口可能开放但服务无响应；防火墙或 OLLAMA_HOST 未配置）"
        except Exception as e:
            err = str(e)

        # 2) Ollama 原生 /api/tags 兜底
        ollama_base = base.rstrip("/")
        if ollama_base.endswith("/v1"):
            ollama_base = ollama_base[:-3]
        try:
            r2 = await client.get(f"{ollama_base}/api/tags", headers=headers)
            if r2.status_code == 200:
                tags = (r2.json() or {}).get("models") or []
                names = [m.get("name") for m in tags if isinstance(m, dict)]
                model_ok = (not p.model) or (p.model in names)
                return {
                    "ok": True,
                    "endpoint": f"{ollama_base}/api/tags",
                    "models": names,

                    "model_exists": model_ok,
                    "hint": (
                        "OpenAI /v1/models 接口失败，但 Ollama 原生 API 通了。"
                        "请把 Base URL 设为：" + ollama_base + "/v1"
                    ) + (
                        "" if model_ok else f"\nModel `{p.model}` 不存在；可选：{', '.join(names[:10])}"
                    ),
                }
        except Exception:
            pass

    return {"ok": False, "endpoint": f"{base}/models", "error": err}
