#!/usr/bin/env python3
"""文档清单数字守卫：`项目说明.md` / `docs/README.md` 里手写的规模数字必须与代码实测一致。

为什么需要它：`docs/03-开发/项目说明.md` 自称「单一事实来源」，但数字一直靠人肉维护，
2026-10-29 全链路测试时实测已漂移 20%+（文档 198 路径 / 实测 205，文档 1632 用例 /
实测 1825），而 `check_docs_links.py` 只校验链接、不校验数字，所以长期无人发现。

零依赖、不连库、不起服务：只 import 应用取 OpenAPI 与 metadata，其余靠文件系统统计。
退出码：0 = 一致；1 = 有漂移（直接打印实测值，照着替换即可）。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
GUIDE = REPO / "docs" / "03-开发" / "项目说明.md"
README = REPO / "docs" / "README.md"


def _use_backend_venv() -> bool:
    """本机直接 `python scripts/check_doc_stats.py` 时系统 Python 没有 fastapi。

    有 backend/venv 就换用它的解释器重新执行一次（CI 里依赖已装好，不会走到这一步）。
    两者都没有时返回 False，由调用方跳过 OpenAPI 部分而不是崩溃。
    """
    try:
        import fastapi  # noqa: F401
        return True
    except ImportError:
        pass
    venv_py = BACKEND / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
    if venv_py.exists() and Path(sys.executable).resolve() != venv_py.resolve():
        os.execv(str(venv_py), [str(venv_py), *sys.argv])
    return False


sys.path.insert(0, str(BACKEND))

DECORATOR_RE = re.compile(r"^@router\.(get|post|put|delete|patch)\(", re.M)


def count_decorators() -> tuple[int, int, int]:
    """返回 (总装饰器数, admin_* 合计, admin_* 模块数)"""
    total = 0
    admin_total = 0
    admin_modules = 0
    for path in sorted((BACKEND / "app" / "api").glob("*.py")):
        n = len(DECORATOR_RE.findall(path.read_text(encoding="utf-8")))
        if not n:
            continue
        total += n
        if path.stem.startswith("admin_"):
            admin_total += n
            admin_modules += 1
    return total, admin_total, admin_modules


def openapi_counts() -> tuple[int, int]:
    from app.main import app

    spec = app.openapi()
    return len(spec["paths"]), sum(len(v) for v in spec["paths"].values())


def table_count() -> int:
    from app import models  # noqa: F401 - 导入即注册全部模型
    from app.database import Base

    return len(Base.metadata.tables)


def count_files(pattern: str, root: Path, exclude_init: bool = False) -> int:
    files = list(root.glob(pattern))
    if exclude_init:
        files = [p for p in files if p.name != "__init__.py"]
    return len(files)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _doc_value(path: Path, pattern: str, group: int) -> int:
    """取文档里已写的数字（无依赖环境下用它兜住 OpenAPI 那两项，避免误报）"""
    match = re.search(pattern, read(path), re.M)
    return int(match.group(group)) if match else 0


def main() -> int:
    if not GUIDE.exists() or not README.exists():
        print(f"缺少文档: {GUIDE} / {README}", file=sys.stderr)
        return 1

    decorators, admin_decorators, admin_modules = count_decorators()
    if not _use_backend_venv():
        print("⚠ 未安装后端依赖（fastapi 不可用），跳过 OpenAPI 数字校验；其余项继续。")
        paths, ops = _doc_value(GUIDE, r"\*\*(\d+) 条路径 · (\d+) 个操作\*\*", 1), _doc_value(
            GUIDE, r"\*\*(\d+) 条路径 · (\d+) 个操作\*\*", 2
        )
    else:
        paths, ops = openapi_counts()
    tables = table_count()
    migrations = count_files("*.py", BACKEND / "alembic" / "versions")
    test_files = count_files("test_*.py", BACKEND / "tests")
    services = count_files("*.py", BACKEND / "app" / "services", exclude_init=True)
    models = count_files("*.py", BACKEND / "app" / "models", exclude_init=True)

    guide = read(GUIDE)
    readme = read(README)

    # (文档位置, 正则, 组序号, 实测值, 说明)
    checks = [
        (GUIDE, r"\*\*(\d+) 条路径 · (\d+) 个操作\*\*", 1, paths, "OpenAPI 路径"),
        (GUIDE, r"\*\*(\d+) 条路径 · (\d+) 个操作\*\*", 2, ops, "OpenAPI 操作"),
        (GUIDE, r"### 7\.2 路由规模总览（(\d+) 条装饰器 / (\d+) 操作 / (\d+) 路径）", 1, decorators, "路由装饰器"),
        (GUIDE, r"### 7\.2 路由规模总览（(\d+) 条装饰器 / (\d+) 操作 / (\d+) 路径）", 2, ops, "7.2 操作数"),
        (GUIDE, r"### 7\.2 路由规模总览（(\d+) 条装饰器 / (\d+) 操作 / (\d+) 路径）", 3, paths, "7.2 路径数"),
        (GUIDE, r"\*\*(\d+) 个域模块\*\*", 1, admin_modules, "admin 域模块数"),
        (GUIDE, r"\| `admin_\*`（\*\*\d+ 个域模块\*\*[^|]*\| \*\*(\d+)\*\*", 1, admin_decorators, "admin 路由数"),
        (GUIDE, r"(\d+) 张表 / (\d+) 个迁移", 1, tables, "数据表数"),
        (GUIDE, r"(\d+) 张表 / (\d+) 个迁移", 2, migrations, "迁移文件数"),
        (GUIDE, r"(\d+) 个 service / (\d+) 个 model 文件", 1, services, "service 数"),
        (GUIDE, r"(\d+) 个 service / (\d+) 个 model 文件", 2, models, "model 文件数"),
        (GUIDE, r"tests/ +# (\d+) 个测试文件", 1, test_files, "后端测试文件数"),
        (README, r"(\d+) 张表、(\d+) 操作 / (\d+) 路径", 1, tables, "README 表数"),
        (README, r"(\d+) 张表、(\d+) 操作 / (\d+) 路径", 2, ops, "README 操作数"),
        (README, r"(\d+) 张表、(\d+) 操作 / (\d+) 路径", 3, paths, "README 路径数"),
    ]

    errors: list[str] = []
    for doc, pattern, group, actual, label in checks:
        text = guide if doc == GUIDE else readme
        match = re.search(pattern, text, re.M)
        name = doc.name
        if not match:
            errors.append(f"{name}: 找不到「{label}」的写法（正则 {pattern}），请检查文档是否被改写")
            continue
        documented = int(match.group(group))
        if documented != actual:
            errors.append(f"{name}: {label} 文档写 {documented}，实测 {actual}")

    if errors:
        print("❌ 文档清单数字与代码不一致：")
        for item in errors:
            print("  ✗ " + item)
        print(
            "\n实测值："
            f"装饰器 {decorators}（admin {admin_decorators} / {admin_modules} 模块）· "
            f"OpenAPI {paths} 路径 / {ops} 操作 · {tables} 表 · {migrations} 迁移 · "
            f"{test_files} 测试文件 · {services} service · {models} model"
        )
        return 1

    print(
        f"✅ 文档数字一致（{paths} 路径 / {ops} 操作 / {decorators} 装饰器 / "
        f"{tables} 表 / {migrations} 迁移 / {test_files} 测试文件）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
