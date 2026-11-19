"""开放 API（访问令牌 / Webhook / elevation）响应模型（2026-11-20）。

契约进 OpenAPI 快照 → api.gen.d.ts，字段漂移受 test_openapi_snapshot 守卫。
时间列多为可空（数据库默认值未回填的瞬间），全部 Optional。
"""

from datetime import datetime

from pydantic import BaseModel


class ApiTokenItemData(BaseModel):
    """令牌清单行（脱敏：只有前缀，无摘要无明文）"""

    id: int
    name: str
    prefix: str
    scopes: list[str] = []
    created_at: datetime | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked: bool = False
    expired: bool = False


class ApiTokenListData(BaseModel):
    items: list[ApiTokenItemData] = []


class ApiTokenCreatedData(BaseModel):
    """签发结果：token 明文只在本次响应出现一次"""

    id: int
    token: str
    prefix: str
    scopes: list[str] = []
    expires_at: datetime | None = None


class ApiTokenRevokeData(BaseModel):
    ok: bool = True
    revoked: bool = False


class WebhookItemData(BaseModel):
    id: int
    name: str
    url: str
    events: list[str] = []
    active: bool = True
    created_at: str | None = None
    last_status: str | None = None
    last_event: str | None = None
    last_delivered_at: str | None = None
    last_error: str | None = None
    failure_streak: int = 0


class WebhookListData(BaseModel):
    items: list[WebhookItemData] = []


class WebhookCreatedData(BaseModel):
    """创建结果：secret 明文只在本次响应出现一次（订阅方验签用）"""

    id: int
    name: str
    url: str
    events: list[str] = []
    active: bool = True
    secret: str


class WebhookStateData(BaseModel):
    """启停 / 试投后的端点状态"""

    id: int
    name: str
    url: str
    events: list[str] = []
    active: bool = True


class WebhookTestResultData(BaseModel):
    id: int
    name: str
    ok: bool = False
    error: str | None = None


class WebhookDeleteData(BaseModel):
    ok: bool = True
    deleted: bool = False


class ElevationData(BaseModel):
    """密码二次校验签发的短时凭据（5 分钟窗口）"""

    token: str
    expires_in: int
