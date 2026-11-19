"""Webhook 出站订阅服务的单元测试（离线跑，全程不发生真实网络请求）。

覆盖三块：
- 入参校验与创建（validate_events / create_webhook）：事件白名单、回调地址只收
  https、签名密钥只在创建响应里明文出现一次（库里只存 Fernet 加密版）；
- 管理操作（set_active / delete_webhook / test_delivery）：行状态与审计留痕；
- 事件分发（emit）：独立会话、按订阅过滤、成功/失败状态回写，以及投递函数自身
  崩溃时不向外抛异常的韧性（emit 由 degrade_guard 收口）。

emit 依赖两处外部副作用，测试里都做了替换：
- ``webhook_service.async_session`` 换成返回 MemorySession 的异步上下文工厂
  （与 async_sessionmaker 同构：调用后得到的对象支持 ``async with``）；
- ``webhook_service._deliver`` 换成 async 桩（返回 (True, "") 或 (False, "HTTP 500")）。
"""

import unittest
from contextlib import asynccontextmanager
from unittest import mock

from app.core.crypto import decrypt_secret
from app.core.exceptions import AppException
from app.models.admin_audit_log import AdminAuditLog
from app.models.user import User
from app.models.webhook import WEBHOOK_EVENTS, WebhookEndpoint
from app.services import webhook_service
from tests.fake_db import MemorySession

# 事件名别名：测试里引用常量而不是裸字符串，白名单调整时这里跟着模型走
EVENT_PUBLISHED = WEBHOOK_EVENTS[0]  # tutorial.published
EVENT_UNPUBLISHED = WEBHOOK_EVENTS[1]  # tutorial.unpublished
UNKNOWN_EVENT = "user.deleted"  # 白名单外的事件名


def _admin() -> User:
    """操作者：record_admin_action 要求 admin.id 非空才落审计行。"""
    return User(id=1, account="admin@example.com", password_hash="x", role="admin")


def _endpoint(**overrides) -> WebhookEndpoint:
    """构造端点对象：默认订阅 tutorial.published、启用。"""
    fields = dict(
        id=10,
        name="渲染站回调",
        url="https://render.example.com/webhook",
        secret_encrypted="enc:v2:stub-cipher",
        events=[EVENT_PUBLISHED],
        active=True,
    )
    fields.update(overrides)
    return WebhookEndpoint(**fields)


def _row_by_id(db: MemorySession, model, row_id: int) -> dict:
    """从权威行里按 id 取一行（all_rows 返回 dict 列表）。"""
    return next(r for r in db.all_rows(model) if r["id"] == row_id)


def _emit_env(*endpoints) -> tuple[dict, MemorySession]:
    """为 emit 用例搭环境：共享同一 storage 的内存库 + 预置端点。

    emit 内部用 app.database.async_session 另起独立会话（与请求会话无关），
    所以把 webhook_service.async_session 换成返回 MemorySession 的异步上下文工厂；
    多个会话共享同一 storage，等价于「多个连接、同一个库」。
    """
    storage: dict = {}
    seeder = MemorySession(storage=storage)
    for ep in endpoints:
        seeder.seed(ep)
    return storage, seeder


def _session_factory(storage: dict):
    """可调用工厂：调用后得到支持 ``async with`` 的异步上下文，产出 MemorySession。"""

    @asynccontextmanager
    async def factory():
        yield MemorySession(storage=storage)

    return factory


class ValidateEventsTests(unittest.IsolatedAsyncioTestCase):
    """validate_events：事件白名单的子集校验。"""

    async def test_empty_defaults_to_all_events(self):
        """未提供（None）或给空列表 → 默认订阅全部事件。"""
        self.assertEqual(webhook_service.validate_events(None), list(WEBHOOK_EVENTS))
        self.assertEqual(webhook_service.validate_events([]), list(WEBHOOK_EVENTS))

    async def test_unknown_event_rejected_with_name(self):
        """未知事件不静默丢弃：400 且报错信息点名。"""
        with self.assertRaises(AppException) as ctx:
            webhook_service.validate_events([EVENT_PUBLISHED, UNKNOWN_EVENT])
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn(UNKNOWN_EVENT, ctx.exception.message)

    async def test_duplicates_deduped_keeping_order(self):
        """重复事件去重，保留首次出现顺序。"""
        events = webhook_service.validate_events(
            [EVENT_UNPUBLISHED, EVENT_PUBLISHED, EVENT_UNPUBLISHED]
        )
        self.assertEqual(events, [EVENT_UNPUBLISHED, EVENT_PUBLISHED])


