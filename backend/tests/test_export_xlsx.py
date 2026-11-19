"""Excel 导出与指标快照导出契约测试。

钉住三条**破了不报错、只会悄悄失去意义**的契约：

1. **xlsx 行集与 CSV 一致**：两种格式走同一 spec.fetch —— Excel 里少一行
   不是格式差异，是口径漂移。
2. **xlsx 单元格防注入**：`= + - @` 开头的文本同样前置单引号（xlsx 与 CSV
   的注入面一致）；数字保留为数字（可排序可求和）。
3. **快照导出列与 METRIC_DEFS 同源**：加指标不加列 = 归档缺字段。
"""

from __future__ import annotations

import io
import unittest
from types import SimpleNamespace
from unittest import mock

from openpyxl import load_workbook

from app.services import export_service as es


class XlsxBuildTests(unittest.TestCase):
    def _make_spec(self):
        from app.services.export_service import Column, ExportSpec

        return ExportSpec(
            key="demo",
            label="示例",
            perm="dashboard:view",
            target_type="demo",
            fetch=None,
            columns=(
                Column("名称", "name"),
                Column("数量", "count"),
                Column("时间", "created_at", es._time),
            ),
        )


    def test_xlsx_rows_match_pages(self):
        spec = self._make_spec()
        raw = _run_build(spec, [
            {"items": [
                {"name": "甲", "count": 5, "created_at": "2026-11-19T01:00:00"},
                {"name": "乙", "count": 8, "created_at": "2026-11-19T02:00:00"},
            ], "total": 2},
        ])
        wb = load_workbook(io.BytesIO(raw), read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        self.assertEqual(rows[0], ("名称", "数量", "时间"))
        self.assertEqual(rows[1], ("甲", 5, es._time("2026-11-19T01:00:00")))
        self.assertEqual(len(rows), 3)
        # 数字保留为数字（不是文本 "5"）
        self.assertIsInstance(rows[1][1], int)

    def test_xlsx_neutralizes_formula_injection(self):
        spec = self._make_spec()
        raw = _run_build(spec, [
            {"items": [{"name": "=1+1", "count": 1, "created_at": None}], "total": 1},
        ])
        wb = load_workbook(io.BytesIO(raw), read_only=True)
        rows = list(wb.active.iter_rows(values_only=True))
        self.assertTrue(str(rows[1][0]).startswith("'"), "xlsx 文本单元格同样要防公式注入")

    def test_xlsx_truncation_note(self):
        spec = self._make_spec()
        big_page = {"items": [
            {"name": f"n{i}", "count": i, "created_at": None}
            for i in range(es.MAX_ROWS)
        ], "total": es.MAX_ROWS}
        raw = _run_build(spec, [big_page])
        wb = load_workbook(io.BytesIO(raw), read_only=True)
        rows = list(wb.active.iter_rows(values_only=True))
        # 表头 + MAX_ROWS 行 + 截断说明行
        self.assertEqual(len(rows), es.MAX_ROWS + 2)
        self.assertIn("截断", str(rows[-1][0]))


def _run_build(spec, pages):
    """build_xlsx 自开 session：patch async_session 返回假会话"""
    import asyncio
    from types import SimpleNamespace
    from unittest import mock

    async def _run():
        with mock.patch.object(es, "async_session", lambda: _NullCtx()):
            return await es.build_xlsx(spec, {})

    return asyncio.run(_run())


class _NullCtx:
    """假 async session 上下文：build_xlsx 的分页查询全部走 mock 过的 fetch"""

    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, *args):
        return False


class OpsMetricsExportTests(unittest.TestCase):
    def test_spec_columns_track_metric_defs(self):
        from app.services.ops_metric_service import METRIC_DEFS

        spec = es.get_spec("ops-metrics")
        labels = [c.label for c in spec.columns]
        self.assertEqual(labels[0], "采样时间")
        self.assertEqual(labels[1:], [str(d["label"]) for d in METRIC_DEFS.values()])

    def test_spec_perm_is_dashboard_view(self):
        spec = es.get_spec("ops-metrics")
        self.assertEqual(spec.perm, "dashboard:view")
        self.assertEqual(spec.key, "ops-metrics")
        self.assertEqual(spec.target_type, "ops_metric")


if __name__ == "__main__":
    unittest.main()


def _run_build(spec, pages):
    """驱动 build_xlsx：dataclasses.replace 换 fetch（frozen dataclass 不可原处赋值），
    async_session 返回假上下文（fetch 已替换，不触库）。

    pages: 每页一个 {"items": [...], "total": n} 字典，按调用顺序返回。
    """
    import asyncio
    import dataclasses

    pages_iter = iter(pages)

    async def _fetch(db, page, size, filters):
        try:
            return next(pages_iter)
        except StopIteration:
            return {"items": [], "total": 0}

    new_spec = dataclasses.replace(spec, fetch=_fetch)

    async def _scenario():
        with mock.patch.object(es, "async_session", lambda: _NullCtx()):
            return await es.build_xlsx(new_spec, {})

    return asyncio.run(_scenario())


class _NullCtx:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, *args):
        return False
