"""扩展「已删除字段」不得回归（2026-10-12）。

背景（用户需求）：
    扩展模块决定只保留「预览量」一个指标。平台 / 版本 / 评分 / 安装次数四列已随
    迁移 `20261012_0040_drop_plugin_legacy_fields` **物理删除**（连同两个索引），
    前端、管理端、小程序三端同步移除。

为什么值得单独守卫：
    这四个字段曾经以「后台能填但前台不显示」「排序按用户看不见的指标」
    「删了表单字段却忘了后端仍是必填（保存才 422）」三种形式残留了很久，
    最后靠一次**破坏性迁移**才清干净。本文件钉住它们不会再回来：
1. 模型层不得再出现这四列（且 `view_count` 仍在，避免断言空洞）；
2. 创建请求不再接收它们 —— `model_dump()` 里不能出现，否则 service 可能写库；
3. 扩展列表的 `sort`：`hottest` / `rating` 作为**历史别名**仍可请求（映射到 `view_count`，
   为的是不打死无法强制更新的小程序旧包 —— 第五轮审查 R5-04），但响应里**绝不能再出现**
   这四个已删字段；其它非白名单取值仍被路由 pattern 422 拦下（均经真实路由验证）。
"""

import unittest
from contextlib import asynccontextmanager
from unittest import mock

import httpx

from app.api.admin_plugins import PluginCreateRequest
from app.models.node import Plugin
from app.models.user import User
from app.services import admin_plugin_service
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession

ADMIN = User(id=9001, account="admin@test")

# 已删除的四个字段 + 曾经只为它们存在的两个索引
LEGACY_COLUMNS = {"platform", "version", "rating", "install_count"}
LEGACY_INDEXES = {"ix_plugins_rating_install", "ix_plugins_install_count"}


class PluginModelCleanupTests(unittest.TestCase):
    def test_model_has_no_legacy_columns(self):
        cols = {c.name for c in Plugin.__table__.columns}
        self.assertEqual(cols & LEGACY_COLUMNS, set(), "这四个字段已删除，不得回归")
        self.assertIn("view_count", cols, "预览量必须保留（扩展唯一保留的指标）")
        self.assertIn("author_name", cols)

    def test_no_index_references_legacy_columns(self):
        names = {i.name for i in Plugin.__table__.indexes}
        self.assertEqual(names & LEGACY_INDEXES, set(), "为已删列建的索引必须一起删掉")
        indexed_cols = {c.name for i in Plugin.__table__.indexes for c in i.columns}
        self.assertEqual(indexed_cols & LEGACY_COLUMNS, set(), "不得有索引引用已删列")


class SeedScriptColumnGuardTests(unittest.TestCase):
    """`seed_more.py` 传给 ORM 构造器的键必须都在模型列里（第五轮审查 R5-13）。

    为什么钉这条：迁移删列后，`seed_more.py` 的八条扩展字典仍在传
    `platform/version/rating/install_count`，而 SQLAlchemy 的声明式构造器对未映射键
    **直接抛 TypeError**（不是静默忽略）—— 照 `backend/README.md` 与部署说明书建库的人
    会在第 4 节中断，留下一套「有教程/节点、无扩展」的脏库，且报错信息
    （`'platform' is an invalid keyword argument for Plugin`）完全看不出是删列导致。
    删列是一次性的，种子脚本却是长期会被照抄的文档路径，所以需要一条静态守卫。
    """

    def test_seed_plugin_keys_are_all_mapped_columns(self):
        import ast
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "seed_more.py").read_text(encoding="utf-8")
        payload_keys = {"sources"}  # 关系表单独处理，不是 Plugin 的列
        blocks = []
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "plugins_data" for t in node.targets
            ):
                blocks.append(ast.literal_eval(node.value))
        self.assertTrue(blocks, "没在 seed_more.py 里找到 plugins_data（脚本被重构过？）")

        cols = {c.key for c in Plugin.__table__.columns}
        rows = [r for block in blocks for r in block]
        self.assertGreaterEqual(len(rows), 1, "plugins_data 为空，本用例会假绿")
        for i, row in enumerate(rows):
            keys = set(row) - payload_keys
            self.assertTrue(keys <= cols, f"seed_more 第 {i} 条扩展传了未映射键：{sorted(keys - cols)}")


