"""敏感词库管理契约测试。

钉住三条**破了不报错、只会悄悄失去意义**的契约：

1. **保存后必须热生效**：save_words 落行 → 调用方 commit → apply_after_save
   失效缓存并重新应用（reload_words 由 runtime_config 自动触发）；
   链路断了的现象是「界面显示保存成功，UGC 拦截却还在用旧词库」。
2. **词表规整统一收口**：去空白、去重（大小写不敏感）、截断、上限 500 ——
   界面与批量导入都过 normalize_words，不各自实现。
3. **命中测试走当前生效词库**：scan() 读的是热重载后的匹配器，测试端点
   必须与真实审核路径同源。
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from app.config import settings
from app.core.exceptions import AppException
from app.services import moderation_service, moderation_words_service as mws


def _run(coro):
    return asyncio.run(coro)


def _fake_db(existing_row=None):
    """scalar_one_or_none 返回既有的设置行；记录 add/delete 调用"""
    added: list = []
    deleted: list = []

    async def _call(*args, **kwargs):
        return SimpleNamespace(scalar_one_or_none=lambda: existing_row)

    return (
        SimpleNamespace(execute=_call, add=added.append, delete=deleted.append),
        added,
        deleted,
    )


class ParseTests(unittest.TestCase):
    def test_parse_words_splits_dedups_and_keeps_order(self):
        self.assertEqual(mws.parse_words("加微信, 加QQ\n加微信,,代开发票"), ["加微信", "加QQ", "代开发票"])

    def test_parse_words_empty_returns_empty(self):
        self.assertEqual(mws.parse_words(None), [])
        self.assertEqual(mws.parse_words(""), [])
        self.assertEqual(mws.parse_words("  ,,, "), [])


class NormalizeTests(unittest.TestCase):
    def test_normalize_strips_dedups_case_insensitive(self):
        words = mws.normalize_words([" 加微信 ", "加微信", "GAMBLE", "gamble", ""])
        self.assertEqual(words, ["加微信", "GAMBLE"])

    def test_normalize_truncates_long_words(self):
        words = mws.normalize_words(["x" * 100])
        self.assertEqual(len(words[0]), mws.WORD_MAX_LEN)

    def test_normalize_rejects_over_limit(self):
        with self.assertRaises(AppException):
            mws.normalize_words([f"词{i}" for i in range(mws.MAX_WORDS + 1)])


class ListWordsTests(unittest.TestCase):
    def test_list_words_groups_builtin_and_extra(self):
        with mock.patch.object(settings, "MODERATION_EXTRA_WORDS", "测试词A,测试词B"):
            data = mws.list_words()
        self.assertIn("加微信", data["builtin_words"])
        self.assertEqual(data["extra_words"], ["测试词A", "测试词B"])
        self.assertEqual(data["max_words"], mws.MAX_WORDS)


class SaveWordsTests(unittest.TestCase):
    def test_save_creates_row_when_missing(self):
        db, added, deleted = _fake_db(existing_row=None)
        result = _run(mws.save_words(db, ["新词1", "新词2"]))
        self.assertTrue(result["changed"])
        self.assertEqual(len(added), 1)
        self.assertEqual(deleted, [])
        self.assertIn("新词1", added[0].value)

    def test_save_updates_row_when_changed(self):
        row = SimpleNamespace(setting_key=mws.WORDS_SETTING_KEY, value="旧词")
        db, added, deleted = _fake_db(existing_row=row)
        result = _run(mws.save_words(db, ["新词"]))
        self.assertTrue(result["changed"])
        self.assertEqual(row.value, "新词")
        self.assertEqual(result["removed"], ["旧词"])
        self.assertEqual(result["added"], ["新词"])

    def test_save_empty_words_deletes_row(self):
        row = SimpleNamespace(setting_key=mws.WORDS_SETTING_KEY, value="旧词")
        db, added, deleted = _fake_db(existing_row=row)
        result = _run(mws.save_words(db, []))
        self.assertTrue(result["changed"])
        self.assertEqual(len(deleted), 1)
        self.assertEqual(added, [])

    def test_save_no_change_skips_write(self):
        with mock.patch.object(settings, "MODERATION_EXTRA_WORDS", "旧词"):
            row = SimpleNamespace(setting_key=mws.WORDS_SETTING_KEY, value="旧词")
            db, added, deleted = _fake_db(existing_row=row)
            result = _run(mws.save_words(db, ["旧词"]))
        self.assertFalse(result["changed"])
        self.assertEqual(added, [])
        self.assertEqual(deleted, [])

    def test_apply_after_save_invalidates_and_reapplies(self):
        with mock.patch(
            "app.services.runtime_config.invalidate_runtime_settings_cache", new=mock.AsyncMock()
        ) as invalidate:
            with mock.patch(
                "app.services.runtime_config.apply_runtime_settings", new=mock.AsyncMock()
            ) as apply_settings:
                _run(mws.apply_after_save())
        invalidate.assert_awaited_once()
        apply_settings.assert_awaited_once()


class ScanTests(unittest.TestCase):
    def test_scan_hits_custom_word_after_reload(self):
        """自定义词保存链路的端到端：写配置 → 重载匹配器 → scan 命中"""
        with mock.patch.object(settings, "MODERATION_EXTRA_WORDS", "神秘测试词XYZ"):
            try:
                moderation_service.reload_words()
                hits = moderation_service.scan("这是一段包含神秘测试词XYZ的文本")
            finally:
                moderation_service.reload_words()  # 恢复默认词库，避免污染其他用例
        self.assertIn("神秘测试词XYZ", hits)


class WordsApiTests(unittest.TestCase):
    def test_get_words_endpoint(self):
        from tests.conftest import async_client
        from app.main import app  # noqa: F401  async_client 内部引用
        from app.models.user import User

        async def scenario():
            async with async_client(user=User(id=1, account="root", role="admin")) as client:
                return await client.get("/api/v1/admin/moderation/words")

        resp = _run(scenario())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 0)
        self.assertIn("builtin_words", body["data"])
        self.assertIn("extra_words", body["data"])

    def test_put_words_endpoint_audits_and_applies(self):
        from tests.conftest import async_client
        from app.models.user import User

        async def scenario():
            async with async_client(user=User(id=1, account="root", role="admin")) as client:
                # 事务边界已下沉到 service（save_words_with_audit），API 层零手工 commit
                with mock.patch.object(
                    mws,
                    "save_words_with_audit",
                    new=mock.AsyncMock(
                        return_value={
                            "changed": True,
                            "words": ["新词"],
                            "added": ["新词"],
                            "removed": [],
                            "committed": True,
                        }
                    ),
                ) as save:
                    resp = await client.put(
                        "/api/v1/admin/moderation/words", json={"extra_words": ["新词"]}
                    )
            return resp, save

        resp, save = _run(scenario())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["changed"], True)
        save.assert_awaited_once()

    def test_test_endpoint_uses_live_matcher(self):
        from tests.conftest import async_client
        from app.models.user import User

        async def scenario():
            async with async_client(user=User(id=1, account="root", role="admin")) as client:
                return await client.post(
                    "/api/v1/admin/moderation/words/test", json={"text": "加微信送福利"}
                )

        resp = _run(scenario())
        self.assertEqual(resp.status_code, 200)
        # 内置兜底词始终生效（加微信 在 DEFAULT_WORDS 中）
        self.assertIn("加微信", resp.json()["data"]["hits"])


if __name__ == "__main__":
    unittest.main()
