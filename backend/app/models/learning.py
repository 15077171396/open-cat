"""学习记录模型"""

from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, DateTime, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class LearningRecord(Base):
    """用户学习记录"""
    __tablename__ = "learning_records"
    __table_args__ = (
        UniqueConstraint("user_id", "tutorial_id", name="uq_user_tutorial"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="用户ID")
    tutorial_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="教程ID")
    title: Mapped[str] = mapped_column(String(200), comment="教程标题快照")
    cover: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="教程封面快照")
    current_chapter: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="当前章节名")
    progress: Mapped[int] = mapped_column(Integer, default=0, comment="学习进度 0-100")
    status: Mapped[str] = mapped_column(String(20), default="in_progress", index=True, comment="in_progress/completed")
    last_study_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), comment="上次学习时间")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
