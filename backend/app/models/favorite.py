"""收藏模型"""

from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class Favorite(Base):
    """用户收藏记录"""
    __tablename__ = "favorites"
    __table_args__ = (
        # 同一用户对同一目标只能收藏一次
        UniqueConstraint("user_id", "target_type", "target_id", name="uq_user_target"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="用户ID")
    target_type: Mapped[str] = mapped_column(String(20), index=True, comment="tutorial/resource/plugin/node/workflow")
    target_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="目标ID")
    # 冗余字段：收藏时目标标题/描述/封面快照，避免列表页 N+1 查询
    title: Mapped[str] = mapped_column(String(200), comment="目标标题快照")
    description: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="目标描述快照")
    cover: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="目标封面快照")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="收藏时间")
