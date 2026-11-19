"""站内通知模板（2026-11-19）：常用通知文案的保存与套用。

## 为什么存 system_settings JSON 而不是建表

模板是**一整份清单**（最多 20 条），没有按行检索/启停/角色权限的需求；
整份 JSON 存一个运行期配置键（`message_templates`）与 IP 黑名单同一取舍 ——
零迁移、零新表，保存即时生效。上限 20 条防误操作把配置撑爆。

## 治理边界

- 套用/查看：`message:view`（能发通知的人自然要能看到模板）；
- 增删：`message:send`（模板内容最终会以管理员名义群发给用户，
  能定义模板 = 能预设「代表平台说的话」的口径，与发送同权）；
- 保存/删除写审计（`message_template_update`）。
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AppException
from app.models.system_setting import SystemSetting
from app.services.runtime_config import apply_runtime_settings, invalidate_runtime_settings_cache

TEMPLATES_SETTING_KEY = "message_templates"
MAX_TEMPLATES = 20
_NAME_MAX = 50
_TITLE_MAX = 100
_CONTENT_MAX = 2000


def parse_templates(raw: str | None) -> list[dict[str, str]]:
    """解析模板 JSON（非法/形态不对一律按空清单处理 —— 配置坏不阻断发通知）。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    templates: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()[:_NAME_MAX]
        title = str(item.get("title", "")).strip()[:_TITLE_MAX]
        content = str(item.get("content", "")).strip()[:_CONTENT_MAX]
        if name and title and content:
            templates.append({"name": name, "title": title, "content": content})
    return templates


def _serialize(templates: list[dict[str, str]]) -> str:
    return json.dumps(templates, ensure_ascii=False)


def list_templates() -> list[dict[str, str]]:
    """当前模板清单（读运行期配置）。"""
    return parse_templates(settings.MESSAGE_TEMPLATES)


async def save_with_audit(db: AsyncSession, admin, templates: list[dict[str, str]]) -> dict:
    """整份保存模板清单 + 审计 + 提交 + 热生效。

    :param templates: 已规整的清单（调用方校验）；超过上限抛 400。
    """
    from app.services.admin_audit_service import record_admin_action

    if len(templates) > MAX_TEMPLATES:
        raise AppException(code=400, message=f"模板数量超过上限（{MAX_TEMPLATES}）")
    for item in templates:
        if not (item.get("name") and item.get("title") and item.get("content")):
            raise AppException(code=400, message="模板的名称 / 标题 / 正文均不能为空")

    old = list_templates()
    new_value = _serialize(templates)
    row = (
        await db.execute(
            select(SystemSetting).where(SystemSetting.setting_key == TEMPLATES_SETTING_KEY)
        )
    ).scalar_one_or_none()

    changed = False
    if not templates:
        if row is not None:
            # AsyncSession.delete 是同步方法
            db.delete(row)
            changed = True
    elif row is None:
        db.add(SystemSetting(setting_key=TEMPLATES_SETTING_KEY, value=new_value))
        changed = True
    elif (row.value or "") != new_value:
        row.value = new_value
        changed = True

    if not changed:
        return {"changed": False, "templates": templates}

    old_names = [t["name"] for t in old]
    new_names = [t["name"] for t in templates]
    record_admin_action(
        db,
        admin,
        "message_template_update",
        target_type="setting",
        summary=f"更新站内通知模板：{len(templates)} 条",
        detail={
            "count": len(templates),
            "added": [n for n in new_names if n not in old_names],
            "removed": [n for n in old_names if n not in new_names],
        },
    )
    await db.commit()
    await invalidate_runtime_settings_cache()
    await apply_runtime_settings()
    return {"changed": True, "templates": templates}
