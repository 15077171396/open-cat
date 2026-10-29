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
from app.models.tutorial import Tutorial
from app.services import comment_service, favorite_service
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = REPO_ROOT / "backend" / "app" / "services"
API_DIR = REPO_ROOT / "backend" / "app" / "api"
ADMIN_COMMENT_LIST = REPO_ROOT / "admin-frontend" / "src" / "pages" / "Comments" / "List.tsx"

# 内容删除侧的清理调用只可能出现在这几个模块里
PURGE_HOSTS = (
    "admin_tutorial_service.py",
)

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
    """④：管理端词汇表逐字对齐（后端注册表是唯一来源）。"""

    _EXPECTED = set(comment_service.registered_comment_targets())

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

if __name__ == "__main__":
    unittest.main()
