"""菜单管理：代码菜单目录 + DB 展示层覆盖。

设计边界：
- **菜单、路由、权限码仍由代码同源定义**（`admin_menu.py` / `rbac_catalog.py`），
  本模块只允许改「中文名 / 英文名 / 图标 / 是否可见 / 排序」。
- 不提供新建、删除菜单：否则会出现「侧栏有入口、点进去 404（无路由）」或
  「有入口但 403（权限码未登记）」的死菜单，正是路由表守卫要防的事。
- 覆盖存 `system_settings.menu_overrides`（JSON），不新增表；未覆盖 = 用代码默认值。
- 每次写操作写审计；生效范围是**下一次 GET /admin/menus**，前端收到事件后重拉菜单。
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import module_registry
from app.core.exceptions import AppException
from app.core.i18n import DEFAULT_LOCALE, pick
from app.models.system_setting import SystemSetting
from app.models.user import User
from app.services import admin_menu
from app.services.admin_audit_service import record_admin_action

MENU_OVERRIDES_KEY = "menu_overrides"
_MAX_LABEL = 64
_MAX_ICON = 50
_SORT_MIN = -9_999
_SORT_MAX = 9_999
_FIELDS = ("label_zh", "label_en", "icon", "visible", "sort_order")


def _parse_overrides(raw: str | None) -> dict[str, dict[str, Any]]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    known = admin_menu.all_menu_keys()
    result: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        if key not in known or not isinstance(value, dict):
            continue
        cleaned = {field: value[field] for field in _FIELDS if field in value and value[field] is not None}
        if cleaned:
            result[key] = cleaned
    return result


async def _load_raw(db: AsyncSession) -> dict[str, dict[str, Any]]:
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.setting_key == MENU_OVERRIDES_KEY)
    )
    row = result.scalar_one_or_none()
    return _parse_overrides(row.value if row else None)


async def load_overrides(db: AsyncSession) -> dict[str, admin_menu.MenuOverride]:
    raw = await _load_raw(db)
    overrides: dict[str, admin_menu.MenuOverride] = {}
    for key, value in raw.items():
        overrides[key] = admin_menu.MenuOverride(
            label_zh=value.get("label_zh"),
            label_en=value.get("label_en"),
            icon=value.get("icon"),
            visible=value.get("visible"),
            sort_order=value.get("sort_order"),
        )
    return overrides


async def _save_raw(db: AsyncSession, raw: dict[str, dict[str, Any]]) -> None:
    value = json.dumps(raw, ensure_ascii=False, separators=(",", ":")) if raw else "{}"
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.setting_key == MENU_OVERRIDES_KEY)
    )
    row = result.scalar_one_or_none()
    if row is None:
        db.add(SystemSetting(setting_key=MENU_OVERRIDES_KEY, value=value))
    else:
        row.value = value


def _default_label_en(key: str, default_zh: str) -> str:
    return admin_menu.MENU_LABELS_EN.get(key) or default_zh


def _effective(key: str, default_zh: str, default_icon: str, override, locale: str, index: int) -> dict:
    label_zh = override.label_zh if override and override.label_zh else default_zh
    label_en = override.label_en if override and override.label_en else _default_label_en(key, default_zh)
    return {
        "key": key,
        "label": pick(locale, label_zh, label_en),
        "label_zh": label_zh,
        "label_en": label_en,
        "default_label_zh": default_zh,
        "default_label_en": _default_label_en(key, default_zh),
        "icon": override.icon if override and override.icon else default_icon,
        "default_icon": default_icon,
        "visible": override.visible if override and override.visible is not None else True,
        "sort_order": override.sort_order if override and override.sort_order is not None else index,
        "is_custom": bool(override),
    }


def _sort_key(entry: dict) -> tuple[int, str]:
    return (int(entry["sort_order"]), str(entry["key"]))


async def settings_tree(db: AsyncSession, locale: str = DEFAULT_LOCALE) -> dict:
    """菜单管理页的数据：代码目录 + 生效值 + 默认值（未做模块过滤，便于管理全部入口）。"""
    overrides = await load_overrides(db)

    groups: list[dict] = []
    for group_index, group in enumerate(admin_menu.MENU_GROUPS):
        group_override = overrides.get(group.key)
        item_rows: list[dict] = []
        for item_index, item in enumerate(group.items):
            item_override = overrides.get(item.key)
            entry = _effective(item.key, item.label, item.icon, item_override, locale, item_index)
            entry.update(
                {
                    "kind": "item",
                    "route": item.key,
                    "perm": item.perm,
                    "perms": list(item.perms),
                    "url": item.url,
                    "module_enabled": module_registry.menu_visible(item.key),
                }
            )
            item_rows.append(entry)
        item_rows.sort(key=_sort_key)

        group_entry = _effective(
            group.key, group.label, group.icon, group_override, locale, group_index
        )
        group_entry.update(
            {
                "kind": "group",
                "route": None,
                "perm": None,
                "perms": [],
                "module_enabled": any(item["module_enabled"] for item in item_rows),
                "items": item_rows,
            }
        )
        groups.append(group_entry)
    groups.sort(key=_sort_key)

    footer: list[dict] = []
    for item_index, item in enumerate(admin_menu.MENU_FOOTER):
        item_override = overrides.get(item.key)
        entry = _effective(item.key, item.label, item.icon, item_override, locale, item_index)
        entry.update(
            {
                "kind": "footer",
                "route": item.key,
                "perm": item.perm,
                "perms": list(item.perms),
                "url": item.url,
                "module_enabled": module_registry.menu_visible(item.key),
            }
        )
        footer.append(entry)
    footer.sort(key=_sort_key)

    return {"groups": groups, "footer": footer, "custom_count": len(overrides)}


def _require_key(key: str) -> None:
    if not admin_menu.menu_key_exists(key):
        raise AppException(code=404, message="菜单不存在")


def _normalize_text(field: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AppException(code=400, message=f"{field} 必须是字符串")
    text = value.strip()
    if not text:
        return None
    max_length = _MAX_ICON if field == "icon" else _MAX_LABEL
    if len(text) > max_length:
        raise AppException(code=400, message=f"{field} 最长 {max_length} 个字符")
    return text


def _normalize_update(changes: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for field in ("label_zh", "label_en", "icon"):
        if field in changes:
            normalized[field] = _normalize_text(field, changes[field])
    if "visible" in changes:
        value = changes["visible"]
        normalized["visible"] = None if value is None else bool(value)
    if "sort_order" in changes:
        value = changes["sort_order"]
        if value is None:
            normalized["sort_order"] = None
        else:
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise AppException(code=400, message="排序必须是整数") from exc
            if not _SORT_MIN <= number <= _SORT_MAX:
                raise AppException(code=400, message=f"排序需在 {_SORT_MIN} ~ {_SORT_MAX} 之间")
            normalized["sort_order"] = number
    return normalized


def _diff(old: dict[str, Any], new: dict[str, Any], fields: Iterable[str]) -> dict[str, list]:
    changes = {}
    for field in fields:
        before = old.get(field)
        after = new.get(field)
        if before != after:
            changes[field] = [before, after]
    return changes


async def update_override(
    db: AsyncSession,
    admin: User,
    key: str,
    changes: dict[str, Any],
) -> dict:
    _require_key(key)
    normalized = _normalize_update(changes)
    if not normalized:
        raise AppException(code=400, message="没有需要修改的字段")

    raw = await _load_raw(db)
    old_entry = dict(raw.get(key, {}))
    new_entry = dict(old_entry)
    for field, value in normalized.items():
        if value is None:
            new_entry.pop(field, None)
        else:
            new_entry[field] = value
    if new_entry:
        raw[key] = new_entry
    else:
        raw.pop(key, None)
    await _save_raw(db, raw)

    record_admin_action(
        db,
        admin,
        "menu_setting_update",
        target_type="menu_setting",
        summary=f"修改菜单展示设置「{key}」",
        detail={"changes": _diff(old_entry, new_entry, normalized.keys())},
    )
    await db.commit()
    return {"key": key, "override": new_entry}


async def reorder(
    db: AsyncSession,
    admin: User,
    items: Iterable[dict[str, Any]],
) -> dict:
    raw = await _load_raw(db)
    changes: dict[str, list] = {}
    for item in items:
        key = str(item.get("key") or "")
        _require_key(key)
        try:
            order = int(item.get("sort_order"))
        except (TypeError, ValueError) as exc:
            raise AppException(code=400, message="排序必须是整数") from exc
        if not _SORT_MIN <= order <= _SORT_MAX:
            raise AppException(code=400, message=f"排序需在 {_SORT_MIN} ~ {_SORT_MAX} 之间")
        entry = dict(raw.get(key, {}))
        before = entry.get("sort_order")
        if before != order:
            entry["sort_order"] = order
            raw[key] = entry
            changes[key] = [before, order]

    if not changes:
        return {"updated": 0}
    await _save_raw(db, raw)
    record_admin_action(
        db,
        admin,
        "menu_setting_update",
        target_type="menu_setting",
        summary=f"调整菜单排序（{len(changes)} 项）",
        detail={"changes": changes},
    )
    await db.commit()
    return {"updated": len(changes)}


async def reset_one(db: AsyncSession, admin: User, key: str) -> None:
    _require_key(key)
    raw = await _load_raw(db)
    old_entry = raw.pop(key, None)
    if old_entry is None:
        return
    await _save_raw(db, raw)
    record_admin_action(
        db,
        admin,
        "menu_setting_reset",
        target_type="menu_setting",
        summary=f"恢复菜单「{key}」的默认展示",
        detail={"removed": old_entry},
    )
    await db.commit()


async def reset_all(db: AsyncSession, admin: User) -> int:
    raw = await _load_raw(db)
    if not raw:
        return 0
    await _save_raw(db, {})
    record_admin_action(
        db,
        admin,
        "menu_setting_reset",
        target_type="menu_setting",
        summary=f"恢复全部菜单默认展示（{len(raw)} 项）",
        detail={"keys": sorted(raw.keys())},
    )
    await db.commit()
    return len(raw)