class CreateWebhookTests(unittest.IsolatedAsyncioTestCase):
    """create_webhook：签名密钥只下发一次、回调地址与事件集校验、审计留痕。"""

    async def test_secret_returned_once_and_stored_encrypted(self):
        """签名密钥只在创建响应里明文出现一次；库行只存密文。"""
        db = MemorySession()
        result = await webhook_service.create_webhook(
            db, _admin(), "渲染站回调", "https://render.example.com/webhook", [EVENT_PUBLISHED]
        )
        secret = result["secret"]
        self.assertEqual(len(secret), 48)  # token_hex(24) → 48 个十六进制字符
        rows = db.all_rows(WebhookEndpoint)
        self.assertEqual(len(rows), 1)
        cipher = rows[0]["secret_encrypted"]
        # 库里那串密文不含明文、也不等于明文；解回来正好是刚才下发的同一把钥匙
        self.assertNotIn(secret, cipher)
        self.assertNotEqual(cipher, secret)
        self.assertEqual(decrypt_secret(cipher), secret)
        self.assertEqual(result["id"], rows[0]["id"])
        self.assertEqual(result["events"], [EVENT_PUBLISHED])
        self.assertTrue(result["active"])

    async def test_http_url_rejected(self):
        """回调地址只收 https：http 直接 400，且不留任何行。"""
        db = MemorySession()
        with self.assertRaises(AppException) as ctx:
            await webhook_service.create_webhook(
                db, _admin(), "x", "http://render.example.com/webhook", None
            )
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("https", ctx.exception.message)
        self.assertEqual(db.all_rows(WebhookEndpoint), [])

    async def test_unknown_event_rejected(self):
        """事件集越界 → 400，不创建行。"""
        db = MemorySession()
        with self.assertRaises(AppException) as ctx:
            await webhook_service.create_webhook(
                db, _admin(), "x", "https://a.example.com/h", [UNKNOWN_EVENT]
            )
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(db.all_rows(WebhookEndpoint), [])

    async def test_create_writes_audit_row(self):
        """创建成功后留下 webhook_create 审计行，目标指向新端点。"""
        db = MemorySession()
        result = await webhook_service.create_webhook(
            db, _admin(), "渲染站回调", "https://render.example.com/webhook", [EVENT_PUBLISHED]
        )
        audits = db.all_rows(AdminAuditLog)
        self.assertEqual(len(audits), 1)
        audit = audits[0]
        self.assertEqual(audit["action"], "webhook_create")
        self.assertEqual(audit["target_type"], "webhook")
        self.assertEqual(audit["target_id"], result["id"])


class SetActiveTests(unittest.IsolatedAsyncioTestCase):
    """set_active：启停翻转 + 审计。"""

    async def test_flip_active_and_audit(self):
        """启用 → 停用：行上 active 翻转，并留 webhook_set_active 审计。"""
        db = MemorySession()
        db.seed(_endpoint())
        result = await webhook_service.set_active(db, _admin(), 10, False)
        self.assertFalse(result["active"])
        row = _row_by_id(db, WebhookEndpoint, 10)
        self.assertFalse(row["active"])
        audits = db.all_rows(AdminAuditLog)
        self.assertEqual(audits[0]["action"], "webhook_set_active")
        self.assertEqual(audits[0]["detail"], {"active": False})

    async def test_missing_webhook_404(self):
        """查无此端点 → 404。"""
        db = MemorySession()
        with self.assertRaises(AppException) as ctx:
            await webhook_service.set_active(db, _admin(), 99, True)
        self.assertEqual(ctx.exception.code, 404)


class DeleteWebhookTests(unittest.IsolatedAsyncioTestCase):
    """delete_webhook：行删除 + 审计。"""

    async def test_delete_removes_row_and_writes_audit(self):
        """删除目标行、其余行保留，并留 webhook_delete 审计。"""
        db = MemorySession()
        db.seed(_endpoint(id=10), _endpoint(id=11, name="索引器回调"))
        self.assertTrue(await webhook_service.delete_webhook(db, _admin(), 10))
        remaining = db.all_rows(WebhookEndpoint)
        self.assertEqual([r["id"] for r in remaining], [11])
        audits = db.all_rows(AdminAuditLog)
        self.assertEqual(audits[0]["action"], "webhook_delete")
        self.assertEqual(audits[0]["target_id"], 10)

    async def test_missing_webhook_404(self):
        """查无此端点 → 404。"""
        db = MemorySession()
        with self.assertRaises(AppException) as ctx:
            await webhook_service.delete_webhook(db, _admin(), 99)
        self.assertEqual(ctx.exception.code, 404)


class TestDeliveryTests(unittest.IsolatedAsyncioTestCase):
    """test_delivery：手动试投（_deliver 已替换为桩，不发真实请求）。"""

    async def test_success_returns_ok_and_audits(self):
        """试投成功：ok=True、无错误信息，并留 webhook_test 审计。"""
        db = MemorySession()
        db.seed(_endpoint())
        calls: list = []

        async def stub(row, event, payload):
            calls.append(event)
            return True, ""

        with mock.patch.object(webhook_service, "_deliver", stub):
            result = await webhook_service.test_delivery(db, _admin(), 10)

        self.assertTrue(result["ok"])
        self.assertEqual(result["error"], "")
        self.assertEqual(calls, [EVENT_PUBLISHED])  # 试投定向用第一个注册事件
        audits = db.all_rows(AdminAuditLog)
        self.assertEqual(audits[0]["action"], "webhook_test")

    async def test_failure_returns_reason(self):
        """试投失败：ok=False、错误信息带原因。"""
        db = MemorySession()
        db.seed(_endpoint())

        async def stub(row, event, payload):
            return False, "HTTP 500"

        with mock.patch.object(webhook_service, "_deliver", stub):
            result = await webhook_service.test_delivery(db, _admin(), 10)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "HTTP 500")


