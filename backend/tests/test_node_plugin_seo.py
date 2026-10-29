"""知识百科 / 插件的 SEO 字段与全文检索回归（缺口台账 P1-17）。

P1-10（新建「知识库/博客」板块）经核实与现有「知识百科」（``nodes`` 模块）重复
建设，已关闭；复查「用现有 nodes 承载问题型长尾内容」是否可行时发现两个真实缺口：

1. **`nodes` / `plugins` 没有 SEO 自定义字段**——``seo_title`` / ``seo_description`` /
   ``seo_keywords`` 只有 ``resources`` 与 ``tutorials`` 有（迁移 0022 / 0023），
   而这两个模块正是 SEO 方案指定的栏目词承载页（「ComfyUI 节点」「ComfyUI 插件下载」）；
2. **`nodes` / `plugins` 未纳入全文检索**——``search.py`` 当时只定义了教程与资源两组列，
   即节点的**正文**（``content``）完全搜不到，而「显存不足怎么办」这类问题词恰恰在正文里。

本文件把这几条钉死，重点防三类回归：
- **口径漂移**：四张内容表的 SEO 列名/宽度必须一致（改一处漏一处会让某张表静默截断）；
- **清空语义**：传空串 = 清除（回落默认标题），传 None = 不改动——两者混了会让
  「保存一次就把已有 SEO 信息清空」这种事故悄悄发生；
- **检索回退**：列表接口必须真的接上全文检索，而不是继续只走 LIKE
  （改造后仍有 LIKE 兜底，所以「没接上」不会报错，只会静默少召回）。
"""

import unittest
from types import SimpleNamespace

from sqlalchemy import BigInteger, Integer, String

from app.models.node import Node, Plugin
from app.models.resource import Resource
from app.models.tutorial import Tutorial
from app.services import admin_node_service, admin_plugin_service, node_service, plugin_service

# 内容 create/update 现已写管理操作审计（第二轮增量审查 P2），需要传入操作者。
# record_admin_action 只读 ``admin.id``；管理端路由实际传的是 ORM User。
_ADMIN = SimpleNamespace(id=9001, account="admin@test", role="admin")

# 四张内容表共用的 SEO 字段口径：列名 -> 最大长度
_SEO_COLUMNS = {
    "seo_title": 200,
    "seo_description": 500,
    "seo_keywords": 500,
}
_SEO_MODELS = (Tutorial, Resource, Node, Plugin)


class _CountResult:
    def __init__(self, value: int):
        self._value = value

    def scalar(self):
        return self._value


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """最小会话桩：记录 SQL、返回预置结果。

    只在 ``count(`` 语句上返回非零，使 ``pick_keyword_condition`` 取第一个
    （即全文）候选后短路——这样「列表接口是否真的接了全文」就能通过
    captured SQL 里有没有 ``MATCH(`` 判定。
    """

    def __init__(self, row=None, count: int = 3):
        self.row = row
        self.count = count
        self.statements: list[str] = []
        self.added: list[object] = []
        self.commits = 0

    async def execute(self, stmt, *args, **kwargs):
        sql = str(stmt)
        self.statements.append(sql)
        if "count(" in sql.lower():
            return _CountResult(self.count)
        return _ScalarsResult([self.row] if self.row is not None else [])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def refresh(self, obj, *args, **kwargs):
        pass

    async def commit(self):
        self.commits += 1


class SeoColumnParityTests(unittest.TestCase):
    """四张内容表的 SEO 列必须同名同宽（防某张表被悄悄改窄）"""

    def test_every_content_model_has_seo_columns(self):
        for model in _SEO_MODELS:
            for col, max_len in _SEO_COLUMNS.items():
                self.assertIn(
                    col,
                    model.__table__.columns,
                    f"{model.__tablename__} 缺少 {col}（四张内容表口径必须一致）",
                )
                column = model.__table__.columns[col]
                self.assertIsInstance(column.type, String)
                self.assertEqual(
                    column.type.length,
                    max_len,
                    f"{model.__tablename__}.{col} 长度为 {column.type.length}，"
                    f"应为 {max_len}（与迁移 0022/0023 口径不一致）",
                )
                self.assertTrue(column.nullable, f"{model.__tablename__}.{col} 必须可空")

    def test_seo_columns_are_not_part_of_search_indexes(self):
        """SEO 字段不进全文索引：它们是给搜索引擎看的元信息，不是站内检索内容"""
        from app.services import search

        for cols in (
            search.NODE_SEARCH_COLS,
            search.PLUGIN_SEARCH_COLS,
            search.TUTORIAL_SEARCH_COLS,
            search.RESOURCE_SEARCH_COLS,
        ):
            keys = {c.key for c in cols}
            self.assertFalse(keys & set(_SEO_COLUMNS), f"全文列里混入了 SEO 字段: {keys}")


class NodeSeoWriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_node_persists_seo_fields(self):
        db = _FakeSession()
        await admin_node_service.create_node(
            db,
            {
                "name": "CheckpointLoaderSimple",
                "category": "加载器",
                "description": "加载大模型",
                "seo_title": "节点 SEO 标题",
                "seo_description": "节点 SEO 描述",
                "seo_keywords": "ComfyUI节点,加载器",
            },
            _ADMIN,
        )
        node = db.added[0]
        self.assertEqual(node.seo_title, "节点 SEO 标题")
        self.assertEqual(node.seo_description, "节点 SEO 描述")
        self.assertEqual(node.seo_keywords, "ComfyUI节点,加载器")

    async def test_create_node_normalizes_blank_to_null(self):
        """空串必须归一为 NULL：NULL = 未填写（前台回落默认标题），空串会让前端误判"""
        db = _FakeSession()
        await admin_node_service.create_node(
            db,
            {
                "name": "n",
                "category": "c",
                "description": "d",
                "seo_title": "",
                "seo_description": "",
                "seo_keywords": "",
            },
            _ADMIN,
        )
        node = db.added[0]
        self.assertIsNone(node.seo_title)
        self.assertIsNone(node.seo_description)
        self.assertIsNone(node.seo_keywords)

    async def test_update_node_sets_and_clears(self):
        node = Node(id=1, name="n", category="c", description="d")
        db = _FakeSession(row=node)

        await admin_node_service.update_node(
            db, 1, {"seo_title": "新标题", "seo_description": "新描述", "seo_keywords": "k"}, _ADMIN
        )
        self.assertEqual(node.seo_title, "新标题")
        self.assertEqual(node.seo_description, "新描述")
        self.assertEqual(node.seo_keywords, "k")

        # 传空串 = 清除（回落默认标题）
        await admin_node_service.update_node(db, 1, {"seo_title": ""}, _ADMIN)
        self.assertIsNone(node.seo_title)

    async def test_update_node_ignores_absent_keys(self):
        """键缺失/值为 None = 不改动：否则「只改正文」的保存会把已有 SEO 信息清空"""
        node = Node(
            id=1, name="n", category="c", description="d",
            seo_title="保留我", seo_description="保留我", seo_keywords="保留我",
        )
        db = _FakeSession(row=node)

        await admin_node_service.update_node(db, 1, {"name": "改名"}, _ADMIN)

        self.assertEqual(node.name, "改名")
        self.assertEqual(node.seo_title, "保留我")
        self.assertEqual(node.seo_description, "保留我")
        self.assertEqual(node.seo_keywords, "保留我")

    async def test_get_node_returns_seo_fields(self):
        node = Node(
            id=7, name="n", category="c", description="d",
            seo_title="T", seo_description="D", seo_keywords="K",
        )
        data = await admin_node_service.get_node(_FakeSession(row=node), 7)
        self.assertEqual(data["seo_title"], "T")
        self.assertEqual(data["seo_description"], "D")
        self.assertEqual(data["seo_keywords"], "K")


class PluginSeoWriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_plugin_persists_seo_fields(self):
        db = _FakeSession()
        await admin_plugin_service.create_plugin(
            db,
            {
                "name": "ComfyUI-Manager",
                "category": "comfyui",
                "platform": "ComfyUI",
                "description": "依赖管理",
                "seo_title": "插件 SEO 标题",
                "seo_description": "插件 SEO 描述",
                "seo_keywords": "ComfyUI插件下载",
            },
            _ADMIN,
        )
        plugin = db.added[0]
        self.assertEqual(plugin.seo_title, "插件 SEO 标题")
        self.assertEqual(plugin.seo_description, "插件 SEO 描述")
        self.assertEqual(plugin.seo_keywords, "ComfyUI插件下载")

    async def test_create_plugin_normalizes_blank_to_null(self):
        db = _FakeSession()
        await admin_plugin_service.create_plugin(
            db,
            {
                "name": "p",
                "category": "comfyui",
                "platform": "ComfyUI",
                "description": "d",
                "seo_title": "",
                "seo_description": "",
                "seo_keywords": "",
            },
            _ADMIN,
        )
        plugin = db.added[0]
        self.assertIsNone(plugin.seo_title)
        self.assertIsNone(plugin.seo_description)
        self.assertIsNone(plugin.seo_keywords)

    async def test_update_plugin_sets_and_clears(self):
        plugin = Plugin(id=1, name="p", category="comfyui", description="d")
        db = _FakeSession(row=plugin)

        await admin_plugin_service.update_plugin(db, 1, {"seo_title": "T", "seo_keywords": "K"}, _ADMIN)
        self.assertEqual(plugin.seo_title, "T")
        self.assertEqual(plugin.seo_keywords, "K")

        await admin_plugin_service.update_plugin(db, 1, {"seo_description": ""}, _ADMIN)
        self.assertIsNone(plugin.seo_description)

    async def test_update_plugin_ignores_absent_keys(self):
        plugin = Plugin(
            id=1, name="p", category="comfyui", description="d",
            seo_title="保留我",
        )
        db = _FakeSession(row=plugin)
        await admin_plugin_service.update_plugin(db, 1, {"name": "改名"}, _ADMIN)
        self.assertEqual(plugin.name, "改名")
        self.assertEqual(plugin.seo_title, "保留我")

    async def test_get_plugin_returns_seo_fields(self):
        plugin = Plugin(
            id=9, name="p", category="comfyui", description="d",
            seo_title="T", seo_description="D", seo_keywords="K",
        )
        data = await admin_plugin_service.get_plugin(_FakeSession(row=plugin), 9)
        self.assertEqual((data["seo_title"], data["seo_description"], data["seo_keywords"]),
                         ("T", "D", "K"))


