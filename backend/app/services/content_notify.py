"""内容生命周期通知：内容下架时提醒「收藏过它」的用户。

为什么只通知收藏、不通知下载/学习：
- 收藏是用户**主动保存的资产**，下架了得给个交代（否则用户点进去才发现 404）；
- 下载 / 学习是自动流水，直接在查询层过滤掉即可（见 `content_availability`），不需要打扰用户。

⚠️ 通知里**刻意不带 link**：目标已经下架，给个链接等于把用户送进 404 ——
那正是这条通知要避免的事。等以后有「重新上架」可以考虑再补一条带链接的通知。
"""

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import generate_id
from app.core.content_types import label_for
from app.models.favorite import Favorite
from app.models.message import Message


async def notify_favoriters_unpublished(
    db: AsyncSession, *, target_type: str, target_id: int, title: str
) -> int:
    """给收藏过该内容的用户各发一条站内消息，返回通知人数

    只 `add` 不 `commit`：与调用方（后台上下架操作）同一个事务，
    操作回滚则通知一起消失 —— 内容没真的下架就不该发出通知。
    """
    user_ids = (
        await db.execute(
            select(Favorite.user_id).where(
                Favorite.target_type == target_type,
                Favorite.target_id == target_id,
            )
        )
    ).scalars().all()
    receivers = {uid for uid in user_ids if uid}
    if not receivers:
        return 0

    # 中文名来自唯一注册表（core.content_types）：新增内容类型时不会再出现
    # 「通知里写成裸代码 tutorial」这种漂移
    label = label_for(target_type)
    safe_title = (title or "").strip() or "你收藏的内容"
    title_text = f"你收藏的{label}已下架"
    content_text = (
        f"《{safe_title}》已被下架，暂时无法访问。\n\n"
        "这条记录仍保留在你的收藏里，如果不需要了可以在收藏页取消收藏。"
    )
    # 一条批量 INSERT 而不是 N 个 ORM 对象：批量下架热门内容时 receivers 可能上万，
    # 逐条 add 会同时持有上万个待 flush 的实例（内存与 flush 时间都随收藏数线性增长）。
    # 仍然是「只写不 commit」—— 与调用方同事务，操作回滚则通知一起消失。
    rows = [
        {
            "id": generate_id(),
            "user_id": user_id,
            "type": "system",
            "title": title_text,
            "content": content_text,
            "avatar": None,
            "link": None,
            "is_read": False,
        }
        for user_id in receivers
    ]
    await db.execute(insert(Message), rows)
    return len(receivers)
