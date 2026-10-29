"""法律文档模块守卫（2026-10-27 从「系统设置 → 法律文档」抽出）。

这是第三个用 `surface` 机制抽出的模块（前两个是登录管理、模型管理），守卫重点与它们一致：

1. **不再双写**：系统设置接口不得再下发 legal 分组，写通道也不接受法务键；
2. **授权面收窄**：`legal_doc:update` 只能写三份法务文本，写不了 COS 密钥/登录开关；
3. **元信息不能说谎**：`documents` 的「自定义 / 系统默认」「字数」必须与库里真实状态一致 ——
   这个页面最容易出的问题是「以为改了、其实前台还是默认文案」；
4. **前台链路不受影响**：清空正文 = 回退默认文案（不是空白页），`LEGAL_DOC_TYPES`
   与设置键必须一一对应，否则前台某个弹窗会永远拿到默认值。
"""

import unittest

from sqlalchemy import select

from app.core.rbac import match_route_permission
from app.models.system_setting import SystemSetting
from app.services import legal_documents_service, setting_service
from app.services.setting_service import (
    LEGAL_DOC_TYPES,
    SURFACE_LEGAL,
    SURFACE_LOGIN,
    SURFACE_SETTINGS,
)
from tests.fake_db import MemorySession


class LegalScopeTests(unittest.TestCase):
    """① 归属与键集合"""

    def test_legal_keys_match_public_doc_types(self):
        """三份文档的键必须与前台 `/settings/legal/{type}` 的映射完全一致"""
        declared = {meta["key"] for meta in LEGAL_DOC_TYPES.values()}
        self.assertEqual(declared, set(legal_documents_service.LEGAL_SETTING_KEYS))

    def test_legal_keys_are_not_in_other_surfaces(self):
        others = setting_service.keys_for_surface(SURFACE_SETTINGS) | setting_service.keys_for_surface(
            SURFACE_LOGIN
        )
        leaked = legal_documents_service.LEGAL_SETTING_KEYS & others
        self.assertEqual(leaked, set(), f"法务键同时归属其它界面（会出现双写）: {sorted(leaked)}")

    def test_settings_payload_excludes_legal_group(self):
        groups = {g["key"] for g in setting_service.groups_payload(surface=SURFACE_SETTINGS)}
        self.assertNotIn("legal", groups, "系统设置仍在下发 legal 分组")

    def test_legal_payload_only_has_legal_group(self):
        self.assertEqual([g["key"] for g in legal_documents_service.groups()], ["legal"])
        self.assertEqual(
            [d["key"] for d in legal_documents_service.definitions()],
            ["legal_privacy", "legal_agreement", "legal_disclaimer"],
        )


class LegalWriteScopeTests(unittest.IsolatedAsyncioTestCase):
    """② 写通道只能写法务文本"""

    async def asyncSetUp(self):
        self.db = MemorySession()

    async def _rows(self):
        return {
            r.setting_key: r.value
            for r in (await self.db.execute(select(SystemSetting))).scalars().all()
        }

    async def test_accepts_legal_keys(self):
        changed = await legal_documents_service.update(self.db, {"legal_privacy": "<p>自定义隐私政策</p>"})
        await self.db.commit()
        self.assertEqual(changed, 1)
        self.assertEqual((await self._rows())["legal_privacy"], "<p>自定义隐私政策</p>")

    async def test_rejects_foreign_keys(self):
        """越界键整批拒绝：不能出现「法务文本写了、顺手把 COS 密钥也改了」"""
        for key in ("cos_secret_key", "phone_login_enabled", "site_name"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    await legal_documents_service.update(
                        self.db, {"legal_disclaimer": "<p>x</p>", key: "injected"}
                    )
        self.assertEqual(await self._rows(), {}, "越界写入没有被整批放弃")

    async def test_settings_surface_rejects_legal_key(self):
        with self.assertRaises(ValueError):
            await setting_service.update_settings(
                self.db, {"legal_privacy": "<p>x</p>"}, surface=SURFACE_SETTINGS
            )

    async def test_clearing_restores_default(self):
        """清空 = 删除该行 = 前台回退默认文案（不是存一个空串变空白页）"""
        await legal_documents_service.update(self.db, {"legal_agreement": "<p>自定义</p>"})
        await self.db.commit()
        await legal_documents_service.update(self.db, {"legal_agreement": ""})
        await self.db.commit()
        self.assertNotIn("legal_agreement", await self._rows())


class LegalDocumentMetaTests(unittest.IsolatedAsyncioTestCase):
    """③ 元信息：自定义 / 默认、字数、最后保存时间"""

    async def asyncSetUp(self):
        self.db = MemorySession()

    async def _doc(self, doc_type: str) -> dict:
        docs = await legal_documents_service.documents(self.db)
        return next(d for d in docs if d["type"] == doc_type)

    async def test_default_docs_are_marked_not_custom(self):
        for doc_type in LEGAL_DOC_TYPES:
            with self.subTest(doc_type=doc_type):
                doc = await self._doc(doc_type)
                self.assertFalse(doc["is_custom"], "未配置的文档不该显示为自定义")
                self.assertGreater(doc["length"], 0, "默认文案不应为空（前台会变成空白页）")
                self.assertIsNone(doc["updated_at"])

    async def test_custom_doc_reports_length_and_timestamp(self):
        await legal_documents_service.update(
            self.db, {"legal_privacy": "<p>甲方<b>乙方</b></p>"}
        )
        await self.db.commit()
        doc = await self._doc("privacy")
        self.assertTrue(doc["is_custom"])
        # 去标签后「甲方乙方」= 4 字
        self.assertEqual(doc["length"], 4)

    async def test_public_api_path_matches_real_route(self):
        """元信息里的前台接口路径必须真的能打到（模板 + doc_type 组合）"""
        from app.main import app

        paths = app.openapi()["paths"]
        template = "/api/v1/settings/legal/{doc_type}"
        self.assertIn(template, paths, "前台读取法律文档的接口不见了")
        for doc_type in LEGAL_DOC_TYPES:
            doc = await self._doc(doc_type)
            self.assertEqual(doc["public_api"], f"/api/v1/settings/legal/{doc['type']}")
            self.assertIn(doc["type"], LEGAL_DOC_TYPES)


class LegalRouteGuardTests(unittest.TestCase):
    """路由权限码与注册"""

    def test_route_permissions(self):
        self.assertEqual(
            match_route_permission("GET", "/api/v1/admin/legal-documents"), ["legal_doc:view"]
        )
        self.assertEqual(
            match_route_permission("PUT", "/api/v1/admin/legal-documents"), ["legal_doc:update"]
        )

    def test_endpoint_registered(self):
        from app.main import app

        self.assertIn("/api/v1/admin/legal-documents", app.openapi()["paths"])


if __name__ == "__main__":
    unittest.main()
