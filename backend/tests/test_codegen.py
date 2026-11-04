"""代码生成器：命名校验、模板编译、ZIP 下载审计与 HTTP 接口。"""

from __future__ import annotations

import asyncio
import io
import unittest
import zipfile
from unittest import mock

from app.core.exceptions import AppException
from app.models.admin_audit_log import AdminAuditLog
from app.models.user import User
from app.services import codegen_service
from app.services.codegen_service import (
    CodegenConfig,
    ColumnSchema,
    TableSchema,
    _render,
    _validate_config,
)
from tests.conftest import async_client, make_user
from tests.fake_db import MemorySession

SCHEMA = TableSchema(
    name="positions",
    comment="岗位表",
    columns=(
        ColumnSchema("id", "BIGINT", "BigInteger", "int", False, "", primary=True),
        ColumnSchema(
            "code", "VARCHAR(50)", "String(50)", "str", False, "岗位编码", unique=True, indexed=True
        ),
        ColumnSchema("name", "VARCHAR(50)", "String(50)", "str", False, "岗位名称", unique=True),
        ColumnSchema("description", "TEXT", "Text", "str", True, "岗位说明"),
        ColumnSchema("sort_order", "INTEGER", "Integer", "int", False, "排序", default="'0'"),
        ColumnSchema("is_enabled", "TINYINT(1)", "Boolean", "bool", False, "是否启用", default="'1'"),
        ColumnSchema("created_at", "DATETIME", "DateTime", "datetime", False, ""),
        ColumnSchema("updated_at", "DATETIME", "DateTime", "datetime", False, ""),
    ),
    uniques=(("code",), ("name",)),
)


def _config(**overrides) -> CodegenConfig:
    values = {
        "table": "positions",
        "module": "sample_positions",
        "class_name": "SamplePositions",
        "title": "岗位",
        "route_prefix": "/sample-positions",
        "fields": (),
    }
    values.update(overrides)
    return CodegenConfig(**values)


def _admin() -> User:
    return User(id=1, account="admin@test", role="admin")


