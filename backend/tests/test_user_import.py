"""用户批量导入契约测试。

钉住四条**破了不报错、只会悄悄失去意义**的契约：

1. **原子性**：任一行校验失败整体取消（不写任何账号）—— 半批生效的账号
   是运营事故（部分访问权已授出但运营以为整体失败）。
2. **行级报告**：失败必须带行号与原因（贴回 CSV 的行号，不是数组下标）。
3. **密码同口径**：导入与注册/建号走同一份强度策略，不存在「导入侧更松」。
4. **汇总审计**：导入完成写一条 user_batch_import（高危级），带账号清单；
   密码列绝不进审计 detail。
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from app.core.exceptions import AppException
from app.services import user_import_service as uis


def _run(coro):
    return asyncio.run(coro)


def _result(first_row=None):
    return SimpleNamespace(
        scalar=lambda: None,
        first=lambda: first_row,
        all=lambda: [],
        rowcount=0,
    )


def _fake_db(first_rows: dict[str, object] | None = None):
    """按查询账号返回预设行的假会话；记录 add/commit 调用"""
    rows = first_rows or {}
    added: list = []

    async def _call(*args, **kwargs):
        # text 查询的绑定参数里带 account：按它返回预设行（Row 用字符串索引取列）
        params = args[1] if len(args) > 1 else kwargs.get("params")
        account = (params or {}).get("account")
        row = rows.get(account)
        return _result(first_row=dict(deleted_at=row) if row is not None else None)

    return SimpleNamespace(execute=_call, add=added.append, commit=mock.AsyncMock(), rollback=mock.AsyncMock()), added


def _csv(*lines: str) -> bytes:
    header = "account,password,role,nickname"
    return "\n".join([header, *lines]).encode("utf-8")


class ParseTests(unittest.TestCase):
    def test_parse_accepts_bom_and_reorders_columns(self):
        raw = b"\xef\xbb\xbf" + "nickname,account,role,password\nå°æ,user1@example.com,user,abc12345\n".encode("utf-8")
        records = uis.parse_csv(raw)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["account"], "user1@example.com")
        self.assertEqual(records[0]["nickname"], "å°æ")

    def test_parse_rejects_missing_column(self):
        with self.assertRaises(AppException) as ctx:
            uis.parse_csv("account,role\nuser1,user".encode("utf-8"))
        self.assertIn("password", ctx.exception.message)

    def test_parse_rejects_empty_file(self):
        with self.assertRaises(AppException):
            uis.parse_csv(b"\n\n")

    def test_parse_quoted_values_with_comma(self):
        raw = _csv('"user1@example.com","abc12345",user,"æ,å­"')
        records = uis.parse_csv(raw)
        self.assertEqual(records[0]["nickname"], "æ,å­")

    def test_parse_row_numbers_start_at_two(self):
        records = uis.parse_csv(_csv("a1@example.com,abc12345,user,A", "a2@example.com,abc12345,user,B"))
        self.assertEqual([r["row"] for r in records], [2, 3])


class ImportTests(unittest.TestCase):
    def _admin(self):
        return SimpleNamespace(id=1, account="root")

    def test_import_success_writes_audit_and_commits(self):
        db, added = _fake_db()
        admin = self._admin()
        raw = _csv("u1@example.com,abc12345,user,U1", "u2@example.com,abc12345,admin,U2")
        with mock.patch.object(uis, "create_user") as create:
            create.side_effect = [
                {"id": 101, "account": "u1@example.com"},
                {"id": 102, "account": "u2@example.com"},
            ]
            # service 以模块级绑定引用 record_admin_action：patch uis 命名空间里的那份
            with mock.patch.object(uis, "record_admin_action") as audit:
                result = _run(uis.import_users(db, admin, raw))
        self.assertEqual(result["imported"], 2)
        # create_user 与审计都已 mock：不该有任何真实 db.add（无 User 行、无审计行）
        self.assertEqual(len(added), 0)
        create.assert_awaited()
        audit.assert_called_once()
        self.assertEqual(audit.call_args.args[2], "user_batch_import")
        db.commit.assert_awaited_once()
        # 密码列绝不进审计 detail
        detail = audit.call_args.kwargs["detail"]
        self.assertNotIn("abc12345", str(detail))

    def test_validation_failure_cancels_everything(self):
        db, added = _fake_db(first_rows={"taken@example.com": SimpleNamespace(id=9, deleted_at=None)})
        admin = self._admin()
        raw = _csv(
            "u1@example.com,abc12345,user,U1",
            "taken@example.com,abc12345,user,U2",
            "u2@example.com,short,user,U3",
        )
        with mock.patch.object(uis, "create_user") as create:
            with self.assertRaises(AppException) as ctx:
                _run(uis.import_users(db, admin, raw))
        create.assert_not_awaited()
        db.commit.assert_not_awaited()
        errors = ctx.exception.data["errors"]
        self.assertEqual(len(errors), 2)  # taken（已存在）+ u2（密码太短）
        # 行号是 CSV 物理行号（表头为第 1 行）
        self.assertEqual([e["row"] for e in errors], [3, 4])

    def test_duplicate_within_file_is_rejected(self):
        db, _ = _fake_db()
        admin = self._admin()
        raw = _csv("same@example.com,abc12345,user,A", "same@example.com,abc12345,user,B")
        with self.assertRaises(AppException) as ctx:
            _run(uis.import_users(db, admin, raw))
        errors = ctx.exception.data["errors"]
        self.assertEqual(errors[0]["code"], uis.REASON_DUP_IN_FILE)

    def test_weak_password_reports_policy_message(self):
        db, _ = _fake_db()
        admin = self._admin()
        raw = _csv("u1@example.com,12345678,user,U1")
        with self.assertRaises(AppException) as ctx:
            _run(uis.import_users(db, admin, raw))
        errors = ctx.exception.data["errors"]
        self.assertEqual(errors[0]["code"], uis.REASON_POLICY_VIOLATION)
        self.assertIn("字母和数字", errors[0]["detail"])

    def test_over_limit_rejected(self):
        db, _ = _fake_db()
        admin = self._admin()
        raw = _csv(*[f"u{i}@example.com,abc12345,user,U{i}" for i in range(uis.IMPORT_MAX_ROWS + 1)])
        with self.assertRaises(AppException) as ctx:
            _run(uis.import_users(db, admin, raw))
        self.assertIn("500", ctx.exception.message)

    def test_translate_maps_codes_to_texts(self):
        translated = uis._translate([{"row": 3, "code": uis.REASON_ACCOUNT_EXISTS, "detail": "x@e.com"}])
        self.assertEqual(translated[0]["reason"], "该账号已存在")
        self.assertEqual(translated[0]["detail"], "x@e.com")


class ImportEndpointTests(unittest.TestCase):
    def test_endpoint_imports_csv(self):
        from tests.conftest import async_client
        from app.models.user import User
        from app.api import admin_users

        async def scenario():
            async with async_client(user=User(id=1, account="root", role="admin")) as client:
                with mock.patch.object(
                    admin_users,
                    "import_users",
                    new=mock.AsyncMock(return_value={"total": 1, "imported": 1, "ids": [101]}),
                ) as import_users_mock:
                    resp = await client.post(
                        "/api/v1/admin/users/import",
                        files={"file": ("users.csv", b"account,password,role,nickname\n", "text/csv")},
                    )
            return resp, import_users_mock

        resp, import_users_mock = _run(scenario())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["imported"], 1)
        import_users_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
