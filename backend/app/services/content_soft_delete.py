"""内容软删除（回收站）的共用语义：一条过滤条件 + 两个状态迁移函数。

为什么单独抽一个模块：教程与资源是两套 admin service，但「什么算删除」「恢复后是什么状态」
必须是**同一套规则**。各写一遍的后果是两边慢慢漂移（一个恢复成草稿、一个恢复成已发布），
而这种不一致不会报错，只会在某天运营发现「恢复出来的东西怎么直接上线了」。

## 语义（改之前先读）

删除（soft delete）：
    `deleted_at = 现在`，并且 **`is_published = False`**。
    第二步是关键 —— 公开端所有读取路径（列表 / 详情 / sitemap / 统计 / 收录推送）
    本来就在过滤 `is_published`，因此「删掉的内容立刻不可见」**不需要改任何公开查询**。
    少改一处查询，就少一个「删了还在」的静默漏洞。

恢复（restore）：
    默认恢复成**草稿**（`is_published` 保持 False），需要人再点一次「发布」。
    刻意不自动回到已发布：内容被删往往是有原因的（违规、过时、写错），
    自动重新上线等于让删除者的一次误操作反向生效两次。
    调用方传 `republish=True` 时才会同时置为已发布并补 `published_at`。

⚠️ 回收站里恢复的内容**不会**带回用户侧记录（收藏 / 学习记录 / 评论在删除时已按既有
   语义清理）。这一点必须诚实告知运营，否则「恢复」会被当成「完全撤销」。
"""

from __future__ import annotations

from datetime import datetime, timezone

# 回收站视图取值：active=正常内容（默认）/ deleted=回收站 / all=两者
DELETED_MODES: tuple[str, ...] = ("active", "deleted", "all")


def normalize_deleted_mode(mode: str | None) -> str:
    """把外部传入的视图参数收敛到合法取值。

    非法值一律按 ``active`` 处理（**绝不放宽**）：接口参数写错时宁可少显示，
    也不能把回收站里的内容混进日常列表 —— 那会让「已删除」看起来像没删掉。
    """
    if mode in DELETED_MODES:
        return mode
    return "active"


def deleted_condition(column, mode: str | None = "active"):
    """返回回收站视图对应的过滤条件；``all`` 返回 None（不加条件）。

    :param column: ``Tutorial.deleted_at`` / ``Resource.deleted_at``
    """
    normalized = normalize_deleted_mode(mode)
    if normalized == "deleted":
        return column.isnot(None)
    if normalized == "all":
        return None
    return column.is_(None)


def mark_deleted(obj, now: datetime | None = None) -> None:
    """把内容对象标记为「在回收站」：打时间戳 + 强制下架。

    只改内存对象，不提交 —— 调用方负责与审计行在同一事务里 commit。
    """
    obj.deleted_at = now or datetime.now(timezone.utc).replace(tzinfo=None)
    obj.is_published = False


def mark_restored(obj, *, republish: bool = False, now: datetime | None = None) -> None:
    """把内容对象移出回收站。

    :param republish: True = 同时恢复为已发布（并补发布时间，仅当原本没有发布时间）
    """
    obj.deleted_at = None
    if republish:
        obj.is_published = True
        if getattr(obj, "published_at", None) is None:
            obj.published_at = now or datetime.now(timezone.utc).replace(tzinfo=None)