class EmitTests(unittest.IsolatedAsyncioTestCase):
    """emit：按订阅分发事件（独立会话 + 投递桩，均被替换）。"""

    async def test_unregistered_event_returns_without_delivery(self):
        """白名单外的事件名直接 return，投递桩一次都不被调用。"""
        storage, seeder = _emit_env(_endpoint())
        calls: list = []

        async def stub(row, event, payload):
            calls.append((row.id, event))
            return True, ""

        with mock.patch.object(webhook_service, "async_session", _session_factory(storage)):
            with mock.patch.object(webhook_service, "_deliver", stub):
                await webhook_service.emit(UNKNOWN_EVENT, {"id": 1})

        self.assertEqual(calls, [])
        self.assertIsNone(_row_by_id(seeder, WebhookEndpoint, 10)["last_status"])

    async def test_delivers_to_active_subscriber_and_marks_success(self):
        """启用且订阅了该事件的端点被投递：状态 success、连续失败清零。"""
        storage, seeder = _emit_env(_endpoint())
        calls: list = []

        async def stub(row, event, payload):
            calls.append((row.id, event, payload))
            return True, ""

        with mock.patch.object(webhook_service, "async_session", _session_factory(storage)):
            with mock.patch.object(webhook_service, "_deliver", stub):
                await webhook_service.emit(EVENT_PUBLISHED, {"id": 7})

        self.assertEqual([(c[0], c[1]) for c in calls], [(10, EVENT_PUBLISHED)])
        self.assertEqual(calls[0][2], {"id": 7})
        row = _row_by_id(seeder, WebhookEndpoint, 10)
        self.assertEqual(row["last_status"], "success")
        self.assertEqual(row["last_event"], EVENT_PUBLISHED)
        self.assertEqual(row["failure_streak"], 0)
        self.assertIsNone(row["last_error"])

    async def test_inactive_endpoint_not_delivered(self):
        """订阅了事件但已停用的端点不投递（查询只取启用行）。"""
        storage, seeder = _emit_env(_endpoint(active=False))
        calls: list = []

        async def stub(row, event, payload):
            calls.append(row.id)
            return True, ""

        with mock.patch.object(webhook_service, "async_session", _session_factory(storage)):
            with mock.patch.object(webhook_service, "_deliver", stub):
                await webhook_service.emit(EVENT_PUBLISHED, {})

        self.assertEqual(calls, [])
        self.assertIsNone(_row_by_id(seeder, WebhookEndpoint, 10)["last_status"])

    async def test_failure_records_status_error_and_streak(self):
        """投递失败：last_status=failed、last_error 带原因、连续失败逐次累加。"""
        storage, seeder = _emit_env(_endpoint())

        async def stub(row, event, payload):
            return False, "HTTP 500"

        with mock.patch.object(webhook_service, "async_session", _session_factory(storage)):
            with mock.patch.object(webhook_service, "_deliver", stub):
                await webhook_service.emit(EVENT_PUBLISHED, {})
                await webhook_service.emit(EVENT_PUBLISHED, {})

        row = _row_by_id(seeder, WebhookEndpoint, 10)
        self.assertEqual(row["last_status"], "failed")
        self.assertEqual(row["last_error"], "HTTP 500")
        self.assertEqual(row["failure_streak"], 2)  # 两次投递都失败 → 连续计数累加


class EmitResilienceTests(unittest.IsolatedAsyncioTestCase):
    """emit 的韧性：投递函数自身崩溃（抛异常而非返回失败）也不得外溢。"""

    async def test_delivery_crash_does_not_escape_emit(self):
        """投递桩抛异常时 emit 由 degrade_guard 收口，不向外抛。

        这里没写 assertRaises —— 若 emit 把异常抛出来，本用例自然红。
        """
        storage, seeder = _emit_env(_endpoint())
        calls: list = []

        async def broken(row, event, payload):
            calls.append(row.id)
            raise RuntimeError("投递通道崩溃（模拟）")

        with mock.patch.object(webhook_service, "async_session", _session_factory(storage)):
            with mock.patch.object(webhook_service, "_deliver", broken):
                await webhook_service.emit(EVENT_PUBLISHED, {})

        self.assertEqual(calls, [10])  # 确认真的走到了投递这一步，而不是提前 return
        row = _row_by_id(seeder, WebhookEndpoint, 10)
        self.assertIsNone(row["last_status"])  # 崩溃发生在状态回写之前，行保持原样
