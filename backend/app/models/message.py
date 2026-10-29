"""消息模型"""

from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime, Text, Boolean, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class Message(Base):
    """用户消息"""
    __tablename__ = "messages"
    # 未读数统计/列表高频按 user_id + is_read 过滤，复合索引避免回表+全扫
    __table_args__ = (
        Index("ix_messages_user_read", "user_id", "is_read"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="接收用户ID")
    sender_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="发送者ID（系统消息为空）")
    # 群发批次号：一次「发全站」会给每个用户插一行，管理端要能按批次查看/统计发了多少人。
    # 单条通知（评论回复、内容下架）为 NULL —— 它们本来就不成批。
    batch_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True, comment="群发批次号（单条通知为空）"
    )
    type: Mapped[str] = mapped_column(String(20), index=True, comment="system/comment/like/private")
    title: Mapped[str] = mapped_column(String(200), comment="标题")
    content: Mapped[str] = mapped_column(Text, comment="正文")
    avatar: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="发送者头像URL")
    link: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="关联页面路径")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True, comment="是否已读")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="发送时间")
