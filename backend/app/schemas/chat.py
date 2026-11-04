"""站内聊天接口模型：请求 / 响应载荷。"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

_PASSTHROUGH = ConfigDict(extra="allow")


class ChatGroupCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100, description="群聊标题")
    member_ids: list[int] = Field(..., min_length=1, max_length=50, description="成员ID（不含发起人）")


class ChatMessageItem(BaseModel):
    model_config = _PASSTHROUGH

    id: int
    conversation_id: int
    sender_id: int
    content: str
    created_at: Optional[str] = None


class ChatMessageCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000, description="消息内容（纯文字）")


class ChatMessagesData(BaseModel):
    model_config = _PASSTHROUGH

    items: list[ChatMessageItem] = Field(default_factory=list)
    has_more: bool = False


class ChatConversationCreated(BaseModel):
    model_config = _PASSTHROUGH

    id: int
    created: bool


class ChatReadAck(BaseModel):
    model_config = _PASSTHROUGH

    conversation_id: int
    last_read_id: int


class ChatUnreadData(BaseModel):
    model_config = _PASSTHROUGH

    total: int


class ChatMemberBrief(BaseModel):
    model_config = _PASSTHROUGH

    id: int
    account: Optional[str] = None
    nickname: Optional[str] = None


class ChatConversationItem(BaseModel):
    """会话条目（列表页左栏）：展示名、最后一条消息、未读数与成员简况。"""

    model_config = _PASSTHROUGH

    id: int
    type: str
    title: str
    member_ids: list[int] = Field(default_factory=list)
    profiles: list[ChatMemberBrief] = Field(default_factory=list)
    last_message: Optional[ChatMessageItem] = None
    unread: int = 0
    updated_at: Optional[str] = None


class ChatConversationsData(BaseModel):
    model_config = _PASSTHROUGH

    items: list[ChatConversationItem] = Field(default_factory=list)
    unread_total: int = 0
