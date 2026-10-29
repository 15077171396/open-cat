"""多态注册表与三端词汇表的收口守卫：评论的 target_type 词汇表散在 5 处（models.comment 常量、
comment_service._TARGET_SPECS、3 个 admin service 的 purge 调用、前端 CommentTargetType、
管理端 TARGET_TYPE_META）且刻意无外键——只改前两处即可全门禁绿，但 purge 调用漏掉的后果是
该类型评论永远删不掉（孤儿 UGC）。本文件把 5 处逐一对齐，另附 favorite_service 注册表一致性检查。
全部离线（MemorySession + FakeRedis）。"""

import ast
import re
import unittest
from pathlib import Path
from unittest import mock

from app.core.exceptions import AppException
from app.models import comment as comment_model
from app.models.node import Node, Plugin
from app.models.resource import Resource
from app.models.tutorial import Tutorial
from app.services import comment_service, favorite_service, plugin_service
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = REPO_ROOT / "backend" / "app" / "services"
API_DIR = REPO_ROOT / "backend" / "app" / "api"
FRONTEND_COMMENT_SECTION = REPO_ROOT / "frontend" / "src" / "components" / "common" / "CommentSection.tsx"
ADMIN_COMMENT_LIST = REPO_ROOT / "admin-frontend" / "src" / "pages" / "Comments" / "List.tsx"

# 内容删除侧的清理调用只可能出现在这几个模块里
PURGE_HOSTS = (
    "admin_tutorial_service.py",
    "admin_node_service.py",
    "admin_resource_service.py",
    # 扩展只可收藏不可评论，此前不在清单里；EXT-07 统一入口后它也有清理调用
    "admin_plugin_service.py",
)


class RegistryIsSingleSourceTests(unittest.TestCase):
    """①②：后端两份声明必须逐字相等（这条同时被导入期断言锁死）。"""

    def test_registry_keys_equal_model_constants(self):
        self.assertEqual(
            set(comment_service._TARGET_SPECS),
            set(comment_model.COMMENT_TARGET_TYPES),
            "注册表与 COMMENT_TARGET_TYPES 漂移：新增类型必须两边同时登记",
        )

    def test_registered_targets_helper_matches(self):
        self.assertEqual(set(comment_service.registered_comment_targets()), set(comment_model.COMMENT_TARGET_TYPES))

    def test_specs_point_at_real_models_and_columns(self):
        """注册表里写的模型与标题列必须真实存在（防「改了字符串没改 model」）。"""
        expected_model = {"tutorial": Tutorial, "node": Node, "resource": Resource}
        for target, spec in comment_service._TARGET_SPECS.items():
            self.assertIs(spec.model, expected_model[target], f"{target} 的 model 不是预期表")
            self.assertTrue(
                hasattr(spec.model, spec.title_column.name),
                f"{target} 的 title_column {spec.title_column.name} 不在 {spec.model.__name__} 上",
            )

    def test_every_registered_target_is_favoritable_with_same_model(self):
        """两套多态注册表里，同名取值必须指向同一张表。

        `favorite_service` 是第 2 套同类注册表（5 值 + 原本是 5 分支 if 链）。
        两边词汇表不完全相同（扩展可收藏但当前不可评论，workflow 只有收藏位），
        所以**不**断言集合相等，只断言交集上的语义一致 ——
        否则「收藏里的 node 是知识百科、评论里的 node 是别的表」这种理解偏差
        会静默存在很久。
        """
        shared = set(comment_service._TARGET_SPECS) & set(favorite_service._TARGET_SPECS)
        self.assertTrue(shared, "两套注册表已完全无交集，守卫需要重新设计")
        for target in shared:
            self.assertIs(
                comment_service._TARGET_SPECS[target].model,
                favorite_service._TARGET_SPECS[target].model,
                f"target_type={target!r} 在评论侧与收藏侧指向不同的表",
            )

    def test_favorite_types_derive_from_registry(self):
        """收藏的 TARGET_TYPES 只能是注册表键集合的派生值（曾经是与 if 链并列的第二份声明）。"""
        self.assertEqual(set(favorite_service.TARGET_TYPES), set(favorite_service._TARGET_SPECS))


