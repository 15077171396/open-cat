"""内容类型注册表的守卫（第五轮审查 EXT-02）。

`core/content_types.py` 把「内容类型」从 9 处手抄清单收敛成 1 处。这类收敛最怕两件事：

1. **能力矩阵被悄悄改**（把 `tool` 的 `favoritable` 打开却没做前端入口，
   于是「收藏成功但收藏页看不到」）—— 用一张**显式**的期望表钉住，
   改能力必须同时改这张表，diff 里一眼能看见；
2. **派生出来的清单与注册表脱钩**（有人又在某个 service 里手抄一份）——
   逐项断言各清单 == 注册表派生值。

全部离线（不连库、不连 Redis）。
"""

import unittest

from app.core import content_types as registry
from app.core.content_types import CONTENT_TYPES, ContentType


class RegistryShapeTests(unittest.TestCase):
    def test_all_entries_are_content_type(self):
        for module, spec in CONTENT_TYPES.items():
            self.assertIsInstance(spec, ContentType)
            self.assertEqual(module, spec.module, f"{module} 的键与 module 字段不一致")

    def test_reserved_flag_matches_model_presence(self):
        """预留类型必须没有模型；有模型的类型不得标 reserved —— 两者混了就会
        出现「purge 时对 None 取 __tablename__」这类运行时炸裂。"""
        for module, spec in CONTENT_TYPES.items():
            if spec.reserved:
                self.assertIsNone(spec.model, f"{module} 标了 reserved 却带模型")
            else:
                self.assertIsNotNone(spec.model, f"{module} 有实体但没登记模型")

    def test_declared_columns_really_exist(self):
        """注册表里写的属性名必须真在模型上：写错字符串不报错，
        只会在「取标题 / 判断发布态 / 取分类」时静默拿到 None。"""
        for module, spec in CONTENT_TYPES.items():
            if spec.model is None:
                continue
            columns = {c.name for c in spec.model.__table__.columns}
            for attr in ("title_attr", "category_attr", "cover_attr", "published_attr"):
                value = getattr(spec, attr)
                if value is None:
                    continue
                self.assertIn(
                    value, columns,
                    f"{module}.{attr} = {value!r} 不是 {spec.model.__name__} 的列（现有：{sorted(columns)}）",
                )

    def test_every_implemented_type_has_publish_state(self):
        """0045 之后 5 类内容都有发布态。没有发布态的实体会让「前台只出已发布」
        这条口径出现例外，必须显式登记而不是漏写。"""
        for module, spec in CONTENT_TYPES.items():
            if spec.model is None:
                continue
            self.assertIsNotNone(
                spec.published_attr,
                f"{module} 没有登记 published_attr —— 若它确实没有发布态，请显式写 None 并在注册表注释里说明",
            )


class CapabilityMatrixTests(unittest.TestCase):
    """能力矩阵的**显式**快照：改任何一个能力开关都必须同步改这里。"""

    EXPECTED = {
        # module: (commentable, favoritable, learnable, reserved)
        "tutorial": (True, True, True, False),
    }

    def test_matrix_matches_expected(self):
        actual = {
            module: (spec.commentable, spec.favoritable, spec.learnable, spec.reserved)
            for module, spec in CONTENT_TYPES.items()
        }
        self.assertEqual(
            actual, self.EXPECTED,
            "内容能力矩阵变了。若这是有意的产品变更，请同步改本测试的 EXPECTED，"
            "并确认前端入口/筛选项、管理端标签、删除侧清理都已跟上；"
            "若只是重构，说明改错了。",
        )


class DerivedListsTests(unittest.TestCase):
    """派生清单必须严格等于注册表的对应子集（防止有人在别处又抄一份）。"""

    def test_comment_targets_derive_from_registry(self):
        from app.models import comment as comment_model
        from app.services import comment_service

        expected = {m for m, s in CONTENT_TYPES.items() if s.commentable}
        self.assertEqual(set(comment_service._TARGET_SPECS), expected)
        self.assertEqual(set(comment_model.COMMENT_TARGET_TYPES), expected)

    def test_favorite_targets_derive_from_registry(self):
        from app.services import favorite_service

        expected = {m for m, s in CONTENT_TYPES.items() if s.favoritable}
        self.assertEqual(set(favorite_service._TARGET_SPECS), expected)
        self.assertEqual(set(favorite_service.TARGET_TYPES), expected)

    def test_availability_models_derive_from_registry(self):
        from app.services import content_availability

        for module, model in content_availability._TYPE_MODELS.items():
            self.assertIs(
                model, registry.availability_model_for(module),
                f"{module} 的可用性判断表与注册表不一致",
            )
        # 每个实现了实体的类型都要能判断可用性（否则用户记录永远显示为可用）
        for module, spec in CONTENT_TYPES.items():
            if spec.model is not None:
                self.assertIn(module, content_availability._TYPE_MODELS)

    def test_category_modules_derive_from_registry(self):
        from app.services.category_service import MODULE_TABLE_MAP

        expected = {m for m, s in CONTENT_TYPES.items() if s.model is not None}
        self.assertEqual(set(MODULE_TABLE_MAP), expected)
        for module, (model, field) in MODULE_TABLE_MAP.items():
            self.assertIs(model, CONTENT_TYPES[module].model)
            self.assertEqual(field, CONTENT_TYPES[module].category_attr)

    def test_notify_labels_come_from_registry(self):
        from app.services.content_notify import notify_favoriters_unpublished  # noqa: F401
        import inspect

        src = inspect.getsource(__import__(
            "app.services.content_notify", fromlist=["x"]
        ))
        self.assertIn("label_for", src, "站内消息的中文名必须来自注册表，不能又手写一份映射")
        for module, spec in CONTENT_TYPES.items():
            self.assertEqual(registry.label_for(module), spec.label)

    def test_unknown_module_falls_back_instead_of_raising(self):
        """通知侧对未知类型要能降级：宁可写「内容已下架」，也不要因为类型名没登记
        而让整个下架操作 500（用户还等着收通知）。"""
        self.assertEqual(registry.label_for("no_such_type"), "内容")
        self.assertIsNone(registry.model_for("no_such_type"))
        self.assertEqual(registry.published_conditions("no_such_type"), ())


class PurgeDispatchTests(unittest.IsolatedAsyncioTestCase):
    """`content_purge.purge_content_records` 必须按能力标记分发，且不吞未知类型。"""

    async def test_unknown_module_raises(self):
        from app.services.content_purge import purge_content_records
        from tests.fake_db import MemorySession

        with self.assertRaises(KeyError):
            await purge_content_records(MemorySession(), "nodes", [1])

    async def test_empty_ids_is_noop(self):
        from app.services.content_purge import purge_content_records
        from tests.fake_db import MemorySession

        self.assertEqual(await purge_content_records(MemorySession(), "tutorial", []), {})
        self.assertEqual(await purge_content_records(MemorySession(), "tutorial", None), {})

if __name__ == "__main__":
    unittest.main()
