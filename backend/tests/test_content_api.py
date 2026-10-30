"""内容 API 命名空间守卫（`app/api/content.py`）。

`/api/v1/content/*` 是对外承诺的接口面，它的价值全在「边界清晰」：
一旦有人往这个命名空间里挂了一个写接口或管理端接口，对外文档就会失真，
而运行时不会有任何报错。所以这里用精确集合把它钉死。

覆盖四件事：
1. 端点清单**逐个比对**（不是「至少包含」）—— 多挂一个也会红；
2. 除浏览埋点外全部只读，且**不含**付费解锁与分类写接口；
3. 独立文档只列内容路径，不泄漏管理端接口；
4. 旧路径仍在（仓库外的用户端在用），且已标记 deprecated。
"""

import unittest

from fastapi.testclient import TestClient

from app.api.content import CONTENT_PREFIX
from app.main import app

# 内容命名空间的**完整**端点清单：改动这里必须是有意识的决定
EXPECTED_PATHS = {
    f"{CONTENT_PREFIX}",
    f"{CONTENT_PREFIX}/tutorials",
    f"{CONTENT_PREFIX}/tutorials/{{tutorial_id}}",
    f"{CONTENT_PREFIX}/tutorials/{{tutorial_id}}/intro",
    f"{CONTENT_PREFIX}/tutorials/{{tutorial_id}}/nodes",
    f"{CONTENT_PREFIX}/tutorials/{{tutorial_id}}/nodes/{{node_id}}",
    f"{CONTENT_PREFIX}/tutorials/{{tutorial_id}}/track",
    f"{CONTENT_PREFIX}/categories",
    f"{CONTENT_PREFIX}/categories/{{module}}",
    f"{CONTENT_PREFIX}/categories/{{module}}/overview",
    f"{CONTENT_PREFIX}/announcements",
    f"{CONTENT_PREFIX}/announcements/{{announcement_id}}",
}

# 只允许出现写方法的端点（浏览埋点）
WRITE_ALLOWED = {f"{CONTENT_PREFIX}/tutorials/{{tutorial_id}}/track"}


class ContentApiSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = app.openapi()
        cls.paths = {
            path: item
            for path, item in cls.schema["paths"].items()
            if path == CONTENT_PREFIX or path.startswith(f"{CONTENT_PREFIX}/")
        }

    def test_parsed_something(self):
        """解析不到说明命名空间没挂上，后面的断言都会变成空集比较（静默通过）"""
        self.assertTrue(self.paths, f"{CONTENT_PREFIX} 下没有任何路由，命名空间可能未挂载")

    def test_exact_path_set(self):
        self.assertEqual(
            set(self.paths),
            EXPECTED_PATHS,
            "内容 API 的端点清单变了。新增/删除端点请同步 EXPECTED_PATHS，"
            f"并确认它确实属于「对外内容接口」: 多出 {sorted(set(self.paths) - EXPECTED_PATHS)}，"
            f"缺少 {sorted(EXPECTED_PATHS - set(self.paths))}",
        )

    def test_only_get_except_view_tracking(self):
        writable = {
            path
            for path, item in self.paths.items()
            if set(item) - {"get", "parameters"}
        }
        self.assertEqual(
            writable,
            WRITE_ALLOWED,
            f"内容 API 除浏览埋点外必须只读，以下端点带了写方法: {sorted(writable - WRITE_ALLOWED)}",
        )

    def test_purchase_and_category_writes_are_not_exposed(self):
        """付费解锁属交易、分类增删改属后台操作，都不该出现在对外内容接口里"""
        forbidden = {f"{CONTENT_PREFIX}/tutorials/{{tutorial_id}}/purchase"}
        self.assertEqual(forbidden & set(self.paths), set(), "付费解锁接口不应进入内容 API")

        category_writes = {
            path
            for path, item in self.paths.items()
            if path.startswith(f"{CONTENT_PREFIX}/categories") and set(item) - {"get", "parameters"}
        }
        self.assertEqual(category_writes, set(), f"分类写接口不应进入内容 API: {sorted(category_writes)}")

    def test_admin_endpoints_do_not_leak(self):
        leaked = [path for path in self.paths if "/admin" in path]
        self.assertEqual(leaked, [], f"管理端接口泄漏到内容 API: {leaked}")

    def test_every_path_carries_content_tag(self):
        missing = [
            path
            for path, item in self.paths.items()
            for method in item.values()
            if "内容 API" not in method.get("tags", [])
        ]
        self.assertEqual(missing, [], f"以下内容接口没有打「内容 API」标签（文档里会散落）: {missing}")


class ContentApiDocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_dedicated_openapi_only_lists_content_paths(self):
        res = self.client.get(f"{CONTENT_PREFIX}/openapi.json")
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertEqual(payload["info"]["title"], "内容 API")
        leaked = [path for path in payload["paths"] if not path.startswith(CONTENT_PREFIX)]
        self.assertEqual(leaked, [], f"内容 API 文档里混入了非内容路径: {leaked}")

    def test_docs_page_points_at_dedicated_schema(self):
        res = self.client.get(f"{CONTENT_PREFIX}/docs")
        self.assertEqual(res.status_code, 200)
        self.assertIn(f"{CONTENT_PREFIX}/openapi.json", res.text)

    def test_docs_routes_are_hidden_from_schema(self):
        """文档页自己不该出现在接口清单里（否则会递归地把自己文档化）"""
        schema_paths = app.openapi()["paths"]
        self.assertNotIn(f"{CONTENT_PREFIX}/docs", schema_paths)
        self.assertNotIn(f"{CONTENT_PREFIX}/openapi.json", schema_paths)


class LegacyContentPathTests(unittest.TestCase):
    """旧路径必须还在：仓库外的用户端与小程序在用，直接删会静默打断它们"""

    @classmethod
    def setUpClass(cls):
        cls.schema = app.openapi()

    def test_legacy_paths_still_exist(self):
        for path in ("/api/v1/tutorials", "/api/v1/categories", "/api/v1/home", "/api/v1/announcements"):
            self.assertIn(path, self.schema["paths"], f"旧路径 {path} 被删了，会打断仓库外的用户端")

    def test_legacy_paths_marked_deprecated(self):
        stale = [
            path
            for path in ("/api/v1/tutorials", "/api/v1/categories", "/api/v1/home", "/api/v1/announcements")
            if not self.schema["paths"][path]["get"].get("deprecated")
        ]
        self.assertEqual(stale, [], f"旧路径未标记 deprecated（调用方不知道要迁移）: {stale}")


if __name__ == "__main__":
    unittest.main()
