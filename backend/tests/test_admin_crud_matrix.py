"""后台增删改的「横切动作矩阵」守卫（第五轮审查 EXT-03）。

## 为什么不是「抽一个公共基类」

EXT-03 的原结论是「5 份 admin service 有 92% 是同一骨架」。回读代码后发现这个数字偏高：

- `admin_node_service` / `admin_plugin_service` / `admin_resource_service` 确实高度同构
  （加载 → 逐字段赋值 → 补作者 → 发布态通知 → 审计 → 提交 → 失效缓存）；
- `admin_tool_service` 是**合理地不一样**：工具没有作者列、不可收藏、没有富文本，
  所以它不调 `fill_author_if_absent` / `notify_favoriters_unpublished` / `sanitize_content`；
- `admin_tutorial_service` 也没有作者列（`Tutorial` 表根本没有 author_* 字段），
  但多了章节、购买记录、批量发布，是另一套流程。

硬抽一个基类只会把「三个真同构」的收益，换成「五个都要传一堆钩子」的成本，
而且审计摘要文案（`编辑节点《X》`）会被统一改写 —— 这类「读起来才知道对不对」的
字符串没有任何测试断言。

## 那真正的风险是什么

不是「代码重复」，而是**新增第 6 类内容时漏掉某个横切动作**：
- 漏 `notify_favoriters_unpublished` → 下架不通知收藏者（用户以为内容消失了）；
- 漏 `fill_author_if_absent` → 后台新建的内容永远没有作者（前台不显示作者行）；
- 漏 `sanitize_content` → 富文本里的 `<script>` 原样入库，前台渲染时靠 DOMPurify 兜；
- 漏 `record_admin_action` → 这次改动在审计里查不到；
- 漏 `purge_content_records` → 删完留下死链收藏与孤儿评论。

这些都能**从注册表和模型列推导出期望**，所以本文件用「推导期望 + 静态检查调用」把它们钉住。
新增内容类型时，忘调哪个动作都会红，且提示语直接说明后果。

全部离线（只读源码 + 注册表）。
"""

import ast
import io
import os
import unittest

from app.core.content_types import CONTENT_TYPES

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVC = os.path.join(REPO, "app", "services")

# 模块 → 后台服务文件（新增内容类型时必须补一行，否则本守卫会直接报「没有服务文件」）
ADMIN_FILES = {
    "tutorial": "admin_tutorial_service.py",
    "node": "admin_node_service.py",
    "resource": "admin_resource_service.py",
    "plugin": "admin_plugin_service.py",
    "tool": "admin_tool_service.py",
}


def _is_updates_get_call(node: ast.Call) -> bool:
    """`updates.get(...)` 形态的调用"""
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "get"
        and isinstance(func.value, ast.Name)
        and func.value.id == "updates"
    )


def _is_publish_none_guard(test: ast.expr) -> bool:
    """`updates.get("is_published") is not None`"""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    if not isinstance(test.ops[0], ast.IsNot):
        return False
    left = test.left
    if not (isinstance(left, ast.Call) and _is_updates_get_call(left)):
        return False
    if not left.args or not isinstance(left.args[0], ast.Constant):
        return False
    return left.args[0].value == "is_published"


def _is_updates_get(node: ast.Subscript) -> bool:
    """`updates["is_published"]` 形态的下标"""
    return (
        isinstance(node.value, ast.Name)
        and node.value.id == "updates"
        and isinstance(node.slice, ast.Constant)
    )


def _src(module: str) -> str:
    filename = ADMIN_FILES[module]
    return io.open(os.path.join(SVC, filename), encoding="utf-8").read()


def _columns(module: str) -> set[str]:
    spec = CONTENT_TYPES[module]
    if spec.model is None:
        return set()
    return {c.name for c in spec.model.__table__.columns}


class ExpectationTests(unittest.TestCase):
    """先证明「期望」本身是推导出来的，而不是手抄的。"""

    def test_every_implemented_type_has_admin_service(self):
        missing = [
            m for m, s in CONTENT_TYPES.items()
            if s.model is not None and m not in ADMIN_FILES
        ]
        self.assertEqual(
            missing, [],
            f"以下内容类型没有对应的 admin service（或没登记进 ADMIN_FILES）：{missing}",
        )

    def test_derived_matrix_is_not_empty(self):
        """三个期望集合都不能为空，否则下面的守卫会静默空转。"""
        fav = {m for m, s in CONTENT_TYPES.items() if s.favoritable and s.model is not None}
        author = {m for m in CONTENT_TYPES if "author_name" in _columns(m)}
        content = {m for m in CONTENT_TYPES if "content" in _columns(m)}
        self.assertTrue(fav, "没有可收藏类型，notify 守卫失效")
        self.assertTrue(author, "没有带作者列的类型，author 守卫失效")
        self.assertTrue(content, "没有富文本类型，sanitize 守卫失效")


