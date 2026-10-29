"""列表导出的契约测试。

导出功能的失败代价不是「报错」，而是**数据悄悄不对**：少几行、多几列、
或者拿到 Excel 里才发现公式被执行。所以这里钉五条：

1. **口径与列表一致**：复用列表的查询函数，筛选白名单之外不传。
2. **CSV 注入防护**：用户可控内容里写 `=cmd|...` 不能让 Excel 当公式执行。
3. **换行不能破坏结构**：多行内容压成一行，否则整表错位。
4. **截断必须写在文件里**：拿到文件的人不能误以为这是全量。
5. **导出要留痕**：数据外带必须可追溯到人、资源、行数、筛选条件。
"""

import asyncio
import csv
import io
import unittest
from dataclasses import replace
from unittest import mock

from app.services import export_service as es


def _collect(gen) -> str:
    """把异步生成器跑完并拼成字符串。"""

    async def run() -> str:
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
        return "".join(chunks)

    return asyncio.run(run())


class _FakeSession:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


def _fake_fetch_factory(total: int):
    """按请求的 size 返回行 —— 与真实 service 一致（有的实现会返回更少，但那是异常情况）。"""
    calls: list[tuple[int, int]] = []

    async def fetch(db, page, size, filters):
        calls.append((page, size))
        start = (page - 1) * size
        end = min(start + size, total)
        items = [
            {"id": i, "account": f"user{i}", "created_at": "2026-10-30T01:02:03.456789+08:00"}
            for i in range(start, end)
        ]
        return {"items": items, "total": total, "page": page, "page_size": size}

    return fetch, calls


def _spec(**kwargs):
    base = es.ExportSpec(
        key="demo",
        label="示例",
        perm="user:view",
        target_type="user",
        fetch=kwargs.get("fetch"),
        columns=(
            es.Column("ID", "id"),
            es.Column("账号", "account"),
            es.Column("时间", "created_at", es._time),
        ),
    )
    return replace(base, **{k: v for k, v in kwargs.items() if k != "fetch"})


class CellSafetyTests(unittest.TestCase):
    def test_formula_prefix_is_neutralized(self):
        """CSV 注入：以 = + - @ 开头的内容必须前置单引号，否则 Excel 当公式执行。"""
        for raw in ("=cmd|' /c calc'!A0", "+1+1", "-2+3", "@SUM(A1)"):
            with self.subTest(raw=raw):
                cell = es._cell(raw)
                self.assertTrue(cell.startswith("'"), cell)
                self.assertEqual(cell[1:], raw)

    def test_normal_text_untouched(self):
        self.assertEqual(es._cell("普通标题"), "普通标题")
        self.assertEqual(es._cell(None), "")
        self.assertEqual(es._cell(True), "是")
        self.assertEqual(es._cell(False), "否")

    def test_newlines_flattened(self):
        """多行内容会破坏「一行一条记录」，必须压平。"""
        self.assertEqual(es._cell("第一行\n第二行"), "第一行 第二行")
        self.assertEqual(es._cell("a\r\nb\rc"), "a b c")

    def test_time_formatted_for_excel(self):
        self.assertEqual(es._time("2026-10-30T01:02:03.456789+08:00"), "2026-10-30 01:02:03")
        self.assertEqual(es._time("2026-10-30T01:02:03Z"), "2026-10-30 01:02:03")
        self.assertEqual(es._time(None), "")


class FilterWhitelistTests(unittest.TestCase):
    def test_only_declared_filters_pass_through(self):
        spec = replace(
            es.RESOURCES["users"], filters=("role", "keyword")
        )
        picked = es.pick_filters(spec, {"role": "admin", "keyword": "a", "evil": "x", "page": 2})
        self.assertEqual(picked, {"role": "admin", "keyword": "a"})

    def test_empty_values_dropped(self):
        spec = replace(es.RESOURCES["users"], filters=("role", "keyword"))
        self.assertEqual(es.pick_filters(spec, {"role": "", "keyword": None}), {})


