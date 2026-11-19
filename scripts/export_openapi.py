"""导出 OpenAPI 文档 JSON → 前端类型生成管线的源头（2026-11-20）。

用法（backend venv）：
    backend/venv/Scripts/python scripts/export_openapi.py

产出：admin-frontend/src/types/openapi.json（整份文档，含内容 API 与管理端）。
下游：`npm run gen:api`（openapi-typescript）把该 JSON 转成 api.gen.d.ts。

同步保证：tests/test_openapi_snapshot.py 断言仓库里的 JSON 与 app.openapi()
逐字节等价 —— 后端契约一变，测试即红，提示先跑本脚本再 gen:api。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

OUT = ROOT / "admin-frontend" / "src" / "types" / "openapi.json"


def main() -> int:
    from app.main import app

    spec = app.openapi()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    before = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    after = json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    OUT.write_text(after, encoding="utf-8", newline="\n")
    changed = "更新" if before != after else "无变化"
    paths = len(spec.get("paths", {}))
    print(f"{OUT.relative_to(ROOT)} {changed}（{paths} 个路径）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
