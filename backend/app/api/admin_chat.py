"""站内聊天接口：REST（会话/消息/已读/未读）与 WebSocket 实时推送。

架构取向：**HTTP 写、WS 推** —— 发消息走 REST（落库即成功，可靠性不依赖连接状态），
服务端再把新消息推给在线成员；断线者重连后用 REST 拉历史兜底。WS 只做「推送 +
心跳」，不承担写入，消息就不会因为半路断链而丢。

WS 鉴权（刻意不用依赖注入）：浏览器 WebSocket 不能带 Authorization 头，握手请求
复用登录时种下的 httpOnly `ACCESS_TOKEN_COOKIE`（同源 SPA 自动携带；不提供 query
token 兜底 —— URL 会进访问日志，等于把凭据写进磁盘）。也不挂 `_admin_shared` 的
路由守卫：`check_route_permission` 是 HTTP 语义（403 JSON 响应），在 websocket
scope 里无法返回 —— WS 端点独立注册，鉴权、admin 校验与模块开关在这里自己收口。

不做（与 FastapiAdmin chat 模块同口径的边界）：文件传输、撤回、已读回执、多端
同步、Redis 扇出。项目是单实例部署（README 已声明），连接管理在进程内完成即可；
未来横向扩展时在此接入 Redis pub/sub，REST 契约不变。
"""

import asyncio
import json

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.core.security import ACCESS_TOKEN_COOKIE, decode_token, is_access_token_revoked
from app.database import get_db
from app.models.user import User
from app.module_registry import is_enabled
from app.schemas.chat import (
    ChatConversationCreated,
    ChatConversationsData,
    ChatMessageCreateRequest,
    ChatMessageItem,
    ChatMessagesData,
    ChatGroupCreateRequest,
    ChatReadAck,
    ChatUnreadData,
)
from app.schemas.common import ApiResponse
from app.services import chat_service

# ---------------- REST（挂共享 router，走常规守卫） ----------------


