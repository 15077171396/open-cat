"""公开详情响应形状的守卫（第五轮审查 EXT-04）。

5 个详情（教程 / 百科 / 资源 / 扩展 / 工具）此前各自手写返回字典，
标题键还分 `title` / `name` 两派。收敛后公共字段由
`services/detail_shape.build_detail_base()` 统一产出，本文件钉住三件事：

1. 公共键**全部存在**（含 Tool 这种没有作者/SEO 列的类型 —— 缺键会让前端多一条类型分支）；
2. `title` 与 `name` **恒等**（`name` 是历史别名，前端仍在读，不能让它漂走）；
3. 5 个详情函数**确实调用了** `build_detail_base` —— 否则有人重新手写一份字典，
   收敛就白做了（这类回退不会报错，只会让下一次加字段又漏一个页面）。

全部离线（不连库）。
"""

import ast
import io
import os
import unittest

from app.core.content_types import CONTENT_TYPES
from app.services.detail_shape import COMMON_DETAIL_KEYS, build_detail_base

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVC = os.path.join(REPO, "app", "services")

# 详情函数 → 所在文件（5 个内容类型 + 教程的 intro/整篇两个入口）
DETAIL_FUNCS = (
    ("node_service.py", "get_node_detail", "node"),
    ("plugin_service.py", "get_plugin_detail", "plugin"),
    ("resource_service.py", "get_resource_detail", "resource"),
    ("tool_service.py", "get_tool_detail", "tool"),
    ("tutorial_service.py", "get_tutorial_intro", "tutorial"),
    ("tutorial_service.py", "get_tutorial_detail", "tutorial"),
)


class _Stub:
    """最小模型替身：只有注册表声明会用到的属性，值全部可辨识。"""

    def __init__(self, module: str):
        spec = CONTENT_TYPES[module]
        self.id = 7
        self.description = "简介"
        self.view_count = 42
        self.updated_at = None
        setattr(self, spec.title_attr, "标题")
        if spec.category_attr:
            setattr(self, spec.category_attr, "cat")
        if spec.cover_attr:
            setattr(self, spec.cover_attr, "cover.png")
        for key in ("author_name", "author_avatar_url", "seo_title", "seo_description", "seo_keywords"):
            setattr(self, key, key)


class CommonKeysTests(unittest.TestCase):
    def test_every_implemented_module_has_all_common_keys(self):
        for module, spec in CONTENT_TYPES.items():
            if spec.model is None:
                continue
            payload = build_detail_base(spec, _Stub(module))
            missing = [k for k in COMMON_DETAIL_KEYS if k not in payload]
            self.assertEqual(missing, [], f"{module} 的详情缺公共键 {missing}")

    def test_title_and_name_are_identical(self):
        """`name` 是 `title` 的历史别名，必须恒等 —— 两者漂移会让
        同一页面的标题在「卡片（读 name）」与「详情（读 title）」不一致。"""
        for module, spec in CONTENT_TYPES.items():
            if spec.model is None:
                continue
            payload = build_detail_base(spec, _Stub(module))
            self.assertEqual(payload["title"], payload["name"], f"{module} 的 title/name 不一致")
            self.assertEqual(payload["title"], "标题")

    def test_module_key_is_the_registry_key(self):
        """前端靠 `module` 判断评论目标/渲染分支，值必须是注册表键而不是模型名。"""
        for module, spec in CONTENT_TYPES.items():
            if spec.model is None:
                continue
            self.assertEqual(build_detail_base(spec, _Stub(module))["module"], module)

    def test_view_count_override_wins(self):
        """浏览量走 Redis 累加时要传「DB 值 + 待落库增量」，不能被模型旧值覆盖。"""
        spec = CONTENT_TYPES["node"]
        payload = build_detail_base(spec, _Stub("node"), view_count=99)
        self.assertEqual(payload["view_count"], 99)

    def test_missing_column_becomes_none_not_missing_key(self):
        """Tool 没有 view_count / 作者 / SEO 列：键必须存在且为 None。"""
        class _Bare:
            id = 1
            name = "工具"
            description = "d"
            category = "prompt"
            updated_at = None

        payload = build_detail_base(CONTENT_TYPES["tool"], _Bare())
        self.assertIsNone(payload["view_count"])
        self.assertIsNone(payload["author_name"])
        self.assertIsNone(payload["seo_title"])
        self.assertIsNone(payload["cover_image"])

    def test_extra_can_override_common_keys(self):
        spec = CONTENT_TYPES["node"]
        payload = build_detail_base(spec, _Stub("node"), extra={"cover_image": "processed.webp"})
        self.assertEqual(payload["cover_image"], "processed.webp")