class CrossCuttingMatrixTests(unittest.TestCase):
    """按注册表/模型列推导「应该调什么」，再逐服务静态检查。"""

    def test_favoritable_types_notify_on_unpublish(self):
        """可收藏的内容下架时必须通知收藏者。

        只对**有收藏能力**的类型要求：工具不可收藏，强行要求它调通知是无意义的。
        """
        expected = {
            m for m, s in CONTENT_TYPES.items()
            if s.favoritable and s.model is not None
        }
        missing = [m for m in expected if "notify_favoriters_unpublished" not in _src(m)]
        self.assertEqual(
            sorted(missing), [],
            f"以下类型的 admin service 从不调用 notify_favoriters_unpublished："
            f"{sorted(missing)}。它们可被收藏，下架后收藏者不会收到任何通知。",
        )

    def test_types_with_author_column_fill_author(self):
        expected = {m for m in CONTENT_TYPES if "author_name" in _columns(m)}
        missing = [m for m in expected if "fill_author_if_absent" not in _src(m)]
        self.assertEqual(
            sorted(missing), [],
            f"以下类型的模型有 author_name 列，但 admin service 从不调用 "
            f"fill_author_if_absent：{sorted(missing)}。后台新建的内容会永远没有作者，"
            "前台也就永远不渲染作者行。",
        )

    def test_types_with_rich_text_sanitize_content(self):
        expected = {m for m in CONTENT_TYPES if "content" in _columns(m)}
        missing = [m for m in expected if "sanitize_content" not in _src(m)]
        self.assertEqual(
            sorted(missing), [],
            f"以下类型有 content 富文本列，但 admin service 从不调用 sanitize_content："
            f"{sorted(missing)}。脏 HTML 会原样入库。",
        )

    def test_every_service_audits_and_purges(self):
        """每个后台服务都必须：写审计 + 删除时清理用户侧记录。"""
        problems = []
        for module in ADMIN_FILES:
            src = _src(module)
            if "record_admin_action" not in src:
                problems.append(f"{ADMIN_FILES[module]} 从不写管理审计")
            spec = CONTENT_TYPES[module]
            needs_purge = spec.commentable or spec.favoritable or spec.learnable
            if needs_purge and "purge_content_records" not in src:
                problems.append(
                    f"{ADMIN_FILES[module]} 删除时不调用 purge_content_records —— "
                    "会留下死链收藏 / 孤儿评论 / 打不开的学习记录"
                )
        self.assertEqual(problems, [], "\n".join(problems))

    def _update_func_ast(self, module: str):
        """取出 `update_*` 函数的 AST（只检查更新流程，避免误伤列表筛选参数）。"""
        src = _src(module)
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name.startswith(
                "update_"
            ):
                return node
        self.fail(f"{ADMIN_FILES[module]} 里找不到 update_* 函数")

    def test_publish_state_write_is_inside_a_none_guard(self):
        """写 `is_published` 必须落在 `if updates.get("is_published") is not None:` 里。

        这是最贵的一类 bug：编辑标题时请求没带 `is_published`，内容被静默下架 ——
        前台消失、收藏者收到下架通知，而操作者以为只是改了标题。

        用 AST 判「赋值语句的行号是否落在守卫块内」，而不是子串匹配：
        `node.is_published = updates["is_published"]` 本身长得就像反模式，
        只看文本会把它（其实被守卫包着）判成 bug。
        """
        problems = []
        for module in ADMIN_FILES:
            func = self._update_func_ast(module)
            guards: list[tuple[int, int]] = []
            assigns: list[int] = []
            for node in ast.walk(func):
                if isinstance(node, ast.If) and _is_publish_none_guard(node.test):
                    guards.append((node.lineno, node.end_lineno or node.lineno))
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and target.attr == "is_published"
                            and isinstance(node.value, ast.Subscript)
                            and _is_updates_get(node.value)
                        ):
                            assigns.append(node.lineno)
            for lineno in assigns:
                if not any(lo <= lineno <= hi for lo, hi in guards):
                    problems.append(
                        f"{ADMIN_FILES[module]}:{lineno} 写 is_published 时没有 "
                        "`updates.get(\"is_published\") is not None` 守卫"
                    )
        self.assertEqual(problems, [], "\n".join(problems))

    def test_no_truthiness_check_on_publish_state(self):
        """`if updates.get("is_published"):` 是错的：False 会被当成「没传」，永远下不了架。"""
        problems = []
        for module in ADMIN_FILES:
            func = self._update_func_ast(module)
            for node in ast.walk(func):
                if not isinstance(node, ast.If):
                    continue
                test = node.test
                if isinstance(test, ast.Call) and _is_updates_get_call(test):
                    problems.append(
                        f"{ADMIN_FILES[module]}:{node.lineno} 用真值判断 is_published"
                        "（False 会被当成「没传」，内容永远下不了架）"
                    )
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
