"""站内通知模板契约测试。

钉住三条**破了不报错、只会悄悄失去意义**的契约：

1. **模板增删与发送同权**：模板内容最终会以管理员名义群发给全站 ——
   查看（message:view）与增删（message:send）分立，套用是发送弹窗的便利功能。
2. **坏配置不阻断发通知**：模板 JSON 损坏 / 形态不对一律按空清单处理
   （发通知不需要模板也能填表单）。
3. **汇总审计**：保存写 message_template_update（模板口径最终以平台名义群发，
   与发送同级敏感），added/removed 进 detail。
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from app.config import settings
from app.core.exceptions import AppException
from app.services import message_template_service as mts


def _run(coro):
    return asyncio.run(coro)


def _fake_db(existing_value: str | None = None):
    added: list = []

    async def _call(*args, **kwargs):
        return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(value=existing_value) if existing_value is not None else None)

    return SimpleNamespace(execute=_call, add=added.append, delete=lambda *a: None,
                           commit=mock.AsyncMock(), rollback=mock.AsyncMock()), added


class ParseTests(unittest.TestCase):
    def test_parse_valid_json(self):
        raw = '[{"name":"维护公告","title":"维护通知","content":"平台将于 xx 维护"}]'
        templates = mts.parse_templates(raw)
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["name"], "维护公告")

    def test_parse_bad_json_returns_empty(self):
        """模板 JSON 损坏 → 空清单（发通知不依赖模板，不阻断）"""
        self.assertEqual(mts.parse_templates("not-json"), [])
        self.assertEqual(mts.parse_templates('{"name": "x"}'), [])  # 形态不对（不是数组）
        self.assertEqual(mts.parse_templates(None), [])

    def test_parse_skips_incomplete_items(self):
        raw = '[{"name":"只有名字","title":""}, {"name":"完整","title":"T","content":"C"}]'
        templates = mts.parse_templates(raw)
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["name"], "完整")


class SaveTests(unittest.TestCase):
    def _admin(self):
        return SimpleNamespace(id=1, account="root")

    def test_save_creates_and_audits(self):
        db, added = _fake_db(None)
        with mock.patch.object(settings, "MESSAGE_TEMPLATES", ""):
            with mock.patch("app.services.admin_audit_service.record_admin_action") as audit:
                result = _run(mts.save_with_audit(db, self._admin(), [{"name": "维护公告", "title": "T", "content": "C"}]))
        self.assertTrue(result["changed"])
        self.assertEqual(len(added), 1)
        audit.assert_called_once()
        self.assertEqual(audit.call_args.args[2], "message_template_update")
        db.commit.assert_awaited_once()

    def test_save_rejects_over_limit(self):
        templates = [{"name": f"t{i}", "title": f"T{i}", "content": "C"} for i in range(mts.MAX_TEMPLATES + 1)]
        db, _ = _fake_db(None)
        with self.assertRaises(AppException):
            _run(mts.save_with_audit(db, self._admin(), templates))

    def test_save_rejects_incomplete_item(self):
        db, _ = _fake_db(None)
        with self.assertRaises(AppException):
            _run(mts.save_with_audit(db, self._admin(), [{"name": "x", "title": "", "content": "C"}]))

    def test_save_empty_clears_row(self):
        db, added = _fake_db('[{"name":"x","title":"T","content":"C"}]')
        with mock.patch.object(settings, "MESSAGE_TEMPLATES", '[{"name":"x","title":"T","content":"C"}]'):
            with mock.patch("app.services.admin_audit_service.record_admin_action") as audit:
                result = _run(mts.save_with_audit(db, self._admin(), []))
        self.assertTrue(result["changed"])
        self.assertEqual(len(added), 0)  # 空清单 = 删除行，不新增
        audit.assert_called_once()


class TemplatesApiTests(unittest.TestCase):
    def test_get_endpoint_shape(self):
        from tests.conftest import async_client
        from app.models.user import User

        async def scenario():
            async with async_client(user=User(id=1, account="root", role="admin")) as client:
                return await client.get("/api/v1/admin/message-templates")

        resp = _run(scenario())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 0)
        self.assertIn("templates", body["data"])

    def test_put_endpoint_delegates_to_service(self):
        from tests.conftest import async_client
        from app.models.user import User

        async def scenario():
            async with async_client(user=User(id=1, account="root", role="admin")) as client:
                with mock.patch.object(
                    mts,
                    "save_with_audit",
                    new=mock.AsyncMock(
                        return_value={"changed": True, "templates": [{"name": "x", "title": "T", "content": "C"}]}
                    ),
                ) as save:
                    resp = await client.put(
                        "/api/v1/admin/message-templates",
                        json={"templates": [{"name": "x", "title": "T", "content": "C"}]},
                    )
            return resp, save

        resp, save = _run(scenario())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["changed"], True)
        save.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
