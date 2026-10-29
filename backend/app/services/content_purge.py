"""内容硬删除时，统一清理「指向它」的用户侧记录（第五轮审查 EXT-07）。

## 背景

`favorites` / `content_comments` / `learning_records` 存的都是**快照 + 裸 ID**，
没有外键（内容散在 5 张表，单列外键表达不了多态目标）。因此内容硬删除后不会级联，
残留行会变成：

- 收藏 → 「我的收藏」里点开即 404 的僵尸条目，用户无法自行清理；
- 评论 → 没有任何展示入口的孤儿 UGC（还占着举报/审核的处理路径）；
- 学习记录 → 「继续学习」里永远打不开的卡片。

所以删除流程必须**同事务**清理。但收敛前这件事是靠 16 处手工调用铺出来的：

```python
await purge_learning_records_for_tutorials(db, [tutorial_id])
await purge_favorites_for_targets(db, "tutorial", [tutorial_id])
await purge_comments_for_targets(db, COMMENT_TARGET_TUTORIAL, [tutorial_id])
```

单条删除和批量删除各写一遍、4 个 admin service 各写一遍。漏一处的后果是**静默**的
（数据在库里烂着，只有用户点到才发现），而且新增内容类型时根本不知道该加几行。

## 现在的做法

调用方只说「我要删哪种内容的哪些 ID」，清哪些表由注册表的能力标记决定：

```python
await purge_content_records(db, "tutorial", ids)
```

`commentable` → 清评论；`favoritable` → 清收藏；`learnable` → 清学习记录。
新增内容类型时，能力标记一改，清理自动跟上 —— 不存在「忘了加一行」。

⚠️ 仍然**只写不提交**：必须与内容删除在同一个事务里。分开提交会出现
「内容已删、记录还在」的中间态，进程崩溃即留下孤儿数据。
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.content_types import CONTENT_TYPES


async def purge_content_records(
    db: AsyncSession, module: str, ids: list[int]
) -> dict[str, int]:
    """清理指向 ``(module, ids)`` 的用户侧记录，返回 `{类别: 清理条数}`。

    参数：
        module: 内容类型（`core.content_types` 的键）。未知类型抛 KeyError ——
            这是程序错误（调用点写错类型名），必须尽早暴露，不能静默跳过清理。
        ids: 待删除内容的 ID 列表。空列表直接返回（批量删除传空是正常情况）。

    返回：只包含「该类型确实有这类记录」的键，便于测试断言与日志核对。
    """
    spec = CONTENT_TYPES[module]
    clean_ids = [i for i in (ids or []) if i is not None]
    if not clean_ids:
        return {}

    removed: dict[str, int] = {}

    # 延迟导入：comment/favorite/learning 三个 service 都依赖 models，
    # 而本模块会被 4 个 admin service 导入 —— 模块级导入会把
    # 「admin service → 三个业务 service → models」整条链在启动期拉满，
    # 也更容易在后续改动中形成环。
    if spec.commentable:
        from app.services.comment_service import purge_comments_for_targets

        removed["comments"] = await purge_comments_for_targets(db, module, clean_ids)

    if spec.favoritable:
        from app.services.favorite_service import purge_favorites_for_targets

        removed["favorites"] = await purge_favorites_for_targets(db, module, clean_ids)

    if spec.learnable:
        from app.services.learning_service import purge_learning_records_for_tutorials

        removed["learning"] = await purge_learning_records_for_tutorials(db, clean_ids)

    logger.bind(
        event="content_purge",
        module=module,
        requested=len(clean_ids),
        **removed,
    ).debug(f"内容删除连带清理 {module}：{removed}")
    return removed
