"""登录设备（会话）路由。

这三个端点都是「操作自己名下的会话」，所以只要求已登录管理员，不设细粒度权限码 ——
但**仍必须在 core/rbac.py 规则表登记**（空权限列表 = 仅需 admin 门槛），
否则 fail-closed 会直接 403（见 rbac.py 文件头）。

强制下线别人是另一回事：那是「让别人立刻失去访问权」，挂在用户管理下，
需要 `user:edit` 并强制留痕（见 POST /users/{user_id}/force-logout）。
"""

from fastapi import Cookie, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.core.security import ACCESS_TOKEN_COOKIE, decode_token
from app.database import get_db
from app.dependencies import pick_token
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.session import SessionListData, SessionRevokeResult
from app.services import session_service


def _current_sid(authorization: str | None, access_token_cookie: str | None) -> str | None:
    """本次请求来自哪个会话（用来在列表里标「本机」）。旧 token 无 sid → None。"""
    token = pick_token(authorization, access_token_cookie)
    payload = decode_token(token) if token else None
    if not payload or payload.get("type") != "access":
        return None
    return payload.get("sid")


@router.get("/sessions", response_model=ApiResponse[SessionListData])
async def list_my_sessions(
    current_user: User = Depends(require_admin),
    authorization: str | None = Header(None),
    access_token_cookie: str | None = Cookie(None, alias=ACCESS_TOKEN_COOKIE),
):
    """我的登录设备：哪台设备、什么时候登录、最近什么时候活跃。"""
    current_sid = _current_sid(authorization, access_token_cookie)
    items = await session_service.list_my_sessions(current_user.id, current_sid)
    return success_response({"items": items, "total": len(items)})


@router.delete("/sessions", response_model=ApiResponse[SessionRevokeResult])
async def revoke_other_sessions(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None),
    access_token_cookie: str | None = Cookie(None, alias=ACCESS_TOKEN_COOKIE),
):
    """退出其他设备（保留当前这台）。"""
    current_sid = _current_sid(authorization, access_token_cookie)
    revoked = await session_service.revoke_my_other_sessions(db, current_user, current_sid)
    return success_response({"revoked": revoked}, f"已退出其他设备（{revoked} 个）")


@router.delete("/sessions/{sid}", response_model=ApiResponse[SessionRevokeResult])
async def revoke_one_session(
    sid: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """下线指定的一个设备。踢的是当前这台时等价于登出，前端需自行清理本地令牌。"""
    await session_service.revoke_my_session(db, current_user, sid, current_sid=None)
    return success_response({"revoked": 1}, "已下线该设备")


@router.post("/users/{user_id}/force-logout", response_model=ApiResponse[SessionRevokeResult])
async def force_logout_user(
    user_id: int,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """强制下线某个用户的全部设备（改密、怀疑盗号、离职交接时用）。"""
    revoked = await session_service.force_logout_user(db, current_user, user_id)
    return success_response({"revoked": revoked}, f"已强制下线该用户（{revoked} 个会话）")
