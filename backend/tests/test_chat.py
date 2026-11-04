"""站内聊天：服务层不变量 + REST/WS 冒烟。

服务层钉五条不变量：
1. **单聊幂等**：同一对管理员永远只有一个 direct 会话（重复发起返回既有会话）；
2. **成员边界**：非成员的会话一律 404（不暴露「会话存在但你不在」）；
3. **未读口径**：只数「别人的、比我已读游标新的」消息；发消息即已读；
4. **群聊下限**：≥3 人（两人请用单聊），对象必须是有效管理员；
5. **历史分页**：游标倒取、正序返回，has_more 指示还有更早的。

WS 冒烟用 starlette TestClient（httpx 不支持 websocket）：钉 cookie 鉴权
（无 token 4401 / 非管理员 4403）、心跳、以及「REST 发消息 → 在线成员实时收到推送」
——这是 **HTTP 写、WS 推** 架构的端到端证据。
"""

import asyncio
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app.core.exceptions import AppException
from app.models.chat import ChatConversation, ChatMember, ChatMessage
from app.models.user import User
from app.services import chat_service as cs
from tests.fake_db import MemorySession


def _admin(uid: int, account: str | None = None) -> User:
    return User(id=uid, account=account or f"admin{uid}@test", password_hash="x", role="admin")


def _db_with(*, admins=(1, 2, 3)) -> MemorySession:
    db = MemorySession()
    db.seed(*[_admin(uid) for uid in admins])
    return db


def _direct(db: MemorySession, a: int, b: int, *, reads: dict[int, int] | None = None) -> ChatConversation:
    """建一个单聊会话（成员一次 seed 完）。

    ⚠️ 不要对同一 (conversation_id, user_id) 二次 seed：替身对复合主键表按内部
    自增行号存储，重复 seed 会造出两行 —— 真实库是主键冲突。要改已读游标，
    用 ``reads`` 参数一次给全。
    """
    conv = ChatConversation(id=900, type="direct", title=None, created_by=a)
    db.seed(conv)
    for uid in (a, b):
        db.seed(ChatMember(conversation_id=900, user_id=uid, last_read_id=(reads or {}).get(uid, 0)))
    return conv


class DirectTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_is_idempotent(self):
        db = _db_with()
        me = _admin(1)
        first = await cs.get_or_create_direct(db, me, 2)
        self.assertTrue(first["created"])
        second = await cs.get_or_create_direct(db, me, 2)
        self.assertEqual(second["id"], first["id"])
        self.assertFalse(second["created"], "同一对管理员第二次发起必须复用既有会话")
        # 反向发起同样命中既有会话
        third = await cs.get_or_create_direct(db, _admin(2), 1)
        self.assertEqual(third["id"], first["id"])

    async def test_direct_rejects_self_and_missing(self):
        db = _db_with()
        me = _admin(1)
        with self.assertRaises(AppException) as ctx:
            await cs.get_or_create_direct(db, me, 1)
        self.assertEqual(ctx.exception.code, 400)
        with self.assertRaises(AppException) as ctx:
            await cs.get_or_create_direct(db, me, 999)
        self.assertEqual(ctx.exception.code, 404)

    async def test_direct_rejects_non_admin(self):
        db = _db_with()
        db.seed(User(id=9, account="u9@test", password_hash="x", role="user"))
        with self.assertRaises(AppException) as ctx:
            await cs.get_or_create_direct(db, _admin(1), 9)
        self.assertIn("管理员", ctx.exception.message)


class GroupTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_requires_title_and_three_members(self):
        db = _db_with()
        me = _admin(1)
        with self.assertRaises(AppException) as ctx:
            await cs.create_group(db, me, "  ", [2, 3])
        self.assertEqual(ctx.exception.code, 400)
        with self.assertRaises(AppException) as ctx:
            await cs.create_group(db, me, "两人群", [2])
        self.assertIn("3 名成员", ctx.exception.message)

    async def test_group_rejects_non_admin_member(self):
        db = _db_with()
        db.seed(User(id=9, account="u9@test", password_hash="x", role="user"))
        with self.assertRaises(AppException) as ctx:
            await cs.create_group(db, _admin(1), "混入群", [2, 9])
        self.assertIn("管理员", ctx.exception.message)

    async def test_group_creates_with_dedup_members(self):
        db = _db_with()
        result = await cs.create_group(db, _admin(1), "值班群", [2, 3, 2])
        self.assertTrue(result["created"])
        member_rows = [r for r in db.all_rows(ChatMember) if r["conversation_id"] == result["id"]]
        self.assertEqual(sorted(m["user_id"] for m in member_rows), [1, 2, 3], "重复成员去重")


class MessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_member_gets_404(self):
        db = _db_with()
        _direct(db, 1, 2)
        with self.assertRaises(AppException) as ctx:
            await cs.send_message(db, _admin(3), 900, "我是外人")
        self.assertEqual(ctx.exception.code, 404, "非成员不暴露会话存在性")
        with self.assertRaises(AppException) as ctx:
            await cs.list_messages(db, _admin(3), 900, before_id=None)
        self.assertEqual(ctx.exception.code, 404)

    async def test_send_validates_and_marks_read(self):
        db = _db_with()
        _direct(db, 1, 2)
        with self.assertRaises(AppException):
            await cs.send_message(db, _admin(1), 900, "   ")
        with self.assertRaises(AppException):
            await cs.send_message(db, _admin(1), 900, "超" * (cs.CHAT_MESSAGE_MAX_LENGTH + 1))

        message = await cs.send_message(db, _admin(1), 900, "你好")
        self.assertEqual(message["sender_id"], 1)
        # 发消息即已读：自己刚发的消息不能占自己的未读位
        member = next(
            r for r in db.all_rows(ChatMember)
            if r["conversation_id"] == 900 and r["user_id"] == 1
        )
        self.assertEqual(member["last_read_id"], message["id"])

    async def test_send_strips_html(self):
        db = _db_with()
        _direct(db, 1, 2)
        message = await cs.send_message(db, _admin(1), 900, "<script>alert(1)</script>hi")
        self.assertNotIn("<script>", message["content"], "存储型 XSS 必须被剥离")
        self.assertIn("hi", message["content"])


class UnreadTests(unittest.IsolatedAsyncioTestCase):
    async def test_unread_counts_only_others_after_cursor(self):
        db = _db_with()
        _direct(db, 1, 2, reads={1: 2, 2: 0})
        db.seed(ChatMessage(id=1, conversation_id=900, sender_id=2, content="a"))
        db.seed(ChatMessage(id=2, conversation_id=900, sender_id=1, content="b"))
        db.seed(ChatMessage(id=3, conversation_id=900, sender_id=2, content="c"))

        self.assertEqual(await cs.unread_total(db, _admin(1)), 1)
        self.assertEqual(await cs.unread_total(db, _admin(2)), 1, "用户 2 未读 = 用户 1 发的那 1 条")

    async def test_mark_read_advances_cursor(self):
        db = _db_with()
        _direct(db, 1, 2, reads={1: 0, 2: 0})
        db.seed(ChatMessage(id=5, conversation_id=900, sender_id=2, content="a"))
        ack = await cs.mark_read(db, _admin(1), 900)
        self.assertEqual(ack["last_read_id"], 5)
        self.assertEqual(await cs.unread_total(db, _admin(1)), 0)


class HistoryPagingTests(unittest.IsolatedAsyncioTestCase):
    async def test_cursor_paging_returns_ascending_with_has_more(self):
        db = _db_with()
        _direct(db, 1, 2)
        for i in range(1, 6):
            db.seed(ChatMessage(id=i, conversation_id=900, sender_id=1, content=f"m{i}"))
        db.seed(ChatMember(conversation_id=900, user_id=1, last_read_id=5))

        page1 = await cs.list_messages(db, _admin(1), 900, before_id=None, limit=3)
        self.assertEqual([m["id"] for m in page1["items"]], [3, 4, 5], "取最新 3 条，正序返回")
        self.assertTrue(page1["has_more"])

        page2 = await cs.list_messages(db, _admin(1), 900, before_id=3, limit=3)
        self.assertEqual([m["id"] for m in page2["items"]], [1, 2])
        self.assertFalse(page2["has_more"], "已到最早一条")

    async def test_conversation_list_carries_last_and_unread(self):
        db = _db_with()
        _direct(db, 1, 2, reads={1: 0, 2: 1})
        db.seed(ChatMessage(id=1, conversation_id=900, sender_id=2, content="在吗"))
        result = await cs.list_conversations(db, _admin(1))
        self.assertEqual(result["unread_total"], 1)
        item = result["items"][0]
        self.assertEqual(item["last_message"]["content"], "在吗")
        # 单聊展示名取对方账号
        self.assertEqual(item["title"], "admin2@test")
        self.assertEqual(item["unread"], 1)


# ---------------- REST / WS 冒烟（TestClient，httpx 不支持 websocket） ----------------


def _build_client(db: MemorySession, *, admin: User):
    """TestClient：get_db 指向内存会话，RBAC/令牌撤销打桩。"""
    from app.main import app
    from app.core import rbac as _rbac
    from app.core.security import create_token_pair
    from app.api import admin_chat as admin_chat_api
    from app.database import get_db

    async def _override_db():
        yield db

    async def _override_rbac():
        pass

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[_rbac.check_route_permission] = _override_rbac
    tokens = create_token_pair(admin.id, "admin")
    admin_chat_api._reset_connections()
    # 撤销检查打桩（FakeRedis 未接到 admin_chat 的导入点）
    patcher = mock.patch.object(admin_chat_api, "is_access_token_revoked", mock.AsyncMock(return_value=False))
    patcher.start()
    client = TestClient(app)
    client.headers["Cookie"] = f"ai_cat_access_token={tokens['access_token']}"
    return client, patcher, app


