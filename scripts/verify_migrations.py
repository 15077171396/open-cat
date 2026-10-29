"""本地验证 alembic 迁移链（upgrade → downgrade → upgrade）：在临时库跑通后自动删库，不碰现有数据。

用法（必须用 backend 的虚拟环境解释器，依赖 pymysql / alembic / app.config）：
    backend/venv/Scripts/python.exe scripts/verify_migrations.py     # Windows
    backend/venv/bin/python        scripts/verify_migrations.py     # Linux/macOS

可选环境变量 TMP_DB 指定临时库名（默认 ai_cat_migtest）；连接取自 backend/.env（不打印凭据）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
TMP_DB = os.environ.get("TMP_DB", "ai_cat_migtest")

try:
    import pymysql  # noqa: F401
except ImportError:
    venv_hint = BACKEND / "venv" / "Scripts" / "python.exe"
    sys.exit(
        "缺少 pymysql —— 请用 backend 的虚拟环境解释器运行：\n"
        f"  {venv_hint} {Path(__file__).name}\n"
        "（Linux/macOS 为 backend/venv/bin/python）"
    )


def _load_env_file() -> dict:
    """从 backend/.env 读连接信息（只读取，不打印）"""
    data = {}
    env_path = BACKEND / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip()
    return data


def main() -> int:
    if not BACKEND.is_dir():
        sys.exit(f"未找到 backend 目录: {BACKEND}")

    env_file = _load_env_file()
    host = env_file.get("DB_HOST", "127.0.0.1")
    port = int(env_file.get("DB_PORT", 3306))
    user = env_file.get("DB_USER", "root")
    password = env_file.get("DB_PASSWORD", "")

    conn = pymysql.connect(host=host, port=port, user=user, password=password, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TMP_DB}")
        cur.execute(f"CREATE DATABASE {TMP_DB} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    print(f"[1/5] 已创建临时库 {TMP_DB}（不影响任何现有库）")

    env = dict(os.environ)
    env["DB_NAME"] = TMP_DB  # 环境变量优先级高于 .env

    steps = [
        ("[2/5] alembic heads（必须是单头）", ("heads",)),
        ("[3/5] alembic upgrade head", ("upgrade", "head")),
        ("[4/5] alembic downgrade base（回滚能力）", ("downgrade", "base")),
        ("[5/5] alembic upgrade head（可重复执行）", ("upgrade", "head")),
    ]

    failed = False
    try:
        for label, args in steps:
            result = subprocess.run(
                [sys.executable, "-m", "alembic", *args],
                cwd=BACKEND, env=env, capture_output=True, text=True,
            )
            print(f"\n===== {label} -> {'OK' if result.returncode == 0 else 'FAILED'} =====")
            if result.stdout.strip():
                print(result.stdout.strip()[-2000:])
            if result.returncode != 0:
                print("---- stderr ----")
                print(result.stderr.strip()[-3000:])
                failed = True
                break
    finally:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {TMP_DB}")
        conn.close()
        print(f"\n[清理] 已删除临时库 {TMP_DB}")

    # Windows 默认 GBK 控制台无法编码 ✓/❌，脚本曾在成功时因为末行打印而崩成非 0 退出码
    print("\n结论:", "迁移链存在失败项 FAILED" if failed else "迁移链双向可逆 OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
