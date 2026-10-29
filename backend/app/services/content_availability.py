"""内容可用性判断：用户侧记录（收藏 / 下载 / 学习）指向的目标是否仍可访问。

背景：`favorites` / `downloads` / `learning_records` 存的都是**操作当时的快照**
（标题、封面、描述），目标被删除或**取消发布**后快照仍在。
删除路径有 `purge_*`（台账 P1-7）做同事务清理，但**取消发布不会删记录** ——
如果列表照旧展示，用户点进去只会拿到「内容不存在」，体验非常差。

处理策略（产品口径，2026-10-20 调整过，以本段为准）：
- **收藏** ＝ 用户主动保存的资产 → 不静默消失，列表里标「已下架」并允许取消收藏；
- **学习记录** ＝ 用户自己的学习轨迹 → **同样保留**，标「已下架」并禁用跳转按钮。
  （最初按「自动流水」处理，在查询层直接过滤；实测一个用户 11 条记录里 10 条指向
  已下架教程，一次过滤让历史几乎全空 —— 比看到「已下架」更让人困惑，故改为保留。）
- **下载记录** ＝ 纯操作流水 → 仍在查询层过滤（少一条用户无感，且没有「复习」价值）。

调用方注意：本模块只**判断**可用性，不负责清理数据，也不负责过滤 ——
要不要隐藏由各 service 自己决定（学习记录用 `available` 标记，下载用 join 过滤）。

这里只判断「是否还能公开访问」，不负责清理数据。
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.content_types import CONTENT_TYPES, availability_model_for

# 收藏 target_type / 记录类型 → 模型。派生自 `core.content_types`（唯一注册表，EXT-02），
# 不再手抄第二份。未知类型一律视为「可用」（宁可多显示，也不误杀）。
# 预留类型 workflow 也有一项：它借用 Tool 表判断，这是收敛前的既有行为（见注册表注释）。
_TYPE_MODELS = {
    module: availability_model_for(module)
    for module in CONTENT_TYPES
    if availability_model_for(module) is not None
}


async def unavailable_targets(
    db: AsyncSession, pairs: Iterable[tuple[str, int]]
) -> set[tuple[str, int]]:
    """返回其中「已删除或已取消发布」的 (target_type, target_id) 集合

    每个类型一次 `IN` 查询（页大小最多几十条，不会成为瓶颈），
    未登记的 target_type 直接跳过 —— 不知道该表用什么字段判断发布态时，
    宁可当作可用，也不要凭空隐藏用户资产。
    """
    grouped: dict[str, set[int]] = {}
    for target_type, target_id in pairs:
        if target_type in _TYPE_MODELS and target_id is not None:
            grouped.setdefault(target_type, set()).add(target_id)

    missing: set[tuple[str, int]] = set()
    for target_type, ids in grouped.items():
        model = _TYPE_MODELS[target_type]
        alive = (
            await db.execute(
                select(model.id).where(model.id.in_(ids), model.is_published.is_(True))
            )
        ).scalars().all()
        alive_ids = set(alive)
        missing |= {(target_type, i) for i in ids if i not in alive_ids}
    return missing