class DetailServicesUseBaseTests(unittest.TestCase):
    """AST 守卫：详情函数必须调用 `build_detail_base`，不许再手写整份字典。"""

    def _calls_build_base(self, filename: str, func_name: str) -> bool:
        tree = ast.parse(io.open(os.path.join(SVC, filename), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                        if sub.func.id == "build_detail_base":
                            return True
        return False

    def test_all_detail_funcs_use_builder(self):
        missing = [
            f"{filename}::{func}"
            for filename, func, _module in DETAIL_FUNCS
            if not self._calls_build_base(filename, func)
        ]
        self.assertEqual(
            missing,
            [],
            "以下详情函数又手写了返回字典（应改为 build_detail_base + extra）："
            f"{missing}。手写会让「给所有详情加字段」再次变成改 N 个函数。",
        )


class LoadOnlyCoversCommonKeysTests(unittest.TestCase):
    """用了 `load_only` 的详情查询，必须把**公共字段要读的列**都加载进来。

    回归背景（2026-10-20 线上事故）：`get_tutorial_intro` 用 `load_only` 裁掉正文大字段，
    但列表里漏了 `Tutorial.updated_at`；而 `build_detail_base` 会读它 ——
    在**异步**会话里读未加载的列会抛 `MissingGreenlet`，于是**教程详情/介绍页全站 500**。

    测试为什么没抓到：单测用 `MemorySession` 假会话，不模拟 SQLAlchemy 的懒加载语义，
    所有属性都能直接读到。所以这条只能做**静态检查**：扫源码，
    凡是「既调 `load_only(` 又调 `build_detail_base(`」的函数，逐个核对列清单。
    """

    #: 公共字段会读的属性名（与 detail_shape.build_detail_base 一一对应）
    NEEDED_ATTRS = (
        "id",
        "description",
        "view_count",
        "updated_at",
        "seo_title",
        "seo_description",
        "seo_keywords",
    )

    def _check(self):
        import ast
        import glob

        svc_dir = os.path.join(REPO, "app", "services")
        problems = []
        checked = 0
        for path in glob.glob(os.path.join(svc_dir, "*.py")):
            src = io.open(path, encoding="utf-8").read()
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                    continue
                calls = [
                    n
                    for n in ast.walk(node)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                ]
                has_load_only = any(c.func.id == "load_only" for c in calls)
                uses_builder = any(c.func.id == "build_detail_base" for c in calls)
                if not (has_load_only and uses_builder):
                    continue
                checked += 1
                for call in calls:
                    if call.func.id != "load_only":
                        continue
                    loaded = {
                        a.attr for a in call.args if isinstance(a, ast.Attribute)
                    }
                    for attr in self.NEEDED_ATTRS:
                        if attr not in loaded:
                            problems.append(
                                f"{os.path.basename(path)}::{node.name} 的 load_only 漏了 `{attr}`"
                                " —— 异步上下文读它会 MissingGreenlet（整页 500）"
                            )
        return checked, problems

    def test_load_only_includes_common_columns(self):
        checked, problems = self._check()
        self.assertGreaterEqual(
            checked, 1, "没有找到「load_only + build_detail_base」的函数，守卫可能已失效"
        )
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
