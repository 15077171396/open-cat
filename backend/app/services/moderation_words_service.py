"""敏感词库服务：读词表 / 保存（落 system_settings + 热重载）/ 命中测试。

词库 = 内置兜底词（DEFAULT_WORDS）+ 自定义词。自定义词存 `moderation_extra_words`
运行期配置（system_settings 表，明文非密钥），保存链路：

    save_words（改行，不 commit，与审计同事务）
    → 调用方 commit
    → apply_after_save（失效缓存 + apply_runtime_settings）
    → runtime_config._after_apply 自动 reload_words

本 worker 立即生效；其他 gunicorn worker 经 60s 轮询同步。
词数上限 500、单词 40 字 —— 词库是「每条 UGC 都要过一遍」的热路径，
上限防止误操作把匹配正则撑爆。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AppException
from app.models.system_setting import SystemSetting
from app.services.moderation_service import DEFAULT_WORDS, scan

WORDS_SETTING_KEY = "moderation_extra_words"
MAX_WORDS = 500
WORD_MAX_LEN = 40


def parse_words(raw: str | None) -> list[str]:
    """把逗号/换行分隔的原始配置解析成词表（去空白、去重、保序）"""
    if not raw:
        return []
    seen: set[str] = set()
    words: list[str] = []
    for part in raw.replace("\r", "\n").replace("\n", ",").split(","):
        word = part.strip()
        if word and word.lower() not in seen:
            seen.add(word.lower())
            words.append(word)
    return words


def list_words() -> dict:
    """词库清单（内置兜底词 + 自定义词分组返回）"""
    return {
        "builtin_words": list(DEFAULT_WORDS),
        "extra_words": parse_words(settings.MODERATION_EXTRA_WORDS),
        "max_words": MAX_WORDS,
        "word_max_len": WORD_MAX_LEN,
    }


def normalize_words(raw_words: list[str]) -> list[str]:
    """规整词表：去空白、截断、按小写去重；超上限抛 400"""
    words: list[str] = []
    seen: set[str] = set()
    for raw in raw_words or []:
        word = (raw or "").strip()[:WORD_MAX_LEN]
        if word and word.lower() not in seen:
            seen.add(word.lower())
            words.append(word)
    if len(words) > MAX_WORDS:
        raise AppException(code=400, message=f"自定义词数量超过上限（{MAX_WORDS}）")
    return words


async def save_words(db: AsyncSession, raw_words: list[str]) -> dict:
    """保存自定义词（**全量覆盖**；只改行不 commit —— 调用方与审计同事务提交）。

    返回 {changed, words, added, removed}：added/removed 给审计 detail，
    删一个词等于放行一类内容，差异必须可追溯。
    """
    words = normalize_words(raw_words)
    new_value = "\n".join(words)
    row = (
        await db.execute(
            select(SystemSetting).where(SystemSetting.setting_key == WORDS_SETTING_KEY)
        )
    ).scalar_one_or_none()
    # diff 基于**持久事实**（库里的行）：settings 是运行期投影，多 worker 下
    # 最多滞后 60s，用它对账会出现「明明删了词却没记 removed」的假差异
    old_words = (
        parse_words(row.value) if row is not None else parse_words(settings.MODERATION_EXTRA_WORDS)
    )

    changed = False
    if not words:
        if row is not None:
            # AsyncSession.delete 是同步方法（错误地 await 会在运行时 TypeError）
            db.delete(row)
            changed = True
    elif row is None:
        db.add(SystemSetting(setting_key=WORDS_SETTING_KEY, value=new_value))
        changed = True
    elif (row.value or "") != new_value:
        row.value = new_value
        changed = True

    return {
        "changed": changed,
        "words": words,
        "added": [w for w in words if w not in old_words],
        "removed": [w for w in old_words if w not in words],
    }


async def save_words_with_audit(db: AsyncSession, admin, raw_words: list[str]) -> dict:
    """保存 + 审计 + 提交 + 热生效（事务边界在 service 层，API 层零手工 commit）。

    词库内容直接决定 UGC 拦截边界 —— 删一个词等于放行一类内容，
    added/removed 差异进审计 detail。
    """
    from app.services.admin_audit_service import record_admin_action

    result = await save_words(db, raw_words)
    if not result["changed"]:
        return {**result, "committed": False}
    record_admin_action(
        db,
        admin,
        "moderation_words_update",
        target_type="setting",
        summary=f"维护敏感词库：自定义词 {len(result['words'])} 个",
        detail={
            "count": len(result["words"]),
            "added": result["added"][:50],
            "removed": result["removed"][:50],
        },
    )
    await db.commit()
    # 提交后立即生效：失效缓存 + 重新应用（reload_words 由 runtime_config 自动触发）
    await apply_after_save()
    return {**result, "committed": True}


async def apply_after_save() -> None:
    """保存提交后调用：失效缓存 + 重新应用（reload_words 由 _after_apply 自动触发）"""
    from app.services.runtime_config import apply_runtime_settings, invalidate_runtime_settings_cache

    await invalidate_runtime_settings_cache()
    await apply_runtime_settings()


def test_text(text: str) -> list[str]:
    """命中测试：对任意文本跑一次当前生效词库，返回命中词（去重保序）"""
    return scan(text)
