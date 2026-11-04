"""站内聊天模型：管理员之间的轻量内部沟通（私聊 / 群聊）

范围刻意收敛（与 FastapiAdmin 的 chat 模块同口径）：纯文字 + 实时推送 + 未读角标；
**不做**文件传输、撤回、已读回执、多端同步 —— 那是把 Slack 搬进后台，维护成本
远超管理员间沟通的真实需求。消息本身就是台账（谁在什么时候说了什么可查），
因此不走管理操作审计。

为什么消息主键用**自增** id 而不是全库惯例的随机 `generate_id`：
未读计算与历史分页都要「按序游标」（`id > last_read_seq` / `before_id`），
随机 10 位 id 无法比较大小；消息 id 只在聊天上下文内部流转（按会话 + 游标寻址），
无对外枚举面，单调性带来的正确性远比防枚举重要。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id

CHAT_CONVERSATION_TYPES = ("direct", "group")

# 消息内容长度上限（与前端输入框 maxLength 对齐）
CHAT_MESSAGE_MAX_LENGTH = 2000

# 群聊最少人数：包含发起人在内的 3 人（2 人请用单聊，免得出现"两人群"这种脏数据）
CHAT_GROUP_MIN_MEMBERS = 3


class ChatConversation(Base):
    """聊天会话：direct（两人单聊）或 group（群聊）"""

    __tablename__ = "chat_conversations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    type: Mapped[str] = mapped_column(String(10), comment="direct/group")
    # 群聊标题；单聊恒为空 —— 展示名取对方成员，存一份反而要同步
    title: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="群聊标题(单聊为空)")
    created_by: Mapped[int] = mapped_column(BigInteger, comment="发起人ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # 会话里最近一条消息的时间：会话列表按它排序，避免全表 join messages 取 max
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="最近消息时间"
    )


class ChatMessage(Base):
    """聊天消息（纯文字）。主键自增：排序与已读游标依赖单调性，见模块注释。"""

    __tablename__ = "chat_messages"
    # 会话内按时间读 + 未读比较都是 (conversation_id, id) 范围查询
    __table_args__ = (Index("ix_chat_messages_conv_id", "conversation_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, comment="所属会话ID")
    sender_id: Mapped[int] = mapped_column(BigInteger, comment="发送人ID")
    content: Mapped[str] = mapped_column(Text, comment="消息内容(纯文字)")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChatMember(Base):
    """会话成员与已读游标。last_read_id = 已读到的最大消息 id（0 = 没读过）。"""

    __tablename__ = "chat_members"
    __table_args__ = (Index("ix_chat_members_user_conv", "user_id", "conversation_id"),)

    conversation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_read_id: Mapped[int] = mapped_column(BigInteger, default=0, comment="已读游标(消息id)")
    joined_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
