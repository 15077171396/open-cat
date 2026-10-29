"""公告与通知设置模型"""

from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime, Text, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class Announcement(Base):
    """平台公告"""
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    title: Mapped[str] = mapped_column(String(200), comment="标题")
    content: Mapped[str] = mapped_column(Text, comment="正文")
    tag: Mapped[str] = mapped_column(String(20), default="公告", comment="标签：公告/活动/资讯")
    color: Mapped[str] = mapped_column(String(20), default="#3b82f6", comment="标签颜色")
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, index=True, comment="是否发布")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="发布时间")
    # 定时发布：NULL=立即（沿用 is_published）；>now 时即使 is_published=1 也不可见。
    # 刻意不引入「已排期」状态 —— 可见性在查询时判断，不依赖定时任务「记得跑」
    publish_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="定时发布时间(NULL=立即, >now=未到点不可见)"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class NotificationSetting(Base):
    """用户通知设置"""
    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, comment="用户ID")
    system: Mapped[bool] = mapped_column(Boolean, default=True, comment="系统通知")
    comment: Mapped[bool] = mapped_column(Boolean, default=True, comment="评论回复")
    like: Mapped[bool] = mapped_column(Boolean, default=True, comment="点赞")
    private: Mapped[bool] = mapped_column(Boolean, default=True, comment="私信")
    email: Mapped[bool] = mapped_column(Boolean, default=False, comment="邮件通知")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
