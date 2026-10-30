"""仓库卫生守卫：不该进版本库的东西，一个都不许进。

**为什么需要它**：`scripts/pack-deploy.sh` 只在打包时检查产物，而问题往往更早发生 ——
一个 737KB 的运行期日志包（含 43 个手机号形态、224 个邮箱）曾被提交进仓库，
`.gitignore` 里的 `*.log` 挡不住轮转出来的 `*.log.zip`。它既污染部署包
（打包自检会报错），又把日志里的用户数据永久写进了 git 历史。

这类错误没有运行期症状，只能靠静态守卫。测试在无 `.git` 的环境（部署包内）自动跳过。
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 命中即失败：运行期产物、依赖目录、本地配置
_FORBIDDEN = {
    "node_modules": re.compile(r"(^|/)node_modules/"),
    "venv": re.compile(r"(^|/)(\.?venv)/"),
    "dist": re.compile(r"(^|/)dist/"),
    "pycache": re.compile(r"__pycache__"),
    "pyc": re.compile(r"\.pyc$"),
    "日志": re.compile(r"\.log$|\.log\.\d+$|\.log\.zip$"),
    "本地 .env": re.compile(r"(^|/)\.env$"),
    # 允许 logs/.gitkeep（compose 需要目录存在），其余 logs/ 下一律不许
    "logs 目录": re.compile(r"(^|/)logs/(?!\.gitkeep$)"),
}

# 单个已跟踪文件的体积上限。超限通常意味着把产物或数据当源码提交了。
MAX_FILE_BYTES = 3 * 1024 * 1024


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


class RepoTrackedFilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (REPO_ROOT / ".git").exists() or shutil.which("git") is None:
            raise unittest.SkipTest("非 git 工作区（部署包内），跳过仓库卫生检查")
        cls.files = [p for p in _git("ls-files", "-z").split("\0") if p]

    def test_some_files_tracked(self):
        """解析不到文件说明 git 调用失败，后面的断言会变成空集合比较（静默通过）"""
        self.assertGreater(len(self.files), 50, "解析到的已跟踪文件过少，守卫可能已失效")

    def test_no_runtime_artifacts(self):
        hits = {
            label: [f for f in self.files if pattern.search(f)]
            for label, pattern in _FORBIDDEN.items()
        }
        dirty = {label: files[:5] for label, files in hits.items() if files}
        self.assertEqual(
            dirty,
            {},
            "以下运行期产物 / 依赖目录被提交进了仓库（会污染部署包，日志还可能含用户数据）: "
            f"{dirty}",
        )

    def test_no_oversized_files(self):
        big = []
        for rel in self.files:
            path = REPO_ROOT / rel
            if path.is_file() and path.stat().st_size > MAX_FILE_BYTES:
                big.append(f"{rel}（{path.stat().st_size / 1024 / 1024:.1f} MB）")
        self.assertEqual(big, [], f"以下已跟踪文件超过 {MAX_FILE_BYTES // 1024 // 1024} MB，疑似产物: {big}")


if __name__ == "__main__":
    unittest.main()