@router.get("/chat/conversations", response_model=ApiResponse[ChatConversationsData])
async def list_chat_conversations(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """我的会话列表（含最后一条消息、未读数），按最近消息时间倒序。"""
    return success_response(await chat_service.list_conversations(db, admin))


@router.post("/chat/direct/{user_id}", response_model=ApiResponse[ChatConversationCreated])
async def open_direct_conversation(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """发起/获取单聊（幂等）：同一对管理员永远返回同一个会话。"""
    result = await chat_service.get_or_create_direct(db, admin, user_id)
    return success_response(result, "会话已就绪" if result["created"] else "会话已存在")


@router.post("/chat/group", response_model=ApiResponse[ChatConversationCreated])
async def create_chat_group(
    req: ChatGroupCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """建群：成员（含发起人）≥3，建群后成员固定。"""
    result = await chat_service.create_group(db, admin, req.title, req.member_ids)
    return success_response(result, "群聊已创建")


@router.get("/chat/conversations/{conversation_id}/messages", response_model=ApiResponse[ChatMessagesData])
async def list_chat_messages(
    conversation_id: int,
    before_id: int | None = Query(None, description="取该消息 id 之前的更早消息"),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """历史消息（游标分页，正序返回）——断线重连与「加载更多」共用。"""
    return success_response(
        await chat_service.list_messages(db, admin, conversation_id, before_id=before_id, limit=limit)
    )


@router.post("/chat/conversations/{conversation_id}/messages", response_model=ApiResponse[ChatMessageItem])
async def send_chat_message(
    conversation_id: int,
    req: ChatMessageCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """发消息（HTTP 写）：落库成功后由 WS 推给在线成员，断线者靠历史接口兜底。"""
    message = await chat_service.send_message(db, admin, conversation_id, req.content)
    # 扇出失败（对方断线/满员）只影响实时性：消息已落库，重连后拉历史可见
    await _push_to_users(
        await chat_service._member_ids(db, conversation_id),
        {"type": "message", "conversation_id": conversation_id, "message": message},
    )
    return success_response(message, "已发送")


@router.post("/chat/conversations/{conversation_id}/read", response_model=ApiResponse[ChatReadAck])
async def mark_chat_read(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """标记已读：游标推进到会话当前最大消息 id。"""
    return success_response(await chat_service.mark_read(db, admin, conversation_id))


@router.get("/chat/unread", response_model=ApiResponse[ChatUnreadData])
async def chat_unread_total(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """全站未读总数（顶栏角标轮询用）。"""
    return success_response({"total": await chat_service.unread_total(db, admin)})


# ---------------- WebSocket（独立 router，cookie 鉴权 + 进程内连接管理） ----------------

chat_ws_router = APIRouter()

# user_id -> 该用户当前打开的 WS 连接集合（多标签页各一条，全活）
_connections: dict[int, set[WebSocket]] = {}
_connections_lock = asyncio.Lock()


async def _register(user_id: int, websocket: WebSocket) -> None:
    async with _connections_lock:
        _connections.setdefault(user_id, set()).add(websocket)


async def _unregister(user_id: int, websocket: WebSocket) -> None:
    async with _connections_lock:
        conns = _connections.get(user_id)
        if conns is not None:
            conns.discard(websocket)
            if not conns:
                _connections.pop(user_id, None)


async def _push_to_users(user_ids: list[int], payload: dict) -> None:
    """把事件推给目标用户的全部在线连接；失败连接就地摘除（下次重连拉 REST 兜底）。

    模块内共享：REST 发消息与未来的系统事件都走这一个出口。
    """
    if not user_ids:
        return
    text = json.dumps(payload, ensure_ascii=False)
    async with _connections_lock:
        targets = [ws for uid in user_ids for ws in _connections.get(uid, ())]
    for ws in targets:
        try:
            await ws.send_text(text)
        except Exception as exc:  # noqa: BLE001 - 单连接失败只影响它自己，摘除即可
            logger.debug(f"chat WS 推送失败（连接已摘除）: {type(exc).__name__}: {exc}")
            for uid in user_ids:
                await _unregister(uid, ws)


def _reset_connections() -> None:
    """仅供测试：清空连接表。"""
    _connections.clear()


async def _authenticate_ws(websocket: WebSocket, db: AsyncSession) -> User | None:
    """握手鉴权：httpOnly cookie → 解 token → 撤销校验 → 加载用户 → 必须 admin。

    任何一步不过都以 4401/4403 关闭（业务码放在 close code 里，前端据此提示）。
    """
    token = websocket.cookies.get(ACCESS_TOKEN_COOKIE)
    payload = decode_token(token) if token else None
    if not payload or payload.get("type") != "access":
        await websocket.close(code=4401)
        return None
    if await is_access_token_revoked(payload):
        await websocket.close(code=4401)
        return None
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        await websocket.close(code=4401)
        return None
    # 用户加载与 admin 校验收口在服务层（路由层不写 ORM）
    return await chat_service.load_ws_user(db, user_id)


@chat_ws_router.websocket("/ws/chat")
async def chat_ws_endpoint(
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
):
    """聊天实时通道：只推不写。

    客户端发 `"ping"` 心跳，服务端回 `"pong"`（穿透代理的空闲超时）；其余消息一律
    忽略 —— 写入统一走 REST，避免两套写路径各自校验。
    """
    # 模块开关：聊天被裁剪时直接拒绝升级（与 REST 的 fail-closed 同方向）
    if not is_enabled("chat"):
        await websocket.close(code=4404)
        return
    user = await _authenticate_ws(websocket, db)
    if user is None:
        return
    await websocket.accept()
    await _register(user.id, websocket)
    logger.info(f"chat WS 已连接: admin={user.id}")
    try:
        while True:
            text = await websocket.receive_text()
            if text == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        await _unregister(user.id, websocket)
        logger.info(f"chat WS 已断开: admin={user.id}")