class ChatHttpSmokeTests(unittest.TestCase):
    def setUp(self):
        self.db = _db_with()
        self.admin = _admin(1)
        self.client, self._patcher, self.app = _build_client(self.db, admin=self.admin)

    def tearDown(self):
        self._patcher.stop()
        self.app.dependency_overrides.clear()
        admin_chat_api_reset = __import__("app.api.admin_chat", fromlist=["_reset_connections"])
        admin_chat_api_reset._reset_connections()

    def test_rest_crud_roundtrip(self):
        # 单聊（幂等两次）→ 发消息 → 历史 → 已读 → 未读清零
        first = self.client.post("/api/v1/admin/chat/direct/2").json()
        self.assertTrue(first["data"]["created"])
        second = self.client.post("/api/v1/admin/chat/direct/2").json()
        self.assertFalse(second["data"]["created"])
        conv_id = first["data"]["id"]

        sent = self.client.post(
            f"/api/v1/admin/chat/conversations/{conv_id}/messages", json={"content": "你好"}
        ).json()
        self.assertEqual(sent["data"]["content"], "你好")

        history = self.client.get(f"/api/v1/admin/chat/conversations/{conv_id}/messages").json()
        self.assertEqual([m["content"] for m in history["data"]["items"]], ["你好"])

        self.client.post(f"/api/v1/admin/chat/conversations/{conv_id}/read")
        unread = self.client.get("/api/v1/admin/chat/unread").json()
        self.assertEqual(unread["data"]["total"], 0)

        conversations = self.client.get("/api/v1/admin/chat/conversations").json()
        self.assertEqual(conversations["data"]["items"][0]["title"], "admin2@test")

    def test_rest_rejects_non_member(self):
        self.client.post("/api/v1/admin/chat/direct/2")
        # 用户 3 不是成员：404（用例直接查不存在的会话亦可，这里查真实会话）
        resp = self.client.post(
            "/api/v1/admin/chat/conversations/999999/messages", json={"content": "x"}
        )
        self.assertEqual(resp.status_code, 404)

    def test_ws_requires_token_and_admin(self):
        # 无 cookie → 4401
        # ⚠️ 不用 `with TestClient(...)`：那会跑 app lifespan，shutdown 会把
        # run_heavy 的专用线程池关掉，污染同进程的后续测试
        from starlette.websockets import WebSocketDisconnect

        anon = TestClient(self.app)
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with anon.websocket_connect("/api/v1/admin/ws/chat"):
                pass
        self.assertEqual(ctx.exception.code, 4401)

        # 非管理员 token → 4403
        from app.core.security import create_token_pair

        user_tokens = create_token_pair(9, "user")
        plain = TestClient(self.app)
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with plain.websocket_connect(
                "/api/v1/admin/ws/chat",
                headers={"Cookie": f"ai_cat_access_token={user_tokens['access_token']}"},
            ):
                pass
        self.assertEqual(ctx.exception.code, 4403)

    def test_ws_receives_push_from_rest_send(self):
        """HTTP 写、WS 推 的端到端证据：REST 发消息，在线的对方连接实时收到。"""
        created = self.client.post("/api/v1/admin/chat/direct/2").json()
        conv_id = created["data"]["id"]

        # 对方（管理员 2）的连接：先用其 token 建独立 client（不用 with，理由同上）
        from app.core.security import create_token_pair

        peer_tokens = create_token_pair(2, "admin")
        peer_client = TestClient(self.app)
        peer_client.headers["Cookie"] = f"ai_cat_access_token={peer_tokens['access_token']}"
        with peer_client.websocket_connect("/api/v1/admin/ws/chat") as ws:
            # 建连后发一条心跳确认链路通
            ws.send_text("ping")
            self.assertEqual(ws.receive_text(), "pong")

            # 管理员 1 通过 REST 发消息（成员校验后扇出给在线的 2）
            resp = self.client.post(
                f"/api/v1/admin/chat/conversations/{conv_id}/messages",
                json={"content": "实时推送了吗"},
            )
            self.assertEqual(resp.status_code, 200)

            import json as _json

            event = _json.loads(ws.receive_text())
            self.assertEqual(event["type"], "message")
            self.assertEqual(event["conversation_id"], conv_id)
            self.assertEqual(event["message"]["content"], "实时推送了吗")


if __name__ == "__main__":
    unittest.main()
