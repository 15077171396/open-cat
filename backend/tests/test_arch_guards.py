"""三条零依赖架构守卫（第四轮全量审查 P2-29 / P2-32 / P2-35）。

为什么用测试而不是 linter：本仓库**没有任何 linter**（无 ruff/flake8/eslint 配置，
CI 无 lint 步骤，本机 venv 也没有可装的 ruff），而 P1-31 那次拆分往 13 个管理端路由
模块里各抄了一份 import 头，从此再没人发现 —— 所以这里用仓库自己已经在用的方式
（纯 stdlib 静态守卫测试，见 `test_admin_route_guards.py` / `test_rbac.py`）把三类
"会静默变糟"的问题钉住。

三条守卫分别对应：
- **P2-29** 提交归属三套约定并存 → 路由层不得再新增 `db.commit(`（现存 3 处已登记为债）
- **P2-32** `admin.py` 的手工 import 顺序 = 路由优先级 → 静态路由必须早于遮蔽它的参数路由
- **P2-35** 无 linter → 未使用 import 不允许增长（基线只减不增）
"""

import ast
import pathlib
import re
import unittest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
API_DIR = BACKEND / "app" / "api"

# --- P2-29 基线：路由层现存的手工 commit（第三轮 P0-9 系列打的补丁）---
# 每一处都是「service 刻意不 commit、又必须在响应前落库」的产物。
# 理想终局是 service 层自持事务（P2-29），但先别让它继续长。
ALLOWED_ROUTER_COMMITS = {
    "admin_settings.py": ["# AI 配置缓存必须在提交之后失效，故路由内显式提交"],
    # 登录管理同口径：集成配置缓存（60s Redis + 30s 进程内）必须**提交之后**失效，
    # 否则并发读会把旧值回填进缓存，表现为「保存成功但登录页还是老样子」
    "admin_login_settings.py": ["# 登录配置缓存必须在提交之后失效，故路由内显式提交"],
    # 法律文档同理：legal_doc:* 独立缓存必须在提交之后失效，
    # 否则前台页脚会继续展示旧文案（而这正是法务文本最不能出错的地方）
    "admin_legal_documents.py": ["# 法务文本缓存必须在提交之后失效，故路由内显式提交"],
    # 支付配置：集成配置缓存（60s）必须提交之后失效，否则并发下单会用旧凭据签名
    "admin_payment_settings.py": ["# 支付配置缓存必须在提交之后失效，故路由内显式提交"],
    # AI 成本护栏（并入模型管理）：限额/单价缓存在 ai_usage_service（Redis 60s），
    # 提交之后再失效，否则并发看板读会把旧阈值回填
    "admin_ai_models.py": ["# 成本护栏缓存必须在提交之后失效，故路由内显式提交"],
    # 邮件通道：① 配置经 runtime_config 注入 settings，必须提交后再应用；
    # ② 测试发信的审计在失败分支也要落库（否则事务回滚会把这条记录丢掉）
    "admin_mail_settings.py": ["# 邮件配置须在提交后应用；测试发信审计失败分支也要落库"],
    "admin_users.py": [],
    "users.py": [],
}

# --- P2-35 基线：既有的未使用 import（只减不增；修掉一处就同步删一行）---
# 2026-10-18：存量 15 个文件的未使用 import 已全部清零，基线收缩为空。
# 守卫仍生效 —— 此后任何新增的未使用 import 都会直接判红。
UNUSED_IMPORT_BASELINE: dict[str, set[str]] = {}


