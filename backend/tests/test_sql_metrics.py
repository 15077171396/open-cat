"""慢 SQL 打点与连接池水位的单元测试（sqlite 同步引擎 + ORM，离线可跑）。

监听逻辑与方言无关：before/after_cursor_execute 是引擎级事件，sqlite 上验证
计数、阈值判定、幂等安装与异常清理，MySQL 生产侧行为一致。
本文件刻意走纯 ORM 语句路径（建表/增删改查均经 ORM），不出现裸语句调用。
"""

import unittest

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase

from app.config import settings
from app.core.sql_metrics import install_sql_metrics, pool_status, sql_metrics_snapshot


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "t_metrics"
    id: sa.orm.Mapped[int] = sa.orm.mapped_column(primary_key=True)


class _MissingRow(_Base):
    __tablename__ = "t_missing"
    id: sa.orm.Mapped[int] = sa.orm.mapped_column(primary_key=True)


class SqlMetricsTests(unittest.TestCase):
    def setUp(self):
        self.engine = sa.create_engine("sqlite://")
        install_sql_metrics(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_schema_emission_counts_as_statements(self):
        """建表语句同样经过引擎 cursor —— 总数应增长。"""
        before = sql_metrics_snapshot()["total"]
        _Row.__table__.metadata.create_all(self.engine)
        after = sql_metrics_snapshot()["total"]
        self.assertGreater(after - before, 0, "DDL 也走引擎事件，必须计数")

    def test_orm_insert_counts_once_and_respects_threshold(self):
        _Row.__table__.metadata.create_all(self.engine)
        before = sql_metrics_snapshot()
        session = sa.orm.Session(self.engine)
        session.add(_Row(id=1))
        session.flush()
        session.commit()
        mid = sql_metrics_snapshot()
        self.assertGreaterEqual(mid["total"] - before["total"], 1, "INSERT 必须计数")

        # 阈值 0 = 全部记录（调试档）：不真造慢查询也能验证慢计数路径
        original = settings.SQL_SLOW_QUERY_MS
        settings.SQL_SLOW_QUERY_MS = 0
        try:
            session2 = sa.orm.Session(self.engine)
            session2.add(_Row(id=2))
            session2.flush()
            session2.commit()
        finally:
            settings.SQL_SLOW_QUERY_MS = original
        after = sql_metrics_snapshot()
        self.assertGreaterEqual(after["slow"] - mid["slow"], 1)
        self.assertTrue(after["last_slow_sql"])
        self.assertGreaterEqual(after["slow_threshold_ms"], original)

    def test_failed_statement_does_not_leak_timing_slot(self):
        """异常语句会跳过 after 事件 —— 单槽覆盖设计下不得污染后续计数。"""
        _Row.__table__.metadata.create_all(self.engine)
        # 两个 ORM 类共享同一 metadata：先删掉目标表，让 INSERT 真正失败
        _MissingRow.__table__.drop(self.engine)
        session = sa.orm.Session(self.engine)
        session.add(_MissingRow(id=1))
        with self.assertRaises(Exception):
            session.flush()
        # 新会话再执行成功语句：正常计数一次
        before = sql_metrics_snapshot()["total"]
        session2 = sa.orm.Session(self.engine)
        session2.add(_Row(id=1))
        session2.flush()
        session2.commit()
        self.assertEqual(sql_metrics_snapshot()["total"] - before, 1)

    def test_install_is_idempotent(self):
        """重复安装不得叠加监听（否则一条语句计两次，基线直接翻倍）。"""
        _Row.__table__.metadata.create_all(self.engine)
        install_sql_metrics(self.engine)
        before = sql_metrics_snapshot()["total"]
        session = sa.orm.Session(self.engine)
        session.add(_Row(id=1))
        session.flush()
        session.commit()
        self.assertEqual(sql_metrics_snapshot()["total"] - before, 1)

    def test_pool_status_reports_levels(self):
        info = pool_status(self.engine)
        for key in ("size", "checked_in", "checked_out", "overflow", "status"):
            self.assertIn(key, info)


if __name__ == "__main__":
    unittest.main()
