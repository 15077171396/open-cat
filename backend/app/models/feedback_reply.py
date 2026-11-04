"""反馈回复模型：把「反馈」从单向台账升级为可往返的工单闭环

与 `Feedback.admin_note`（内部备注）的职责分离是刻意的：
- `admin_note`：只有管理员可见的处理笔记（用户永远看不到）；
- `FeedbackReply`：进入对话线程、**用户可见**的回复（sender 区分谁说的）。
把两者混在一个字段里，要么用户看到内部备注、要么备注被迫公开 —— 两个方向都是事故。
"""

from datetime import datetime
from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id

# 发送方：admin=管理员回复（用户可见）；user=用户补充说明
REPLY_SENDERS = ("admin", "user")


class FeedbackReply(Base):
    """反馈回复（工单对话线程中的一条发言）"""

    __tablename__ = "feedback_replies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    feedback_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="所属反馈ID")
    sender: Mapped[str] = mapped_column(String(10), comment="发送方: admin/user")
    content: Mapped[str] = mapped_column(Text, comment="回复内容")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
