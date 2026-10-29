"""脚手架自检：生成的骨架必须**当场能被仓库守卫接受**，否则生成器就是负债。

不测「文案对不对」，只测三类硬事实：

1. 生成物能被 `ast.parse` 解析（模板里混入未转义的花括号会直接语法错）；
2. 分层约定没被模板破坏：`commit` 只在 service、`record_admin_action` 在每个写操作、
   路由都带 `require_admin("<key>:xxx")`；
3. 目标文件已存在时**拒绝覆盖**（生成器最危险的失败模式是把别人的模块冲掉）。
"""

from __future__ import annotations

import ast
import importlib.util
import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(BACKEND, "scripts", "new_module.py")


def _load_script():
    spec = importlib.util.spec_from_file_location("new_module", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScaffoldOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_script()
        cls.files = cls.mod.build_files("webinar", "线上课", "webinars")

    def _by_suffix(self, suffix: str) -> str:
        for path, content in self.files.items():
            if str(path).replace("\\", "/").endswith(suffix):
                return content
        raise AssertionError(f"生成物里没有 {suffix}")

    def test_generates_the_documented_five_files(self):
        names = sorted(p.name for p in self.files)
        self.assertEqual(
            names,
            [
                "List.tsx",
                "admin_webinar.py",
                "admin_webinar_service.py",
                "webinar.py",  # model
                "webinar.py",  # schema（同名不同目录）
            ],
        )

    def test_python_outputs_parse(self):
        checked = 0
        for path, content in self.files.items():
            if path.suffix != ".py":
                continue
            ast.parse(content)  # 语法错直接失败
            checked += 1
        self.assertGreaterEqual(checked, 4, "只解析到不到 4 个 Python 产物，模板可能没生成全")

    def test_service_owns_transactions_and_audit(self):
        service = self._by_suffix("admin_webinar_service.py")
        api = self._by_suffix("api/admin_webinar.py")

        # 审计：每个写操作（create/update/delete）都要留痕
        self.assertEqual(service.count("record_admin_action("), 3)
        for action in ('"create"', '"update"', '"delete"'):
            self.assertIn(action, service)

        # 事务边界：commit 只在 service，不能出现在 api 层（test_arch_guards 会判红）
        self.assertIn("await db.commit()", service)
        self.assertNotIn("commit(", api)

    def test_router_endpoints_are_permission_guarded(self):
        api = self._by_suffix("api/admin_webinar.py")
        for perm in ("webinar:view", "webinar:create", "webinar:edit", "webinar:delete"):
            self.assertIn(f'require_admin("{perm}")', api)
        # 五个端点：列表 / 详情 / 新增 / 修改 / 删除
        self.assertEqual(api.count("@router."), 5)

    def test_frontend_page_uses_the_shared_skeleton(self):
        page = self._by_suffix("List.tsx")
        for component in ("PageHeader", "ListToolbar", "DataTable"):
            self.assertIn(f"<{component}", page)
        self.assertIn("useCrudList", page)
        # 模板里的 `{{` 是 str.format 时代的转义残留：漏还原会让 import 变成 `import {{`（语法错）
        self.assertIn("import { useState } from 'react'", page)
        # 手写外壳会被 listPageCoverage.test.ts 判红
        self.assertNotIn("adm-tablecard", page)
        self.assertNotIn("adm-toolbar", page)


class ScaffoldSafetyTests(unittest.TestCase):
    def test_refuses_to_overwrite_existing_module(self):
        mod = _load_script()
        out, err = io.StringIO(), io.StringIO()
        # tutorial 是仓库里真实存在的模块：生成器必须拒绝，且一个文件都不写
        with redirect_stdout(out), redirect_stderr(err):
            code = mod.main(["tutorial", "--label", "教程"])
        self.assertEqual(code, 1)
        self.assertIn("已存在", err.getvalue())


if __name__ == "__main__":
    unittest.main()