def _route_entries():
    """按注册顺序展开 /api/v1/admin 的 (method, path)。

    FastAPI 0.139 起 `include_router` 延迟展开，`app.routes` 里只有包装对象，
    必须递归走 `_walk`（同 `test_admin_route_guards.py`）。
    """
    from app.main import app
    from tests.test_admin_route_guards import _walk

    out = []
    for path, route, _deps in _walk(app.router):
        if not path.startswith("/api/v1/admin"):
            continue
        for method in sorted(getattr(route, "methods", set()) or set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            out.append((method, path))
    return out


class RouterCommitOwnershipTests(unittest.TestCase):
    """P2-29：事务提交归属不能继续在路由层扩散"""

    def test_no_new_db_commit_in_api_layer(self):
        found = {}
        for path in sorted(API_DIR.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            n = text.count("db.commit(")
            if n:
                found[path.name] = n
        self.assertEqual(
            set(found) - set(ALLOWED_ROUTER_COMMITS), set(),
            f"app/api/ 新增了手工 commit（应下沉到 service 层）：{sorted(set(found) - set(ALLOWED_ROUTER_COMMITS))}",
        )

    def test_baseline_is_not_stale(self):
        """登记过的站点若被删掉，基线必须同步收缩（否则基线会变成谎话）"""
        actual = {}
        for path in API_DIR.glob("*.py"):
            n = path.read_text(encoding="utf-8").count("db.commit(")
            if n:
                actual[path.name] = n
        for name in ALLOWED_ROUTER_COMMITS:
            self.assertIn(
                name, actual,
                f"{name} 里的手工 commit 已移除，请把 {name} 从 ALLOWED_ROUTER_COMMITS 删掉",
            )


class AdminRouteShadowingTests(unittest.TestCase):
    """P2-32：静态路径必须早于会遮蔽它的参数路径

    `api/admin.py` 的 import 顺序**就是**路由优先级（注释自己写着「请勿随意调整」），
    而一次 IDE「organize imports」就能把 `/tutorials/batch-delete` 排到
    `/tutorials/{tutorial_id}` 之后 —— 于是它被永久遮蔽，表现为 404/类型校验失败，
    且**没有任何报错**。本守卫不依赖人工清单：直接从真实 app 的路由顺序判定。
    """

    @staticmethod
    def _can_shadow(general: str, concrete: str) -> bool:
        g, c = general.split("/"), concrete.split("/")
        if len(g) != len(c) or g == c:
            return False
        for gs, cs in zip(g, c):
            param = gs.startswith("{") and gs.endswith("}")
            if not (gs == cs or param):
                return False
        return any(gs.startswith("{") for gs in g)

    def test_can_shadow_semantics(self):
        """守卫自身要有牙：这些配对钉住 _can_shadow 的判定边界

        没有这一条，把 `_can_shadow` 改成恒 False 就能让上面的测试永久假绿
        （第三轮报告用变异测试证实过这类「测了个寂寞」的守卫）。
        """
        # 参数路由在前 → 后面的字面路由永不可达（正是本守卫要拦的）
        self.assertTrue(self._can_shadow("/a/t/{id}", "/a/t/batch-delete"))
        # 字面在前是正确顺序，不算遮蔽
        self.assertFalse(self._can_shadow("/a/t/batch-delete", "/a/t/{id}"))
        # 同一路径（重复注册）不由本守卫负责
        self.assertFalse(self._can_shadow("/a/t/{id}", "/a/t/{id}"))
        # 段数不同不可能互相遮蔽
        self.assertFalse(self._can_shadow("/a/{id}/nodes", "/a/b"))
        # 全参数路由同样会遮蔽同深度的字面路由（第一匹配者胜出）
        self.assertTrue(self._can_shadow("/a/{i}/{j}", "/a/b/c"))
        # 反向：字面段在前、参数段在后 = 正确顺序
        self.assertFalse(self._can_shadow("/a/{i}/list", "/a/{i}/{j}"))

    def test_wrong_order_is_actually_detected(self):
        """反向对照：错误顺序必须报出来（否则上面的 0 违规没有意义）"""
        good = [("GET", "/a/t/batch-delete"), ("GET", "/a/t/{id}")]
        bad = [("GET", "/a/t/{id}"), ("GET", "/a/t/batch-delete")]

        def scan(entries):
            out = []
            for i, (m1, p1) in enumerate(entries):
                for j in range(i + 1, len(entries)):
                    m2, p2 = entries[j]
                    if m1 == m2 and self._can_shadow(p1, p2):
                        out.append(f"{m2} {p2} 被 {p1} 遮蔽")
            return out

        self.assertEqual(scan(good), [])
        self.assertEqual(len(scan(bad)), 1, "调换顺序后守卫没报警 = 它已经失效")

    def test_no_static_route_is_shadowed_by_an_earlier_param_route(self):
        entries = _route_entries()
        self.assertGreater(len(entries), 50, f"只展开到 {len(entries)} 条管理端路由，守卫可能失效")
        offenders = []
        for i, (method_a, path_a) in enumerate(entries):
            for j in range(i + 1, len(entries)):
                method_b, path_b = entries[j]
                if method_a != method_b:
                    continue
                if self._can_shadow(path_a, path_b):
                    offenders.append(f"{method_a} {path_b} 被更靠前的 {path_a} 遮蔽")
        self.assertEqual(
            offenders, [],
            "管理端路由顺序存在遮蔽（调整 admin.py 的 include 顺序就会踩到）：\n" + "\n".join(offenders),
        )


class UnusedImportRatchetTests(unittest.TestCase):
    """P2-35：没有 linter，至少不许未使用 import 继续长"""

    @staticmethod
    def _reexported_names():
        """被其它模块 `from app.api.X import name` 转手引用的名字不算死代码"""
        seen = set()
        for p in BACKEND.rglob("*.py"):
            src = p.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"from\s+app\.api\.(\w+)[^(\n]*import\s+([^\n(]+)", src):
                for n in re.findall(r"\b\w+\b", m.group(2)):
                    seen.add((f"{m.group(1)}.py", n))
        return seen

    @classmethod
    def _scan(cls):
        reexported = cls._reexported_names()
        found = {}
        for path in sorted(API_DIR.glob("*.py")):
            if path.name == "admin.py":
                continue  # 这里的 import 就是注册动作，不是引用
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
            imported, used = [], set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    imported += [a.asname or a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.Name):
                    used.add(node.id)
                elif isinstance(node, ast.Attribute):
                    v = node
                    while isinstance(v, ast.Attribute):
                        v = v.value
                    if isinstance(v, ast.Name):
                        used.add(v.id)
            dead = {
                n for n in imported
                if n not in used
                and len(re.findall(r"\b" + re.escape(n) + r"\b", src)) < 2
                and (path.name, n) not in reexported
            }
            if dead:
                found[path.name] = dead
        return found

    def test_no_new_unused_imports(self):
        for name, dead in self._scan().items():
            self.assertFalse(
                dead - UNUSED_IMPORT_BASELINE.get(name, set()),
                f"{name} 新增了未使用的 import：{sorted(dead - UNUSED_IMPORT_BASELINE.get(name, set()))}"
                "（仓库无 linter，靠本守卫拦下）",
            )

    def test_baseline_shrinks_when_fixed(self):
        """基线只减不增：清掉一处就要从这里删一处，否则它成永久谎话"""
        current = self._scan()
        for name, baselined in UNUSED_IMPORT_BASELINE.items():
            self.assertTrue(
                baselined <= current.get(name, set()),
                f"{name} 的未使用 import 已减少（基线里 {sorted(baselined - current.get(name, set()))} 已不存在），"
                "请同步更新 UNUSED_IMPORT_BASELINE",
            )



class PrivateCrossModuleImportTests(unittest.TestCase):
    """P2-37②：跨模块不得 import 下划线符号（基线为 0，不设豁免表）。

    下划线名是「模块内部实现」的声明。跨模块引用它意味着：重构方改名或删掉它时
    **没有任何信号** —— 调用点散落在另一个模块里，既不报错也不进任何清单。
    本轮整改前仓库里有 5 处（`_mask` ×3、`_raw_to_jsdelivr`、`_pick_token`），
    现已全部改为公开名，所以这里可以直接要求 0 而不是维护一份基线。
    """

    @staticmethod
    def _scan(source: str) -> list[str]:
        """返回 `from app.X import _y` 形态的违规项（调用方已带路径，这里只回名字）"""
        found = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level or not (node.module or '').startswith('app.'):
                continue  # 相对 import 与标准库 import 不在本守卫范围
            for alias in node.names:
                if alias.name.startswith('_'):
                    found.append(f'{node.module}.{alias.name}')
        return found

    def test_scanner_detects_a_violation(self):
        """反向对照：谓词必须能判红一段真实的违规 import，否则下面的守卫恒绿。"""
        bad = "from app.utils.verification import verify_code, _mask\n"
        self.assertEqual(self._scan(bad), ['app.utils.verification._mask'])
        # 合规形态不该被误报：同模块内部的下划线函数、标准库、相对 import
        self.assertEqual(self._scan("from app.utils.verification import mask_account\n"), [])
        self.assertEqual(self._scan("from ._helpers import _thing\n"), [])
        self.assertEqual(self._scan("import httpx\n"), [])

    def test_no_private_cross_module_imports(self):
        root = pathlib.Path(__file__).resolve().parents[1] / 'app'
        violations = []
        for path in sorted(root.rglob('*.py')):
            if '__pycache__' in path.parts:
                continue
            for hit in self._scan(path.read_text(encoding='utf-8')):
                violations.append(f'{path.relative_to(root.parent)}: {hit}')
        self.assertFalse(
            violations,
            '跨模块引用了私有符号（改名时不会有任何信号）：\n' + '\n'.join(violations),
        )



class KeywordParamBoundTests(unittest.TestCase):
    """P2-50⑧：`keyword` 查询参数只能有一个上界口径。

    整改前是**三套**：公开 5 处 `max_length=100`、admin 2 处 `200`、其余 11 处无上界。
    无界者直入 `MATCH AGAINST` / `LIKE ESCAPE`（`services/search.py` 只转义通配符、
    不限长度），所以「加不加 max_length」不是风格问题，而是「这条查询会不会被一条
    超长输入拖成昂贵全表模式匹配」的问题。
    """

    KEYWORD_QUERY = re.compile(r'^\s*keyword:.*=\s*Query\(')

    def _violations_in(self, source: str) -> list[str]:
        from app.schemas.common import KEYWORD_MAX_LENGTH

        bad = []
        for line in source.split('\n'):
            if self.KEYWORD_QUERY.match(line) and 'KEYWORD_MAX_LENGTH' not in line:
                bad.append(line.strip())
        return bad

    def test_predicate_rejects_a_literal_or_missing_bound(self):
        """反向对照：数字字面量与完全没写上界都必须被判红，否则本守卫恒绿。"""
        self.assertEqual(
            self._violations_in('    keyword: str = Query(None, max_length=100),\n'),
            ['keyword: str = Query(None, max_length=100),'],
        )
        self.assertEqual(
            len(self._violations_in('    keyword: Optional[str] = Query(None),\n')), 1
        )
        self.assertEqual(
            self._violations_in(
                '    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),\n'
            ),
            [],
        )

    def test_all_keyword_params_use_the_shared_bound(self):
        root = pathlib.Path(__file__).resolve().parents[1] / 'app' / 'api'
        offenders = []
        for path in sorted(root.glob('*.py')):
            for v in self._violations_in(path.read_text(encoding='utf-8')):
                offenders.append(f'{path.name}: {v}')
        self.assertFalse(
            offenders,
            'keyword 参数出现了非共享上界（数字字面量或无 max_length）：\n'
            + '\n'.join(offenders),
        )


class EagerAnnotationForwardRefTests(unittest.TestCase):
    """模块级函数的注解不得前向引用本模块更晚绑定的名字。

    生产镜像是 Python 3.11，注解在 `def` 执行时**立即求值**；本机 venv 与开发用的
    3.14 起注解默认惰性求值（PEP 649）。于是同一份源码在本地 `import` 成功、在容器里
    导入期直接 `NameError` —— gunicorn 表现为 `HaltServer 'Worker failed to boot.'`，
    而 912 个本地用例全绿。2026-10-13 发版就栽在 `setting_service.py:76` 的这一行上。

    合规写法有两种：把类型定义在使用之前，或像 `comment_service` / `content_cache` 那样
    在模块头加 `from __future__ import annotations`（后者同时把整份文件免疫掉）。
    """

    def _scan(self, source: str) -> list[str]:
        """返回「注解引用了本模块更晚绑定的名字」的 `行号:名字` 列表。"""
        tree = ast.parse(source)
        if any(
            isinstance(n, ast.ImportFrom)
            and n.module == '__future__'
            and any(a.name == 'annotations' for a in n.names)
            for n in tree.body
        ):
            return []  # 惰性注解：整模块不求值，安全

        bound: dict[str, int] = {}

        def mark(name: str, lineno: int) -> None:
            if name and name not in bound:
                bound[name] = lineno

        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                mark(node.name, node.lineno)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    mark((alias.asname or alias.name).split('.')[0], node.lineno)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    mark(alias.asname or alias.name, node.lineno)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for inner in ast.walk(target):
                        if isinstance(inner, ast.Name):
                            mark(inner.id, node.lineno)

        found: list[str] = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            anns = [node.returns]
            args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            anns += [a.annotation for a in args if a.annotation is not None]
            for extra in (node.args.vararg, node.args.kwarg):
                if extra is not None and extra.annotation is not None:
                    anns.append(extra.annotation)
            for ann in anns:
                if ann is None or (isinstance(ann, ast.Constant) and isinstance(ann.value, str)):
                    continue  # 引号前向引用是合规写法
                for inner in ast.walk(ann):
                    if not (isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load)):
                        continue
                    line = bound.get(inner.id)
                    if line is not None and line > node.lineno:
                        found.append(f'{node.lineno}: {inner.id}（绑定于 :{line}）')
        return found

    def test_scanner_detects_a_violation(self):
        """反向对照：谓词必须能判红一段真实的前向引用，否则下面的守卫恒绿。"""
        bad = "def f(x: Thing) -> None:\n    ...\n\n\nclass Thing:\n    ...\n"
        self.assertEqual(self._scan(bad), ['1: Thing（绑定于 :5）'])
        # 三种合规形态不得误报：已绑定、加引号、future annotations
        self.assertEqual(self._scan("class Thing: ...\ndef f(x: Thing) -> None: ...\n"), [])
        self.assertEqual(self._scan('def f(x: "Thing") -> None: ...\nclass Thing: ...\n'), [])
        self.assertEqual(
            self._scan('from __future__ import annotations\n\ndef f(x: Thing) -> None: ...\n'
                       'class Thing: ...\n'),
            [],
        )
        # 名字来自 import（本模块无绑定）→ 由全局解析，不在本守卫范围
        self.assertEqual(self._scan("def f(x: Decimal) -> None: ...\n"), [])

    def test_no_eager_forward_ref_in_production_modules(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        offenders: list[str] = []
        for base in ('app', 'scheduler'):
            for path in sorted((root / base).rglob('*.py')):
                if '__pycache__' in path.parts:
                    continue
                for hit in self._scan(path.read_text(encoding='utf-8')):
                    offenders.append(f'{path.relative_to(root)}: {hit}')
        self.assertFalse(
            offenders,
            '模块级注解前向引用了本模块更晚绑定的名字 —— Python 3.11（生产镜像）导入期'
            '直接 NameError，本地 3.14 却看不出来：\n' + '\n'.join(offenders),
        )


if __name__ == "__main__":


    unittest.main()


class ModelRegistrationTests(unittest.TestCase):
    """模型注册守卫：`app/models/*.py` 里定义的每个 Base 子类都必须在 `Base.metadata` 里。

    为什么要有它：注册入口只有 `app/models/__init__.py` 一处，**漏登记不会报任何错** ——
    service 直接 `from app.models.xxx import X` 照常工作，但 `alembic --autogenerate`
    会把漏登记的表判成「多余」并生成 `DROP TABLE`。
    这个坑已发生三次：P1-16 的 5 张表、`TutorialPurchase`，以及 2026-10-27 发现的
    `search_logs` / `content_views`（这两张表上线已久，autogenerate 一直想删它们）。
    """

    @staticmethod
    def _declared_models():
        """遍历 app/models 下每个模块，收集**定义在该模块内**的 Base 子类"""
        import importlib
        import inspect
        import pkgutil

        import app.models as pkg
        from app.database import Base

        found = {}
        for info in pkgutil.iter_modules(pkg.__path__):
            module = importlib.import_module(f"app.models.{info.name}")
            for name, obj in vars(module).items():
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, Base)
                    and obj is not Base
                    and obj.__module__ == module.__name__
                ):
                    found[f"{module.__name__}.{name}"] = obj
        return found

    def test_every_model_is_registered(self):
        import app.models  # noqa: F401  导入即注册（唯一入口）
        from app.database import Base

        tables = set(Base.metadata.tables)
        missing = {
            path: getattr(model, "__tablename__", "?")
            for path, model in self._declared_models().items()
            if getattr(model, "__tablename__", None) not in tables
        }
        self.assertEqual(
            missing,
            {},
            "以下模型没在 app/models/__init__.py 注册 —— autogenerate 会为它们生成 DROP TABLE: "
            f"{missing}",
        )

    def test_every_model_is_in_all(self):
        import app.models as pkg

        exported = set(getattr(pkg, "__all__", []))
        missing = {
            path.rsplit(".", 1)[-1]
            for path in self._declared_models()
            if path.rsplit(".", 1)[-1] not in exported
        }
        self.assertEqual(missing, set(), f"模型的 __all__ 漏登记（不影响运行，但清单会漂）: {missing}")