class CodegenRenderTests(unittest.TestCase):
    def test_renders_expected_files_and_compiles(self):
        config, warnings = _validate_config(SCHEMA, _config())
        files = _render(SCHEMA, config)
        paths = [item["path"] for item in files]
        self.assertEqual(
            paths,
            [
                "backend/app/models/sample_positions.py",
                "backend/app/schemas/sample_positions.py",
                "backend/app/services/sample_positions_service.py",
                "backend/app/api/admin_sample_positions.py",
                "admin-frontend/src/pages/SamplePositions/List.tsx",
                "INTEGRATION.md",
            ],
        )
        for item in files:
            if item["path"].endswith(".py"):
                compile(item["content"], item["path"], "exec")
        self.assertIn("SamplePositionsCreate", files[1]["content"])
        self.assertIn("sort_order: int = 0", files[1]["content"])
        self.assertIn("record_admin_action", files[2]["content"])
        # 前后端权限码同源：INTEGRATION 与前端守卫都用模块命名空间（不再是 setting:*）
        # files[4] = 前端列表页，files[5] = INTEGRATION.md
        self.assertIn("sample_positions:view", files[5]["content"])
        self.assertIn("sample_positions:edit", files[4]["content"])
        self.assertTrue(any("唯一约束" in warning for warning in warnings))

    def test_frontend_page_renders_contract(self):
        """前端骨架页：组件结构、权限守卫、条件导入与「无 Jinja 残留」。"""
        config, _ = _validate_config(SCHEMA, _config())
        page = next(
            item["content"]
            for item in _render(SCHEMA, config)
            if item["path"].endswith("List.tsx")
        )
        self.assertIn("export default function SamplePositionsList", page)
        self.assertIn("useCrudList<SamplePositionsRow>", page)
        self.assertIn("paginated: false", page, "生成的后端列表不分页，前端必须对齐")
        self.assertIn('perm="sample_positions:create"', page)
        self.assertIn('perm="sample_positions:edit"', page)
        self.assertIn('perm="sample_positions:delete"', page)
        # is_enabled 是 bool → Switch 条件导入；description 是长文本名 → TextArea
        self.assertIn("Switch", page)
        self.assertIn("Input.TextArea", page)
        self.assertIn("formatTime", page, "created_at 列渲染时间")
        # Jinja 残留 / 嵌套花括号都不会产出可编译的 TSX
        self.assertNotIn("{{", page)
        self.assertNotIn("{%", page)
        self.assertNotIn("setting:", page)
        # DataTable 的 empty 必填三键（本断言来自真实 tsc 校验的一次翻车）
        self.assertIn("hasFilter: false", page)
        self.assertIn("filteredText:", page)

    def test_frontend_imports_follow_field_kinds(self):
        """没有 bool/数字字段时对应控件不应被导入（未使用导入过不了前端 tsc）。"""
        plain = TableSchema(
            name="plain",
            comment="",
            columns=(
                ColumnSchema("id", "BIGINT", "BigInteger", "int", False, "", primary=True),
                ColumnSchema("title", "VARCHAR(50)", "String(50)", "str", False, "标题"),
            ),
        )
        config, _ = _validate_config(plain, _config(table="plain", module="plain", class_name="Plain"))
        page = next(
            item["content"]
            for item in _render(plain, config)
            if item["path"].endswith("List.tsx")
        )
        self.assertNotIn("Switch", page)
        self.assertNotIn("InputNumber", page)
        self.assertIn("Input", page)
        self.assertNotIn("formatTime", page, "没有 created_at 时不引时间格式化")

    def test_selected_fields_limit_create_and_search(self):
        config, _ = _validate_config(SCHEMA, _config(fields=("name",)))
        service = next(
            item["content"]
            for item in _render(SCHEMA, config)
            if item["path"].endswith("_service.py")
        )
        schema = next(
            item["content"]
            for item in _render(SCHEMA, config)
            if item["path"].endswith("schemas/sample_positions.py")
        )
        self.assertIn("name: str", schema.split("class SamplePositionsUpdate")[0])
        self.assertNotIn("code: str", schema.split("class SamplePositionsUpdate")[0])
        self.assertNotIn("SamplePositions.code.like", service)
        self.assertIn("SamplePositions.name.like", service)

    def test_invalid_names_and_fields_are_rejected(self):
        cases = [
            (_config(module="Bad-Module"), "模块名"),
            (_config(class_name="bad_class"), "类名"),
            (_config(route_prefix="no-slash"), "路由前缀"),
            (_config(fields=("missing",)), "字段不可编辑"),
            (_config(module="class"), "模块名"),
        ]
        for config, message in cases:
            with self.subTest(config=config):
                with self.assertRaises(AppException) as ctx:
                    _validate_config(SCHEMA, config)
                self.assertIn(message, ctx.exception.message)

    def test_table_without_editable_fields_is_rejected(self):
        schema = TableSchema(
            name="counter",
            comment="",
            columns=(
                ColumnSchema("id", "BIGINT", "BigInteger", "int", False, "", primary=True),
                ColumnSchema("created_at", "DATETIME", "DateTime", "datetime", False, ""),
            ),
        )
        with self.assertRaises(AppException) as ctx:
            _validate_config(schema, _config(table="counter", module="counter_gen", class_name="CounterGen"))
        self.assertIn("没有可编辑字段", ctx.exception.message)

    def test_composite_primary_key_and_reserved_names_are_rejected(self):
        composite = TableSchema(
            name="links",
            comment="",
            columns=(
                ColumnSchema("left_id", "BIGINT", "BigInteger", "int", False, "", primary=True),
                ColumnSchema("right_id", "BIGINT", "BigInteger", "int", False, "", primary=True),
                ColumnSchema("note", "VARCHAR(20)", "String(20)", "str", True, ""),
            ),
        )
        composite_config, _ = _validate_config(
            composite, _config(table="links", module="links_gen", class_name="LinksGen")
        )
        with self.assertRaises(AppException) as ctx:
            _render(composite, composite_config)
        self.assertIn("复合主键", ctx.exception.message)

        reserved = TableSchema(
            name="reserved",
            comment="",
            columns=(
                ColumnSchema("id", "BIGINT", "BigInteger", "int", False, "", primary=True),
                ColumnSchema("db", "VARCHAR(20)", "String(20)", "str", False, ""),
            ),
        )
        with self.assertRaises(AppException) as ctx:
            _validate_config(reserved, _config(table="reserved", module="reserved_gen", class_name="ReservedGen"))
        self.assertIn("函数参数冲突", ctx.exception.message)

    def test_download_writes_zip_and_audit(self):
        db = MemorySession()

        async def fake_load(_table: str) -> TableSchema:
            return SCHEMA

        with mock.patch.object(codegen_service, "_load_table", new=fake_load):
            filename, content = asyncio.run(
                codegen_service.download(
                    db,
                    _admin(),
                    table="positions",
                    module="sample_positions",
                    class_name="SamplePositions",
                    title="岗位",
                    route_prefix="/sample-positions",
                )
            )
        self.assertEqual(filename, "sample_positions-codegen.zip")
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            self.assertIn("backend/app/models/sample_positions.py", names)
            self.assertIn("admin-frontend/src/pages/SamplePositions/List.tsx", names)
            self.assertIn("INTEGRATION.md", names)
        actions = [row["action"] for row in db.all_rows(AdminAuditLog)]
        self.assertEqual(actions, ["codegen_download"])


class CodegenApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_tables_and_preview_use_service_results(self):
        tables = {"available": True, "database": "open_cat", "items": []}
        preview = {"available": True, "warnings": [], "files": []}
        with (
            mock.patch.object(codegen_service, "get_tables", new=mock.AsyncMock(return_value=tables)),
            mock.patch.object(
                codegen_service, "preview", new=mock.AsyncMock(return_value=preview)
            ) as preview_mock,
        ):
            async with async_client(user=make_user(role="admin")) as client:
                response = await client.get("/api/v1/admin/codegen/tables")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["data"]["database"], "open_cat")

                response = await client.get(
                    "/api/v1/admin/codegen/preview",
                    params={
                        "table": "positions",
                        "module": "sample_positions",
                        "class_name": "SamplePositions",
                        "fields": "code,name",
                    },
                )
                self.assertEqual(response.status_code, 200)
        preview_mock.assert_awaited_once()
        self.assertEqual(preview_mock.await_args.kwargs["fields"], ["code", "name"])

    async def test_download_returns_zip_headers(self):
        with mock.patch.object(
            codegen_service,
            "download",
            new=mock.AsyncMock(return_value=("sample_positions-codegen.zip", b"PK-test")),
        ):
            async with async_client(user=make_user(role="admin")) as client:
                response = await client.post(
                    "/api/v1/admin/codegen/download",
                    json={
                        "table": "positions",
                        "module": "sample_positions",
                        "class_name": "SamplePositions",
                        "fields": ["code", "name"],
                    },
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertIn("sample_positions-codegen.zip", response.headers["content-disposition"])
        self.assertEqual(response.content, b"PK-test")


if __name__ == "__main__":
    unittest.main()