class PluginCreateRequestCleanupTests(unittest.TestCase):
    def test_create_request_drops_legacy_fields(self):
        """多传这些字段不应被 pydantic 收下（否则 service 可能照着写库）"""
        req = PluginCreateRequest(
            name="扩展A", category="comfyui", description="简介",
            platform="Coze", version="1.0.0", rating=4.5, install_count=10,
        )
        dumped = req.model_dump()
        self.assertEqual(set(dumped) & LEGACY_COLUMNS, set(), f"仍在下发：{sorted(set(dumped) & LEGACY_COLUMNS)}")
        # 核心字段仍在（否则本用例可能因「模型被清空」而假绿）
        self.assertEqual(dumped["name"], "扩展A")
        self.assertEqual(dumped["category"], "comfyui")


class PluginCreateServiceCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.object(admin_plugin_service, "invalidate_plugins", mock.AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_create_ignores_legacy_fields_in_payload(self):
        """即使调用方硬塞这些键，也不会写进库（列已不存在）"""
        db = MemorySession()
        await admin_plugin_service.create_plugin(
            db,
            {
                "name": "扩展B", "category": "coze", "description": "简介",
                # 模拟旧客户端/脚本残留的字段
                "platform": "Coze", "version": "1.0.0", "rating": 4.5, "install_count": 10,
            },
            ADMIN,
        )
        row = db.all_rows(Plugin)[0]
        self.assertEqual(set(row) & LEGACY_COLUMNS, set(), f"写进了已删列：{sorted(set(row) & LEGACY_COLUMNS)}")
        self.assertEqual(row["name"], "扩展B")


class PluginSortWhitelistTests(unittest.IsolatedAsyncioTestCase):
    """sort 白名单经**真实路由**验证：旧取值必须被拒，而不是静默换一种排序"""

    def setUp(self):
        self.db = MemorySession()
        from app.core import rate_limit as _rate_limit

        patcher = mock.patch.object(_rate_limit, "redis_client", FakeRedis())
        patcher.start()
        self.addCleanup(patcher.stop)

    @asynccontextmanager
    async def _client(self):
        from app.database import get_db
        from app.main import app

        async def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test", timeout=10.0
            ) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_db, None)

    async def test_legacy_sort_values_are_aliased_not_rejected(self):
        """历史取值必须**仍然可用**（映射到 view_count），不能 422（第五轮审查 R5-04）。

        口径变更说明：本用例原先钉的是「sort=hottest/rating 必须 422」。改掉它是**有意的
        产品决策变更**，不是为了让测试变绿：这两个取值仍会从**已发布、无法强制更新**的
        小程序包发来（旧包何时被替换由微信审核节奏决定），422 会让旧用户一点「最多使用」
        就看到空列表，而后端没有任何日志能提示这是版本错配。同一批改动在评论侧为
        `tutorial_id` 保留了兼容参数，两处口径必须一致。
        摘除本分支的判据写在 `api/plugins.py` 注释里（`event=plugin_legacy_sort` 归零）。
        """
        for legacy in ("hottest", "rating"):
            with self.subTest(sort=legacy):
                async with self._client() as client:
                    resp = await client.get("/api/v1/plugins", params={"sort": legacy})
                self.assertEqual(
                    resp.status_code, 200,
                    f"sort={legacy} 是历史取值：必须映射到 view_count 正常返回，不能 422 打死旧客户端",
                )
                data = resp.json()["data"]
                self.assertIn("items", data)
                # 别名之后不得复活已删字段（否则「兼容」就变成了「回滚」）
                for item in data["items"]:
                    self.assertEqual(
                        set(item) & LEGACY_COLUMNS, set(),
                        f"响应里又出现了已删字段：{sorted(set(item) & LEGACY_COLUMNS)}",
                    )
                    self.assertIn("view_count", item)

    async def test_unknown_sort_value_still_rejected(self):
        """兼容的只是两个已知历史取值，不是放开白名单：其它字符串仍须 422"""
        async with self._client() as client:
            resp = await client.get("/api/v1/plugins", params={"sort": "whatever"})
        self.assertEqual(resp.status_code, 422, "非白名单取值必须被路由 pattern 拦下")

    async def test_remaining_sort_values_accepted(self):
        for ok_sort in ("comprehensive", "latest"):
            with self.subTest(sort=ok_sort):
                async with self._client() as client:
                    resp = await client.get("/api/v1/plugins", params={"sort": ok_sort})
                self.assertEqual(resp.status_code, 200, resp.text[:300])


if __name__ == "__main__":
    unittest.main()
