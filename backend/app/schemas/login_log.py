"""登录日志（/admin/login-logs）出入参模型。"""

from pydantic import BaseModel, Field


class LoginLogItem(BaseModel):
    """一条登录事件（账号已脱敏）"""

    id: int | None = None
    user_id: int | None = None
    account: str = ""
    status: str = ""
    message: str = ""
    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    created_at: str | None = None


class LoginLogDeleteRequest(BaseModel):
    """批量删除入参（调用方须写审计）"""

    ids: list[int] = Field(..., min_length=1, max_length=200, description="要删除的日志 ID 列表")
