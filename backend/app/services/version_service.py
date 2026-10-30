"""版本与升级信息：当前代码版本、数据库迁移状态、可选的最新 release 检查。

设计取舍
--------
- **最新 release 是可选增强**：`APP_UPDATE_REPO` 留空就不发任何外部请求；
  离线部署、自建镜像、内网环境仍能正常看到「当前版本 + 迁移状态」。
- **外部检查失败不报 500**：GitHub 不可达 / 限流只让「最新版本」显示检查失败，
  其余信息照常返回；结果在 Redis 缓存（成功 6h / 失败 10min），不在每次看板加载时打 GitHub。
- **不暴露仓库/Token**：响应只有 tag、名称、链接与时间。
"""

from __future__ import annotations

import json
import re
import time

import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.degrade import degrade_guard
from app.redis import redis_client
from app.services import health_service
from app.version import APP_VERSION

_RELEASE_CACHE_KEY = "ops:version:release"
_RELEASE_CACHE_TTL = 6 * 3600
_RELEASE_ERROR_TTL = 600
_SEMVER_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")

BACKUP_HINT = "升级前请先备份数据库（scripts/backup_mysql.sh）；迁移不是总能无损回滚。"


def parse_version(value: str | None) -> tuple[int, int, int] | None:
    """解析 `v1.2.3` / `1.2` 形态；不是版本号返回 None（不猜）。"""
    if not value:
        return None
    match = _SEMVER_RE.match(value.strip())
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def is_newer(current: str | None, latest: str | None) -> bool | None:
    """latest 是否比 current 新；任一无法解析返回 None（未知，不谎报有更新）。"""
    current_v = parse_version(current)
    latest_v = parse_version(latest)
    if current_v is None or latest_v is None:
        return None
    return latest_v > current_v


def pending_revisions(current: str | None, head: str | None) -> list[str] | None:
    """当前库版本到代码 head 之间待执行的迁移 revision 列表；无法判断返回 None。"""
    if not current or not head:
        return None
    script = health_service.script_directory()
    if script is None:
        return None
    revisions: list[str] | None = None
    with degrade_guard("version.pending_revisions", detail="读取迁移链失败，待执行数显示为未知"):
        collected: list[str] = []
        for revision in script.walk_revisions(base=current, head=head):
            if revision.revision == current:
                break
            collected.append(revision.revision)
        revisions = collected
    return revisions


async def _cached_release(repo: str) -> dict | None:
    cached: dict | None = None
    with degrade_guard("version.release_cache_read", detail="读取最新 release 缓存失败"):
        raw = await redis_client.get(f"{_RELEASE_CACHE_KEY}:{repo}")
        if raw:
            value = json.loads(raw)
            if isinstance(value, dict):
                cached = value
    return cached


async def _cache_release(repo: str, payload: dict) -> None:
    ttl = _RELEASE_CACHE_TTL if payload.get("tag") else _RELEASE_ERROR_TTL
    with degrade_guard("version.release_cache_write", detail="写入最新 release 缓存失败"):
        await redis_client.set(
            f"{_RELEASE_CACHE_KEY}:{repo}", json.dumps(payload, ensure_ascii=False), ex=ttl
        )


async def _latest_release() -> dict:
    """查 GitHub 最新 release；未配置仓库时直接返回 configured=False，不联网。"""
    repo = (settings.APP_UPDATE_REPO or "").strip()
    if not repo:
        return {
            "configured": False,
            "checked": False,
            "tag": None,
            "name": None,
            "url": None,
            "published_at": None,
            "checked_at": None,
            "error": None,
        }

    cached = await _cached_release(repo)
    if cached is not None:
        return cached

    payload = {
        "configured": True,
        "checked": True,
        "tag": None,
        "name": None,
        "url": None,
        "published_at": None,
        "checked_at": int(time.time()),
        "error": None,
    }
    with degrade_guard("version.latest_release", detail="检查最新 release 失败，保留当前版本信息"):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-cat-admin",
        }
        if settings.GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as client:
            response = await client.get(
                f"https://api.github.com/repos/{repo}/releases/latest",
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
            payload.update(
                {
                    "tag": str(body.get("tag_name") or "") or None,
                    "name": str(body.get("name") or "") or None,
                    "url": str(body.get("html_url") or "") or None,
                    "published_at": str(body.get("published_at") or "") or None,
                }
            )
    if payload["tag"] is None and payload["error"] is None:
        # degrade_guard 吞掉异常后这里仍是原始默认值；给用户一个可读原因
        payload["error"] = "检查更新失败（网络不可达或 GitHub 限流）"
    await _cache_release(repo, payload)
    logger.info(f"版本检查：{APP_VERSION} → {payload.get('tag') or payload.get('error')}")
    return payload


async def get_version_info(db: AsyncSession) -> dict:
    current = await health_service.read_db_revision(db)
    head = health_service.code_head_cached()
    pending = pending_revisions(current, head)
    latest = await _latest_release()
    update_available = is_newer(APP_VERSION, latest.get("tag"))
    return {
        "version": APP_VERSION,
        "app_env": settings.APP_ENV,
        "app_release": settings.APP_RELEASE or None,
        "migration": {
            "current": current,
            "head": head,
            "in_sync": (
                None
                if current is None or head is None
                else current == head
            ),
            "pending": len(pending) if pending is not None else None,
            "pending_ids": (pending or [])[:20],
        },
        "latest_release": latest,
        "update_available": update_available,
        "backup_hint": BACKUP_HINT,
    }
