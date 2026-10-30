"""登录会话（登录设备）的响应模型。

刻意不返回 access token / refresh token，也不返回完整 UA 原文：
列表只回答「哪台设备、什么时候登录、最近什么时候活跃」，
多给一个字段就多一分被日志或截图带出去的风险。
"""

from pydantic import BaseModel, Field


class SessionItem(BaseModel):
    """一条登录会话"""

    sid: str = Field(..., description="会话 id（踢设备时用它）")
    device: str = Field(..., description="设备标签，如「Chrome · Windows」")
    ip: str = Field("", description="登录时的客户端 IP")
    created_at: str = Field(..., description="登录时间（ISO 8601）")
    last_seen: str = Field(..., description="最近活跃时间（ISO 8601）")
    current: bool = Field(False, description="是否为本次请求所在的会话")


class SessionListData(BaseModel):
    items: list[SessionItem]
    total: int


class SessionRevokeResult(BaseModel):
    """吊销结果：踢掉了几个会话"""

    revoked: int
