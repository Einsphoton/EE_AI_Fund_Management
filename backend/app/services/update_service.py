"""在线更新服务：检查 Docker Hub 镜像版本，并通过 Watchtower HTTP API 触发容器更新。"""
from __future__ import annotations

import os
import re
from typing import Any

import httpx

_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_CONFIRM_TEXT = "UPDATE_NOW"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _semver_key(value: str) -> tuple[int, int, int] | None:
    m = _SEMVER_RE.match(value.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _strip_image_tag(image: str) -> str:
    image = image.split("@", 1)[0]
    slash = image.rfind("/")
    colon = image.rfind(":")
    if colon > slash:
        return image[:colon]
    return image


def dockerhub_repo_from_image(image: str) -> str:
    """从 docker image 字符串推导 Docker Hub repo，例如 docker.io/u/app:latest -> u/app。"""
    image = _strip_image_tag((image or "").strip())
    if not image:
        return ""
    if image.startswith("docker.io/"):
        image = image[len("docker.io/") :]
    elif image.startswith("index.docker.io/"):
        image = image[len("index.docker.io/") :]
    elif image.startswith("registry-1.docker.io/"):
        image = image[len("registry-1.docker.io/") :]

    elif "." in image.split("/", 1)[0] or ":" in image.split("/", 1)[0]:
        return ""
    if "/" not in image:
        image = f"library/{image}"
    return image


def runtime_config() -> dict[str, Any]:
    image = os.getenv("UPDATE_IMAGE", "").strip()
    dockerhub_repo = os.getenv("UPDATE_DOCKERHUB_REPO", "").strip() or dockerhub_repo_from_image(image)
    token = os.getenv("UPDATE_WATCHTOWER_TOKEN", "").strip()
    web_trigger_enabled = _env_bool("UPDATE_ENABLE_WEB_TRIGGER", False) and bool(token)
    return {
        "current_version": os.getenv("APP_VERSION", "local"),
        "current_revision": os.getenv("VCS_REF", ""),
        "build_date": os.getenv("BUILD_DATE", ""),
        "image": image,
        "dockerhub_repo": dockerhub_repo,
        "watchtower_url": os.getenv("UPDATE_WATCHTOWER_URL", "http://ee-fund-watchtower:8080/v1/update"),
        "watchtower_configured": bool(token),
        "web_update_enabled": web_trigger_enabled,
        "confirm_text": _CONFIRM_TEXT,
    }


async def _fetch_dockerhub_latest(repo: str) -> dict[str, Any] | None:
    if not repo:
        return None
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags?page_size=100&ordering=last_updated"
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        payload = resp.json()

    results = payload.get("results") or []
    if not isinstance(results, list) or not results:
        return None

    semver_tags: list[dict[str, Any]] = []
    for item in results:
        name = str(item.get("name") or "")
        key = _semver_key(name)
        if key:
            semver_tags.append({"item": item, "key": key})

    selected = max(semver_tags, key=lambda x: x["key"])["item"] if semver_tags else results[0]
    images = selected.get("images") or []
    digest = ""
    if isinstance(images, list) and images:
        digest = str((images[0] or {}).get("digest") or "")

    return {
        "latest_version": str(selected.get("name") or ""),
        "latest_updated_at": selected.get("last_updated") or "",
        "latest_digest": digest,
        "source": "dockerhub",
        "checked_repo": repo,
    }


def _is_update_available(current: str, latest: str) -> bool | None:
    cur_key = _semver_key(current or "")
    latest_key = _semver_key(latest or "")
    if cur_key and latest_key:
        return latest_key > cur_key
    if current and latest and current.lstrip("v") == latest.lstrip("v"):
        return False
    return None


async def get_update_status() -> dict[str, Any]:
    cfg = runtime_config()
    latest: dict[str, Any] = {}
    check_error = ""
    try:
        fetched = await _fetch_dockerhub_latest(cfg["dockerhub_repo"])
        if fetched:
            latest = fetched
    except Exception as e:  # noqa: BLE001 - 对外返回诊断信息，不让状态页 500
        check_error = f"{type(e).__name__}: {e}"

    latest_version = str(latest.get("latest_version") or "")
    update_available = _is_update_available(str(cfg["current_version"]), latest_version) if latest_version else None
    message = ""
    if not cfg["dockerhub_repo"]:
        message = "未配置 UPDATE_DOCKERHUB_REPO，无法从 Docker Hub 检查最新镜像。"
    elif check_error:
        message = "检查 Docker Hub 最新镜像失败，请确认仓库名和网络连通性。"
    elif update_available is None:
        message = "已获取镜像信息，但当前版本不是语义化版本，无法自动判断新旧；仍可手动触发更新。"
    elif update_available:
        message = "检测到可用新版本。"
    else:
        message = "当前已是最新语义化版本。"

    return {
        **cfg,
        **latest,
        "update_available": update_available,
        "check_error": check_error,
        "message": message,
    }


async def trigger_update(confirm: str) -> dict[str, Any]:
    cfg = runtime_config()
    if not cfg["web_update_enabled"]:
        raise PermissionError("网页触发更新未启用。请设置 UPDATE_ENABLE_WEB_TRIGGER=true 并配置 UPDATE_WATCHTOWER_TOKEN。")
    if confirm != _CONFIRM_TEXT:
        raise ValueError(f"confirm 必须等于 { _CONFIRM_TEXT }")

    headers = {"Authorization": f"Bearer {os.getenv('UPDATE_WATCHTOWER_TOKEN', '').strip()}"}
    timeout = float(os.getenv("UPDATE_WATCHTOWER_TIMEOUT", "120") or "120")
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.post(str(cfg["watchtower_url"]), headers=headers)

    text = resp.text.strip()
    if resp.status_code >= 400:
        raise RuntimeError(f"Watchtower 返回 {resp.status_code}: {text[:500]}")

    return {
        "ok": True,
        "status_code": resp.status_code,
        "message": "已向 Watchtower 发送更新指令；如果拉到新镜像，容器会自动重启，页面可能短暂断开。",
        "watchtower_response": text[:1000],
    }