class NodePluginSearchWiringTests(unittest.IsolatedAsyncioTestCase):
    """列表接口必须真的接上全文检索（没接上不会报错，只会静默少召回）"""

    async def test_node_list_uses_fulltext_match(self):
        from app.services import search

        search.reset_fulltext_availability()
        db = _FakeSession(count=3)
        result = await node_service.list_nodes(db, keyword="安装教程")

        self.assertEqual(result["total"], 3)
        self.assertTrue(
            any("MATCH(" in sql for sql in db.statements),
            "节点列表未使用全文检索（改造后 LIKE 仍能兜底，所以这条只能靠 SQL 断言守住）",
        )

    async def test_plugin_list_uses_fulltext_match(self):
        from app.services import search

        search.reset_fulltext_availability()
        db = _FakeSession(count=2)
        result = await plugin_service.list_plugins(db, keyword="安装教程")

        self.assertEqual(result["total"], 2)
        self.assertTrue(any("MATCH(" in sql for sql in db.statements))

    async def test_blank_keyword_still_applies_like_condition(self):
        """纯空格关键词必须仍然带上 LIKE 条件，而不是被静默丢弃。

        若候选返回空列表，调用方会把 None 拼进 where()（生成 `AND NULL` → 静默 0 行），
        或者干脆不加条件（关键词被无声忽略）。两条都属于「不报错但结果不对」，
        这里用「SQL 里必须有 LIKE」把它钉住。
        """
        from app.services import search

        search.reset_fulltext_availability()
        db = _FakeSession(count=5)
        result = await node_service.list_nodes(db, keyword="   ")

        self.assertEqual(result["total"], 5)
        self.assertTrue(
            any("LIKE" in sql.upper() for sql in db.statements),
            "纯空格关键词被静默丢弃了（应退回 LIKE 而不是不加任何条件）",
        )

    async def test_node_detail_exposes_seo_fields(self):
        node = Node(
            id=3, name="n", category="c", description="d",
            seo_title="T", seo_description="D", seo_keywords="K",
        )
        data = await node_service.get_node_detail(_FakeSession(row=node), 3)
        self.assertEqual((data["seo_title"], data["seo_description"], data["seo_keywords"]),
                         ("T", "D", "K"))

    async def test_plugin_detail_exposes_seo_fields(self):
        plugin = Plugin(
            id=4, name="p", category="comfyui", description="d",
            seo_title="T", seo_description="D", seo_keywords="K",
        )
        data = await plugin_service.get_plugin_detail(_FakeSession(row=plugin), 4)
        self.assertEqual((data["seo_title"], data["seo_description"], data["seo_keywords"]),
                         ("T", "D", "K"))


class SeoModelColumnTypeTests(unittest.TestCase):
    """冒烟：新增列不应破坏主键/自增口径（Node/Plugin 均为 generate_id 主键）"""

    def test_primary_keys_unchanged(self):
        for model in (Node, Plugin):
            pk = list(model.__table__.primary_key.columns)[0]
            self.assertIsInstance(pk.type, BigInteger)
            self.assertFalse(pk.autoincrement)

    def test_view_count_still_integer(self):
        for model in (Node, Plugin):
            self.assertIsInstance(model.__table__.columns["view_count"].type, Integer)


if __name__ == "__main__":
    unittest.main()
