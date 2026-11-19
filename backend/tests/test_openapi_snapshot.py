"""OpenAPI 契约快照守卫：仓库里的 openapi.json 必须与 app.openapi() 完全一致。

背景（2026-11-20，P0-7）：前端类型由 `admin-frontend/src/types/openapi.json`
经 openapi-typescript 生成（`npm run gen:api`）。后端契约一变、忘了重新导出，
前端拿到的就是旧类型 —— 字段漂移静默发生。这条守卫让漂移**当次提交即红**：
后端 schema 改动 → 本测试失败 → 跑 scripts/export_openapi.py + npm run gen:api。

容错：对比的是规范化 JSON（sort_keys），与导出脚本的序列化口径一致。
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "admin-frontend" / "src" / "types" / "openapi.json"


class OpenApiSnapshotTests(unittest.TestCase):
    def test_snapshot_matches_current_schema(self):
        from app.main import app

        current = json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.assertTrue(
            SNAPSHOT.exists(),
            f"快照不存在：先跑 scripts/export_openapi.py 生成 {SNAPSHOT.name}",
        )
        committed = SNAPSHOT.read_text(encoding="utf-8")
        self.assertEqual(
            committed,
            current,
            "OpenAPI 快照过期：后端契约已变化 —— 跑 backend/venv/Scripts/python "
            "scripts/export_openapi.py，再到 admin-frontend 跑 npm run gen:api 更新前端类型",
        )

    def test_content_namespace_documented(self):
        """内容 API 命名空间必须出现在契约里（这是对外承诺的接口面）"""
        spec = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        content_paths = [p for p in spec.get("paths", {}) if p.startswith("/api/v1/content/")]
        self.assertGreater(len(content_paths), 0, "内容 API 未出现在 OpenAPI 契约中")


if __name__ == "__main__":
    unittest.main()
