#!/usr/bin/env python3
"""开源文档集守卫（零依赖）：docs/07-开源文档 与 docs/assets 的清单与品牌首行校验。

历史背景：本脚本原本守卫 `docs/03-开发/项目说明.md` / `docs/README.md` 里手写的规模数字
（OpenAPI 路径数、表数、迁移数等必须与代码实测一致）。这两个「单一事实来源」文档已在
通用化清理中随 `docs/01~05、99` 一并删除，仓库不再维护需要与代码实测对齐的清单文档，
因此本脚本改为守卫开源文档集本身：

1. docs/07-开源文档/ 七份 .md 齐全且非空；
2. 每份文档的品牌首行为 `> 项目：Open Cat · 后台管理系统`（通用化清理后的统一口径）；
3. docs/assets/ 下根 README 引用的截图齐全。

零依赖、不连库、不起服务。
退出码：0 = 通过；1 = 有问题（直接打印缺失项）。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OPEN_DOCS = REPO / "docs" / "07-开源文档"
ASSETS = REPO / "docs" / "assets"

EXPECTED_DOCS = [
    "README.md",
    "01-功能文档.md",
    "02-需求文档.md",
    "03-技术文档.md",
    "04-架构文档.md",
    "05-开发文档.md",
    "06-UI设计规范.md",
]

BRAND_LINE_PREFIX = "> 项目：Open Cat · 后台管理系统"

EXPECTED_ASSETS = [
    "screenshot-dashboard.png",
    "screenshot-editor.png",
    "screenshot-audit.png",
]


def main() -> int:
    # Windows 控制台默认 GBK，输出 ✅ 会抛 UnicodeEncodeError，故强制 UTF-8 输出。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    errors: list[str] = []

    for name in EXPECTED_DOCS:
        path = OPEN_DOCS / name
        if not path.is_file():
            errors.append(f"缺少开源文档: docs/07-开源文档/{name}")
            continue
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            errors.append(f"开源文档为空: docs/07-开源文档/{name}")
            continue
        brand = next(
            (line for line in text.splitlines()[:10] if line.startswith("> 项目：")),
            None,
        )
        if brand is None:
            errors.append(f"docs/07-开源文档/{name}: 缺少品牌首行（> 项目：...）")
        elif not brand.startswith(BRAND_LINE_PREFIX):
            errors.append(
                f"docs/07-开源文档/{name}: 品牌首行应为「{BRAND_LINE_PREFIX}…」，实际为「{brand}」"
            )

    for name in EXPECTED_ASSETS:
        if not (ASSETS / name).is_file():
            errors.append(f"缺少截图资源: docs/assets/{name}")

    if errors:
        print("❌ 开源文档集校验不通过：")
        for item in errors:
            print("  ✗ " + item)
        return 1

    print(
        f"✅ 开源文档集齐全（{len(EXPECTED_DOCS)} 份文档 / {len(EXPECTED_ASSETS)} 张截图，"
        "品牌首行统一为 Open Cat）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
