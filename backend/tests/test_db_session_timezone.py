"""MySQL 会话时区必须锁定为 UTC：func.now() 按会话时区求值而应用侧一律按 UTC 比较，
不一致不报错、只静默产生小时级偏差（免费时长白送、VIP 提前失效、日界跨错）。
compose 的 TZ=UTC 是隐式依赖容器时区，本机开发 MySQL 跟随系统（+08:00）即出错，
故必须在连接上显式 SET。本文件钉死：SQL 常量为数字偏移 +00:00，且每处 create_async_engine
都带上该 connect_args（漏掉即重新引入静默偏差）。"""

import ast
import re
import unittest
from pathlib import Path

from app import database

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"

# 需要检查「创建引擎必须带时区」的调用名
ENGINE_FACTORIES = ("create_async_engine", "create_engine", "engine_from_config")

# 刻意排除的路径：
#   venv / __pycache__ / node_modules —— 第三方代码
#   tests —— 本文件自己就会引用 "SET time_zone" 字符串做断言，属于自指误报；
#            且守卫要管的是**生产与迁移代码**，测试路径不是运行期入口
SKIP_DIRS = ("venv", "__pycache__", "node_modules", "tests")


def _iter_py_files():
    for path in BACKEND.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


class SessionTimezoneConstantTests(unittest.TestCase):
    def test_constant_uses_numeric_offset(self):
        """必须用数字偏移 `+00:00`：命名时区 `UTC` 依赖 mysql 库导入了时区表，
        在未导入的安装上会直接报错。"""
        self.assertEqual(database.MYSQL_SESSION_TZ_INIT, "SET time_zone='+00:00'")

    def test_engine_is_created_with_the_timezone_init(self):
        """运行期断言：引擎确实带上了 connect_args（不是只定义常量不用）。"""
        # SQLAlchemy 不公开 connect_args，但会把 DBAPI 的 connect 参数记在
        # dialect 的 create_connect_args 结果里 —— 这里退一步检查源码，见下方 AST 用例。
        src = (BACKEND / "app" / "database.py").read_text(encoding="utf-8")
        self.assertIn("connect_args=", src)
        self.assertIn("MYSQL_SESSION_TZ_INIT", src)


class EveryEngineSetsTimezoneTests(unittest.TestCase):
    """静态守卫：所有创建引擎的地方都必须带 connect_args（含时区）。"""

    def test_all_engine_factories_pass_connect_args(self):
        offenders: list[str] = []
        checked = 0
        for path in _iter_py_files():
            src = path.read_text(encoding="utf-8")
            if not any(name + "(" in src for name in ENGINE_FACTORIES):
                continue
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name)
                    else ""
                )
                if name not in ENGINE_FACTORIES:
                    continue
                checked += 1
                kwargs = {kw.arg for kw in node.keywords}
                if "connect_args" not in kwargs:
                    rel = path.relative_to(REPO_ROOT)
                    offenders.append(f"{rel}:{node.lineno} {name}() 缺少 connect_args")
        self.assertGreaterEqual(checked, 2, f"只扫描到 {checked} 处引擎创建，守卫可能已失效")
        self.assertEqual(
            offenders, [],
            "以下引擎创建未锁定会话时区，会重新引入 created_at 与 UTC 语义不一致：\n"
            + "\n".join(offenders),
        )

    def test_alembic_reuses_the_same_constant(self):
        """alembic 必须复用 app.database 的常量，而不是自己再写一份 SQL
        （两份字符串迟早漂移）。"""
        env_src = (BACKEND / "alembic" / "env.py").read_text(encoding="utf-8")
        self.assertIn("MYSQL_SESSION_TZ_INIT", env_src)
        self.assertNotIn(
            "SET time_zone", env_src,
            "alembic/env.py 里出现了硬编码的时区 SQL，应改为导入 MYSQL_SESSION_TZ_INIT",
        )

    def test_no_hardcoded_timezone_sql_elsewhere(self):
        """除 database.py 的定义处外，不应再有硬编码的 SET time_zone"""
        for path in _iter_py_files():
            if path.name == "database.py":
                continue
            src = path.read_text(encoding="utf-8")
            if re.search(r"SET\s+time_zone", src):
                self.fail(f"{path.relative_to(REPO_ROOT)} 硬编码了 SET time_zone，请复用常量")


if __name__ == "__main__":
    unittest.main()