class StreamCsvTests(unittest.TestCase):
    def test_bom_header_and_rows(self):
        fetch, _ = _fake_fetch_factory(2)
        spec = _spec(fetch=fetch)

        with mock.patch.object(es, "async_session", lambda: _FakeSession()):
            text = _collect(es.stream_csv(spec, {}))

        # Excel 打开 UTF-8 CSV 必须有 BOM，否则中文乱码
        self.assertTrue(text.startswith("\ufeff"))
        rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
        self.assertEqual(rows[0], ["ID", "账号", "时间"])
        self.assertEqual(rows[1], ["0", "user0", "2026-10-30 01:02:03"])
        self.assertEqual(len(rows), 3)

    def test_paging_stops_at_total(self):
        fetch, calls = _fake_fetch_factory(1200)
        spec = _spec(fetch=fetch)

        with mock.patch.object(es, "async_session", lambda: _FakeSession()):
            text = _collect(es.stream_csv(spec, {}))

        rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
        self.assertEqual(len(rows), 1 + 1200)
        # 1200 行 / 每批 500 → 3 页，不能多查一次空页
        self.assertEqual([c[0] for c in calls], [1, 2, 3])

    def test_truncation_is_announced_in_file(self):
        """截断必须写在文件里：否则拿到文件的人以为这就是全量。"""
        fetch, _ = _fake_fetch_factory(500)
        spec = _spec(fetch=fetch)

        with mock.patch.object(es, "async_session", lambda: _FakeSession()), mock.patch.object(
            es, "MAX_ROWS", 100
        ):
            text = _collect(es.stream_csv(spec, {}))

        rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
        data_rows = [r for r in rows if r and r[0].isdigit()]
        self.assertEqual(len(data_rows), 100)
        self.assertTrue(
            any("已截断" in (r[0] if r else "") for r in rows),
            "截断时必须在末尾明确写出提示",
        )

    def test_injection_in_real_column_is_escaped(self):
        async def fetch(db, page, size, filters):
            return {"items": [{"id": 1, "account": '=HYPERLINK("http://x")'}], "total": 1}

        spec = _spec(fetch=fetch)
        with mock.patch.object(es, "async_session", lambda: _FakeSession()):
            text = _collect(es.stream_csv(spec, {}))

        rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
        self.assertTrue(rows[1][1].startswith("'="), rows[1])


class RegistryTests(unittest.TestCase):
    def test_unknown_resource_raises(self):
        with self.assertRaises(KeyError):
            es.get_spec("not-a-resource")

    def test_filename_has_key_and_timestamp(self):
        name = es.filename(es.RESOURCES["users"])
        self.assertTrue(name.startswith("users-"))
        self.assertTrue(name.endswith(".csv"))

    def test_every_spec_perm_is_a_real_permission_code(self):
        """导出权限必须是真的存在的权限码，否则规则表写了也永远 403。"""
        from app.services.rbac_catalog import ALL_PERMISSION_CODES

        for key, spec in es.RESOURCES.items():
            with self.subTest(resource=key):
                self.assertIn(spec.perm, ALL_PERMISSION_CODES)

    def test_routes_are_registered_fail_closed(self):
        from app.core.rbac import match_route_permission

        for key, spec in es.RESOURCES.items():
            with self.subTest(resource=key):
                perms = match_route_permission("GET", f"/api/v1/admin/export/{key}")
                self.assertEqual(perms, [spec.perm])

    def test_disabled_module_blocks_export(self):
        """关掉积分模块后，流水也不该能导出（否则开关只是藏了菜单）。"""
        from app import module_registry

        self.assertEqual(
            module_registry.module_for_path("/api/v1/admin/export/point-transactions").key,
            "points",
        )
        from app.config import settings

        with mock.patch.object(settings, "ENABLED_MODULES", "core,tutorial,resource"):
            self.assertFalse(
                module_registry.is_path_enabled("/api/v1/admin/export/point-transactions")
            )
            self.assertTrue(module_registry.is_path_enabled("/api/v1/admin/export/users"))


class RecordExportTests(unittest.IsolatedAsyncioTestCase):
    """`record_export` 是导出链路上唯一会写库的一步，必须真的落审计。

    这条用例来自真环境：`record_admin_action` 是**同步**函数，
    最初写成 `await record_admin_action(...)` → 导出接口 500，而单测全绿
    （测试只覆盖了 CSV 生成，没碰审计）。下面用「传 MagicMock 给它」来钉住同步性：
    如果又写成 await，对 MagicMock 求值会直接抛 TypeError。
    """

    async def test_writes_audit_then_commits(self):
        from app.services import export_service as svc

        spec = svc.RESOURCES["users"]
        db = mock.Mock()
        db.commit = mock.AsyncMock()
        admin = mock.Mock()

        with mock.patch.object(svc, "record_admin_action") as record:
            await svc.record_export(db, admin, spec, {"role": "admin"}, 42)

        record.assert_called_once()
        args, kwargs = record.call_args
        self.assertEqual(args[2], "data_export")
        self.assertEqual(kwargs["target_type"], "user")
        self.assertIn("42", kwargs["summary"])
        self.assertEqual(kwargs["detail"]["rows"], 42)
        db.commit.assert_awaited_once()


class AuditTests(unittest.TestCase):
    def test_export_action_is_registered(self):
        """未登记的动作码只会打 warning，筛选下拉里看不到 —— 等于隐性丢失可追溯性。"""
        from app.models.admin_audit_log import AUDIT_ACTIONS, AUDIT_TARGET_TYPE_LABELS

        self.assertIn("data_export", AUDIT_ACTIONS)
        for key, spec in es.RESOURCES.items():
            with self.subTest(resource=key):
                # 复用已有的单数类型，否则审计里同一对象会出现两个代码
                self.assertIn(spec.target_type, AUDIT_TARGET_TYPE_LABELS)


if __name__ == "__main__":
    unittest.main()
