"""IP 黑名单服务：读取 / 校验 / 保存（+ 审计 + 提交 + 即时生效）。

黑名单存 `ip_blacklist` 运行期配置（system_settings 表，每行一个 IP/CIDR），
保存链路与敏感词库同构：

    save_with_audit（改行 + 审计，不 commit）
    → 本函数内 commit
    → apply_after_save（失效缓存 + apply_runtime_settings）
    → 中间件按配置值指纹自动重载解析结果

为什么复用运行期配置而不是独立表：黑名单是**一整份策略**（不是逐条记录），
没有按行检索/启停的需求；整份覆盖写 + 文本形态与 env 初始值天然兼容。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AppException
from app.core.ip_guard import parse_blacklist
from app.models.system_setting import SystemSetting
from app.services.runtime_config import apply_runtime_settings, invalidate_runtime_settings_cache

BLACKLIST_SETTING_KEY = "ip_blacklist"
# 上限：每行按 60 字节算，1000 条 ≈ 60KB，对「一份策略文本」是合理天花板
MAX_ENTRIES = 1000


def list_entries() -> dict:
    """黑名单清单（有效条目 + 无效行提示）。"""
    valid, invalid = parse_blacklist(settings.IP_BLACKLIST)
    return {
        "entries": valid,
        "invalid_entries": invalid,
        "max_entries": MAX_ENTRIES,
    }


async def save_with_audit(db: AsyncSession, admin, raw_text: str) -> dict:
    """保存黑名单（**整份覆盖**）+ 审计 + 提交 + 热生效。

    拉黑/放行直接影响线上访问面，added/removed 差异进审计 detail。
    """
    from app.services.admin_audit_service import record_admin_action

    entries, invalid = parse_blacklist(raw_text)
    if len(entries) > MAX_ENTRIES:
        raise AppException(code=400, message=f"黑名单条目超过上限（{MAX_ENTRIES}）")

    old_entries, _ = parse_blacklist(settings.IP_BLACKLIST)
    old_set, new_set = set(old_entries), set(entries)
    added = sorted(new_set - old_set)
    removed = sorted(old_set - new_set)
    new_value = "\n".join(entries)

    row = (
        await db.execute(
            select(SystemSetting).where(SystemSetting.setting_key == BLACKLIST_SETTING_KEY)
        )
    ).scalar_one_or_none()

    changed = False
    if not entries:
        if row is not None:
            # AsyncSession.delete 是同步方法
            db.delete(row)
            changed = True
    elif row is None:
        db.add(SystemSetting(setting_key=BLACKLIST_SETTING_KEY, value=new_value))
        changed = True
    elif (row.value or "") != new_value:
        row.value = new_value
        changed = True

    if not changed:
        return {"changed": False, "entries": entries, "invalid_entries": invalid,
                "added": [], "removed": []}

    record_admin_action(
        db,
        admin,
        "ip_blacklist_update",
        target_type="setting",
        summary=f"更新 IP 黑名单：{len(entries)} 条",
        detail={
            "count": len(entries),
            "added": added[:50],
            "removed": removed[:50],
        },
    )
    await db.commit()
    # 提交后失效缓存并重新应用（中间件按指纹自动重载解析结果）
    await invalidate_runtime_settings_cache()
    await apply_runtime_settings()
    return {"changed": True, "entries": entries, "invalid_entries": invalid,
            "added": added, "removed": removed}