class PurgeCallSiteGuardTests(unittest.TestCase):
    """③：每个「有用户侧记录」的内容类型都必须有删除侧清理调用。

    EXT-07 之后，删除侧的清理**不再按表逐个手写**，而是统一走
    `content_purge.purge_content_records(db, module, ids)` —— 清哪几张表由
    `core.content_types` 的能力标记决定。所以这里守卫三件事：

    1. admin service 里传的 module 必须是**注册表里的真实键**（写字面量拼错会
       静默不清理，正是孤儿 UGC 的成因）；
    2. 每个「可评论/可收藏/有学习记录」的类型都要有调用点（漏了等于该类型删不干净）；
    3. 不许再直接调用三个底层 purge 函数 —— 绕过统一入口就等于绕过能力标记，
       新增类型时又会漏。
    """

    _LEGACY_PURGE_FUNCS = (
        "purge_comments_for_targets",
        "purge_favorites_for_targets",
        "purge_learning_records_for_tutorials",
    )

    def _module_calls(self) -> list[tuple[str, ast.Call]]:
        from app.core.content_types import CONTENT_TYPES

        found: list[tuple[str, ast.Call]] = []
        for filename in PURGE_HOSTS:
            tree = ast.parse((SERVICES_DIR / filename).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id == "purge_content_records":
                        found.append((filename, node))
        self.assertTrue(found, "一个 purge_content_records 调用都没找到，守卫可能已失效")
        return found

    def test_module_argument_is_a_known_registry_key(self):
        from app.core.content_types import CONTENT_TYPES

        problems = []
        for filename, call in self._module_calls():
            arg = call.args[1] if len(call.args) > 1 else None
            if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
                problems.append(f"{filename}:{call.lineno} 的 module 参数不是字符串字面量")
                continue
            if arg.value not in CONTENT_TYPES:
                problems.append(
                    f"{filename}:{call.lineno} 传了未登记的 module {arg.value!r}"
                )
        self.assertEqual(problems, [], "删除侧 module 写错不会报错，只会静默不清理：\n" + "\n".join(problems))

    def test_every_type_with_user_records_has_a_purge_call(self):
        from app.core.content_types import CONTENT_TYPES

        need = {
            module
            for module, spec in CONTENT_TYPES.items()
            if spec.model is not None and (spec.commentable or spec.favoritable or spec.learnable)
        }
        used = {
            call.args[1].value
            for _filename, call in self._module_calls()
            if len(call.args) > 1
            and isinstance(call.args[1], ast.Constant)
            and isinstance(call.args[1].value, str)
        }
        missing = need - used
        self.assertEqual(
            sorted(missing),
            [],
            f"以下类型有用户侧记录（收藏/评论/学习），却没有任何删除侧清理调用，"
            f"删除该类内容会留下死链与孤儿 UGC：{sorted(missing)}",
        )

    def test_no_direct_low_level_purge_calls_left(self):
        hits = []
        for filename in PURGE_HOSTS:
            tree = ast.parse((SERVICES_DIR / filename).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id in self._LEGACY_PURGE_FUNCS:
                        hits.append(f"{filename}:{node.lineno} 直接调用了 {node.func.id}")
        self.assertEqual(
            hits,
            [],
            "删除侧必须走 content_purge.purge_content_records（能力标记驱动），"
            "直接调用底层 purge 会绕过注册表，新增类型时必然漏：\n" + "\n".join(hits),
        )


class CrossLanguageVocabularyTests(unittest.TestCase):
    """④⑤：三端词汇表逐字对齐（后端注册表是唯一来源）。"""

    _EXPECTED = set(comment_service.registered_comment_targets())

    def test_frontend_union_matches(self):
        text = FRONTEND_COMMENT_SECTION.read_text(encoding="utf-8")
        m = re.search(r"export type CommentTargetType\s*=\s*([^\n]+)", text)
        self.assertIsNotNone(m, "找不到前端 CommentTargetType 定义，守卫需同步调整")
        found = set(re.findall(r"'([A-Za-z_]+)'", m.group(1)))
        self.assertEqual(found, self._EXPECTED, f"用户端 union 与后端注册表不一致：{sorted(found)}")

    def test_admin_meta_keys_match(self):
        text = ADMIN_COMMENT_LIST.read_text(encoding="utf-8")
        m = re.search(r"const TARGET_TYPE_META[^{]*\{(.*?)\n\}", text, re.S)
        self.assertIsNotNone(m, "找不到管理端 TARGET_TYPE_META，守卫需同步调整")
        found = set(re.findall(r"^ {2}([A-Za-z_]+):", m.group(1), re.M))
        self.assertEqual(found, self._EXPECTED, f"管理端筛选项与后端注册表不一致：{sorted(found)}")

    def test_public_route_documents_every_target(self):
        """路由的取值说明必须由注册表派生（此前两处手写 `(tutorial/node)`，漏了 resource）。"""
        src = (API_DIR / "comments.py").read_text(encoding="utf-8")
        self.assertIn("registered_comment_targets()", src, "路由取值说明不得再手写清单")
        self.assertNotIn(
            'description="目标类型(tutorial',
            src,
            "路由里又出现了手写的 target_type 取值说明",
        )


class PurgeBehaviourTests(unittest.IsolatedAsyncioTestCase):
    """删除侧的实际行为：未知类型必须报错，而不是静默删 0 行。"""

    def setUp(self):
        self.db = MemorySession()

    async def test_unknown_target_type_raises_instead_of_silent_zero(self):
        self.db.seed(comment_model.ContentComment(id=5001, target_type="node", target_id=7, user_id=1))
        with self.assertRaises(AppException):
            await comment_service.purge_comments_for_targets(self.db, "nodes", [7])
        # 抛错前不得产生任何删除副作用（同事务内，调用方回滚即可）
        self.assertEqual(len(self.db.all_rows(comment_model.ContentComment)), 1, "校验失败时不应已经动过数据")

    async def test_valid_target_still_purges(self):
        self.db.seed(comment_model.ContentComment(id=5002, target_type="node", target_id=7, user_id=1))
        removed = await comment_service.purge_comments_for_targets(
            self.db, comment_model.COMMENT_TARGET_NODE, [7]
        )
        self.assertEqual(removed, 1)
        self.assertEqual(self.db.all_rows(comment_model.ContentComment), [])


class FeaturedShapeTests(unittest.IsolatedAsyncioTestCase):
    """公开响应里的键必须都是真实列（P1-77 的「幽灵键」）。"""

    def setUp(self):
        self.db = MemorySession()
        self.db.seed(Plugin(id=1, name="扩展1", author_name="作者"))
        patcher = mock.patch.object(plugin_service, "redis_client", FakeRedis())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_featured_plugin_keys_are_real_columns(self):
        columns = {c.name for c in Plugin.__table__.columns}
        result = await plugin_service.get_featured_plugins(self.db, limit=12)
        for item in result["items"]:
            ghost = set(item) - columns
            self.assertEqual(
                ghost,
                set(),
                f"/plugins/featured 返回了 {Plugin.__name__} 里不存在的键 {sorted(ghost)} —— "
                "前端会照着它写渲染分支，取值恒为 null 且没有任何报错",
            )

    async def test_list_plugin_keys_are_real_columns(self):
        """列表侧同一条规矩（变异验证时发现只测 featured 会漏 —— 幽灵键写进 list 照样全绿）。"""
        columns = {c.name for c in Plugin.__table__.columns}
        listed = await plugin_service.list_plugins(self.db, page=1, page_size=10)
        for item in listed["items"]:
            ghost = set(item) - columns
            self.assertEqual(ghost, set(), f"/plugins 返回了不存在的键 {sorted(ghost)}")

    async def test_featured_shape_matches_list(self):
        """featured 不得比 list 多键（P1-77 的原始症状就是 featured 多一个 list 没有的键）。"""
        featured = (await plugin_service.get_featured_plugins(self.db, limit=12))["items"][0]
        listed = (await plugin_service.list_plugins(self.db, page=1, page_size=10))["items"][0]
        self.assertEqual(
            sorted(set(featured) - set(listed)),
            [],
            f"featured 多出 list 没有的键：{sorted(set(featured) - set(listed))}",
        )


class AuthorCoverReadPathTests(unittest.IsolatedAsyncioTestCase):
    """`author_* / cover_image` 这三列在**每一条读路径**上都要出现。

    P1-77 的第二半：这三列是逐域手抄进各个 service 的（实测约 20 个读写点），
    而 `test_list_parity` 只覆盖 5 对 **list** 函数 —— 详情、精选、后台编辑页回填
    三条路径此前零守卫。P0-15 就是这条链上炸的：后台编辑页拿不到 `author_avatar_url`
    就无法回填，前端提交时把它当「用户清空」写回 NULL，全部门禁还是绿的。

    不变式取「模型有这一列 ⇒ 每条读路径都必须给这一列」，因此新增一列或新增一种
    内容时会自动纳入检查；函数名写错会被 `hasattr` 那条断言当场拦下（而不是静默跳过）。
    """

    # domain → 模型。读写函数按 `get_{domain}_detail` / `get_{domain}` 的名字约定解析，
    # 名字对不上时 helpers 里的断言会当场拦下，不会静默跳过（守卫退化成本文件最忌讳的形态）。
    MODELS = {"node": Node, "plugin": Plugin, "resource": Resource}
    IDS = {"node": 11, "plugin": 12, "resource": 13}

    def setUp(self):
        self.db = MemorySession()
        self.db.seed(Node(id=11, name="节点"), Plugin(id=12, name="扩展"), Resource(id=13, name="资源"))
        # 公开详情路径会自增浏览量 / 读缓存，必须换成 FakeRedis 才谈得上离线（CI 契约 DB_PORT=1）
        from app.services import node_service, resource_service

        for module in (node_service, plugin_service, resource_service):
            patcher = mock.patch.object(module, "redis_client", FakeRedis())
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _public_detail(self, domain: str) -> dict:
        from app.services import node_service, resource_service

        module = {"node": node_service, "plugin": plugin_service, "resource": resource_service}[domain]
        fn = getattr(module, f"get_{domain}_detail", None)
        self.assertIsNotNone(fn, f"{module.__name__} 没有 get_{domain}_detail，守卫已失效")
        return await fn(self.db, self.IDS[domain])

    async def _admin_getter(self, domain: str) -> dict:
        from app.services import admin_node_service, admin_plugin_service, admin_resource_service

        module = {
            "node": admin_node_service,
            "plugin": admin_plugin_service,
            "resource": admin_resource_service,
        }[domain]
        fn = getattr(module, f"get_{domain}", None)
        self.assertIsNotNone(fn, f"{module.__name__} 没有 get_{domain}，守卫已失效")
        return await fn(self.db, self.IDS[domain])

    async def test_trio_columns_reach_every_read_path(self):
        TRIO = ("author_name", "author_avatar_url", "cover_image")
        problems: list[str] = []
        checked = 0
        for domain, model in self.MODELS.items():
            owned = {c for c in TRIO if c in {x.name for x in model.__table__.columns}}
            self.assertTrue(owned, f"{model.__name__} 已不再持有 trio 列，本守卫需要重新设计")
            detail = await self._public_detail(domain)
            admin = await self._admin_getter(domain)
            for column in owned:
                checked += 1
                if column not in detail:
                    problems.append(f"{domain} 的公开详情缺 {column}")
                if column not in admin:
                    problems.append(f"{domain} 的后台回填（编辑页唯一数据来源）缺 {column}")
        self.assertGreaterEqual(checked, 7, f"只检查了 {checked} 项，守卫可能已失效")
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
