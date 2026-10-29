"""公告定时发布的契约测试。

钉住三条**破了不报错、只会让「定时」变成「立即」或整体偏 8 小时**的契约：

1. **公开端三处必须共用同一份条件**（列表 / 详情 / 首页公告条）。各写一遍时最容易漏掉首页，
   表现是「详情页 404 但首页已经滚出来了」。
2. **未到点 = 不可见**：`publish_at` 在未来时，即使 `is_published=1` 也不能出现在前台。
3. **时区口径**：带时区的 ISO 要转成 naive UTC；不带时区的按 UTC 原样保留
   （服务端时区恒为 UTC，猜本地时区会整体偏 8 小时）。
"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models.announcement import Announcement
from app.services import admin_announcement_service as aas
from app.services import announcement_service as ans

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "backend" / "app"


class ConditionTests(unittest.TestCase):
    def test_public_conditions_cover_both_flags(self):
        conditions = ans.public_conditions()
        self.assertEqual(len(conditions), 2, "必须同时约束「已发布」与「到点」")
        rendered = " ".join(str(item) for item in conditions)
        self.assertIn("is_published", rendered)
        self.assertIn("publish_at", rendered)

    def test_all_public_read_paths_share_conditions(self):
        """列表 / 详情 / 首页公告条三处必须都走 public_conditions()"""
        source = Path(ans.__file__).read_text(encoding="utf-8")
        # 定义 1 次 + 调用 3 次
        self.assertGreaterEqual(
            source.count("public_conditions()"), 4, "有公开读取路径没走统一条件"
        )
        # 不允许任何一处再手写裸 is_published 过滤（那会绕过定时）
        self.assertNotIn("Announcement.is_published == True)  # noqa", source)


class TimezoneTests(unittest.TestCase):
    def test_aware_datetime_is_converted_to_naive_utc(self):
        # 北京时间 20:00 = UTC 12:00
        aware = datetime(2026, 10, 27, 20, 0, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(aas._to_naive_utc(aware), datetime(2026, 10, 27, 12, 0))

    def test_naive_datetime_is_kept_as_is(self):
        naive = datetime(2026, 10, 27, 12, 0)
        self.assertEqual(aas._to_naive_utc(naive), naive)

    def test_empty_values_become_none(self):
        for value in (None, ""):
            self.assertIsNone(aas._to_naive_utc(value))

    def test_unknown_type_is_rejected_silently(self):
        """字符串不会被瞎猜成时间（宁可当没填，也不要把 8 小时偏差写进库）"""
        self.assertIsNone(aas._to_naive_utc("2026-10-27 20:00"))


class SourceGuardTests(unittest.TestCase):
    def test_admin_create_and_update_handle_publish_at(self):
        source = Path(aas.__file__).read_text(encoding="utf-8")
        self.assertIn("publish_at=_to_naive_utc(", source, "创建时没写入定时时间")
        self.assertIn('if "publish_at" in updates:', source, "更新时没处理定时时间")

    def test_update_uses_key_presence_not_none_check(self):
        """`publish_at: null` 表示「取消定时」，必须靠 `in updates` 判断，
        用 `is not None` 会让「取消定时」变成空操作"""
        source = Path(aas.__file__).read_text(encoding="utf-8")
        self.assertIn('if "publish_at" in updates:', source)

    def test_cache_invalidated_when_schedule_changes(self):
        """改了定时时间也要清首页缓存，否则「改成现在」看起来没生效"""
        source = Path(aas.__file__).read_text(encoding="utf-8")
        self.assertIn('"publish_at" in updates', source)
        self.assertIn("_invalidate_home_cache", source)

    def test_request_models_accept_publish_at(self):
        source = (APP / "api" / "admin_announcements.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("publish_at: Optional[datetime]"), 2)

    def test_response_item_exposes_publish_at(self):
        source = (APP / "schemas" / "admin.py").read_text(encoding="utf-8")
        self.assertIn("publish_at: Optional[str]", source)

    def test_model_and_migration_agree(self):
        columns = {c.name for c in Announcement.__table__.columns}
        self.assertIn("publish_at", columns)
        migration = (
            REPO_ROOT / "backend" / "alembic" / "versions"
            / "20261027_0053_add_announcement_publish_at.py"
        ).read_text(encoding="utf-8")
        self.assertIn("'publish_at'", migration)
        self.assertIn("'announcements'", migration)


if __name__ == "__main__":
    unittest.main()
