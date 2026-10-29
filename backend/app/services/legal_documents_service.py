"""法律文档：全站公示的隐私政策 / 用户协议 / 免责声明（2026-10-27 从系统设置抽出）。

抽出的理由与「登录管理」同源，但侧重不同：这批内容不是「配置项」，是**对外承诺的文本**。
放在几十个系统设置 tab 里，有两个具体问题：

1. **权限面**：改法务文本通常只有负责人能做，而系统设置的写权限同时覆盖 COS 密钥、
   支付商户私钥。抽出来后 `legal_doc:update` 可以单独授予；
2. **信息面**：原表单只能看到「有一段 HTML」，看不到「这份是自定义的还是系统默认的」、
   「多长」、「什么时候改的」。改法务文本最需要这几个信息来确认改的是哪一份、改没改。

内容存储不变（仍是 `system_settings` 的三个 `legal_*` 键），前台读取路径不变
（`GET /api/v1/settings/legal/{type}` + `legal_doc:*` Redis 缓存），本模块只负责后台读写。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_setting import SystemSetting
from app.services.setting_definitions import SETTING_DEFINITIONS, SURFACE_LEGAL
from app.services.setting_service import (
    LEGAL_DOC_TYPES,
    definitions_payload,
    get_stored_settings,
    groups_payload,
    keys_for_surface,
    update_settings,
)

#: 本模块负责的设置键（= legal 分组的三个 richtext 项）
LEGAL_SETTING_KEYS: frozenset[str] = frozenset(keys_for_surface(SURFACE_LEGAL))

_DEFS = {d.key: d for d in SETTING_DEFINITIONS}

# 统计「正文字数」用：去掉标签、HTML 实体与空白，避免把排版标记算进字数
_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_RE = re.compile(r"&[a-zA-Z#0-9]+;")
_WS_RE = re.compile(r"\s+")

#: 前台展示位置的说明（文案固定，便于运营确认改的是页脚哪一份）
_PUBLIC_HINTS: dict[str, str] = {
    "privacy": "前台页脚 → 隐私政策",
    "agreement": "前台页脚 → 用户协议",
    "disclaimer": "前台页脚 → 免责声明",
}


def definitions() -> list[dict]:
    return definitions_payload(surface=SURFACE_LEGAL)


def groups() -> list[dict]:
    return groups_payload(surface=SURFACE_LEGAL)


async def stored_values(db: AsyncSession) -> dict[str, str]:
    """已显式配置的值（法务文本是富文本，无密钥，原样返回）"""
    return await get_stored_settings(db, surface=SURFACE_LEGAL)


async def update(db: AsyncSession, items: dict) -> int:
    """保存法务文本；越界键（例如 cos_secret_key）直接 ValueError"""
    return await update_settings(db, items, surface=SURFACE_LEGAL)


def _plain_length(html: str) -> int:
    text = _TAG_RE.sub("", html or "")
    text = _ENTITY_RE.sub("", text)
    return len(_WS_RE.sub("", text))


async def documents(db: AsyncSession) -> list[dict[str, Any]]:
    """三份文档的元信息（左侧导航用）：是否自定义、字数、最后保存时间、前台位置

    ⚠️ 一次查询取三行：这三份都是几十 KB 的富文本，正文由 `values` 单独下发，
    这里只算统计量，避免把三份全文重复塞进响应。
    """
    rows = (
        await db.execute(
            select(SystemSetting).where(SystemSetting.setting_key.in_(LEGAL_SETTING_KEYS))
        )
    ).scalars().all()
    by_key = {row.setting_key: row for row in rows}

    result: list[dict[str, Any]] = []
    for doc_type, meta in LEGAL_DOC_TYPES.items():
        key = meta["key"]
        defn = _DEFS[key]
        row = by_key.get(key)
        custom = bool(row and row.value)
        content = row.value if custom else defn.default
        updated_at = getattr(row, "updated_at", None) if custom else None
        result.append(
            {
                "key": key,
                "type": doc_type,
                "title": meta["title"],
                # 自定义 = 后台存过值；否则前台展示的是代码里的默认文案
                "is_custom": custom,
                "length": _plain_length(content),
                "updated_at": updated_at.isoformat() if updated_at else None,
                "public_hint": _PUBLIC_HINTS.get(doc_type, ""),
                "public_api": f"/api/v1/settings/legal/{doc_type}",
            }
        )
    return result


async def overview(db: AsyncSession) -> dict[str, Any]:
    """法律文档页的完整载荷：定义 + 分组 + 值 + 文档元信息"""
    return {
        "definitions": definitions(),
        "groups": groups(),
        "values": await stored_values(db),
        "documents": await documents(db),
    }
