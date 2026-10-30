"""备份管理（只读）：列目录、下载备份文件、生成宿主机恢复命令。

为什么后台不直接执行备份 / 恢复
--------------------------------
`scripts/backup_mysql.sh` 跑在**宿主机**：它需要 docker CLI、mysqldump、可写的
`./backups` 目录，以及交互式确认（恢复会覆盖同库同名表）。app 容器里这些都不具备，
而把 docker socket / 写权限塞进 Web 容器，等于为了一个按钮放弃整条安全边界。

因此这里是只读的：
- 列表 / 下载：给运维带走备份、核对校验文件；
- 恢复：只生成在宿主机执行的命令与警告，恢复动作仍由人确认执行（脚本本身也拒绝非交互恢复）。

下载备份是**全库数据外带**：每次下载写一条 `data_export` 审计（target_type=backup），
且要求 `setting:update`（只读角色不能下载）。列表仅展示元数据，要求 `setting:view`。
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AppException
from app.models.user import User
from app.services.admin_audit_service import record_admin_action

BACKUP_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.sql\.gz$")
MAX_LISTED = 200


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{size / 1024 / 1024 / 1024:.2f} GB"


def _kind(name: str) -> str:
    if name.startswith("pre_restore_"):
        return "pre_restore"
    if name.startswith("ai_cat_"):
        return "auto"
    return "other"


def _safe_name(name: str) -> str:
    """只接受单一文件名，拒绝目录分隔符 / 上跳 / 非备份后缀。"""
    if not BACKUP_NAME_RE.match(name or ""):
        raise AppException(code=400, message="备份文件名不合法")
    return name


def _backup_root() -> Path:
    return Path(settings.BACKUP_DIR)


def _resolve_backup(name: str) -> Path:
    root = _backup_root()
    target = (root / _safe_name(name)).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        raise AppException(code=400, message="备份文件名不合法")
    if not target.is_file():
        raise AppException(code=404, message="备份文件不存在")
    return target


def restore_command(name: str) -> str:
    """宿主机恢复命令。默认 compose 把 `./backups` 挂到容器 BACKUP_DIR。"""
    _safe_name(name)
    return f"./scripts/backup_mysql.sh --restore backups/{name}"


def backup_commands() -> dict:
    return {
        "backup": "./scripts/backup_mysql.sh",
        "install_cron": "./scripts/backup_mysql.sh --install-cron",
        "verify": "./scripts/backup_mysql.sh --verify",
        "restore_template": "./scripts/backup_mysql.sh --restore backups/<文件名>",
    }


async def list_backups(*, can_download: bool = False) -> dict:
    """列出备份文件（按修改时间倒序）；目录不可见时返回 visible=False，不报错。"""
    root = _backup_root()
    items: list[dict] = []
    if root.is_dir():
        entries = []
        for entry in root.iterdir():
            try:
                if not entry.is_file() or not BACKUP_NAME_RE.match(entry.name):
                    continue
                stat = entry.stat()
                entries.append((entry, stat))
            except OSError:
                continue
        entries.sort(key=lambda item: item[1].st_mtime, reverse=True)
        now = time.time()
        for entry, stat in entries[:MAX_LISTED]:
            age_hours = round((now - stat.st_mtime) / 3600, 1)
            items.append(
                {
                    "name": entry.name,
                    "size": stat.st_size,
                    "size_human": _human_size(stat.st_size),
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).replace(tzinfo=None).isoformat(),
                    "age_hours": age_hours,
                    "kind": _kind(entry.name),
                    "has_checksum": (root / f"{entry.name}.sha256").is_file(),
                    "restore_command": restore_command(entry.name),
                }
            )

    latest = items[0] if items else None
    stale = True if latest is None else latest["age_hours"] >= max(1, settings.BACKUP_STALE_HOURS)
    return {
        "dir": str(root),
        "visible": root.is_dir(),
        "latest": latest["name"] if latest else None,
        "stale": stale,
        "stale_hours": max(1, settings.BACKUP_STALE_HOURS),
        "count": len(items),
        "total_size": sum(item["size"] for item in items),
        "items": items,
        "commands": backup_commands(),
        "can_download": can_download,
    }


async def prepare_download(db: AsyncSession, admin: User, name: str) -> Path:
    """校验文件并写审计后返回路径；下载本身由路由用 FileResponse 发送。"""
    target = _resolve_backup(name)
    size = target.stat().st_size
    record_admin_action(
        db,
        admin,
        "data_export",
        target_type="backup",
        summary=f"下载数据库备份 {name}",
        detail={"backup": name, "size": size},
    )
    await db.commit()
    logger.bind(event="backup_download", admin_id=getattr(admin, "id", None), backup=name).warning(
        "管理员下载了数据库备份"
    )
    return target
