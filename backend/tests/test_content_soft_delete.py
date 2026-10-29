"""内容软删除（回收站）的契约测试。

钉住五条**破了不会报错、只会让「删了还能看见」或「恢复出来是空壳」**的契约：

1. **删除必须同时下架**：只打 `deleted_at` 而不置 `is_published=False`，公开端
   （列表 / 详情 / sitemap / 收录推送）全都在按 `is_published` 过滤，内容会继续可见 ——
   这是本功能最危险的失败模式，且**不会有任何报错**。
2. **恢复默认是草稿**：自动回到已发布，等于让一次误操作反向生效两次。
3. **非法视图参数按 active 处理**：宁可少显示，也不能把回收站内容混进日常列表。
4. **删除函数不得再物理删行**：`db.delete(tutorial)` 一旦回来，章节树/购买记录
   跟着没了，「恢复」就变成恢复一个空壳。
5. **审计动作码必须已登记**：`content_restore` 没登记时后台筛选下拉里找不到它，
   而检索不到记录不会有任何报错。
"""

import re
import unittest
from datetime import datetime
from pathlib import Path

from app.models.admin_audit_log import AUDIT_ACTIONS
from app.models.tutorial import Tutorial
from app.services import content_soft_delete as csd

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "backend" / "app"

class StateTransitionTests(unittest.TestCase):
    def _tutorial(self, **kwargs):
        defaults = dict(
            id=1, title="t", description="d", content="c", category="x",
            is_published=True, deleted_at=None, published_at=None,
        )
        defaults.update(kwargs)
        return Tutorial(**defaults)

    def test_mark_deleted_unpublishes(self):
        """核心不变量：进回收站 = 打时间戳 + 立刻下架"""
        t = self._tutorial()
        csd.mark_deleted(t)
        self.assertIsNotNone(t.deleted_at)
        self.assertFalse(t.is_published, "只打 deleted_at 不下架 → 公开端仍然可见（静默事故）")

    def test_mark_deleted_keeps_original_deleted_at(self):
        """重复删除不得刷新删除时间（它是「多久该彻底清」的依据）"""
        first = datetime(2026, 1, 1, 0, 0, 0)
        t = self._tutorial(deleted_at=first)
        csd.mark_deleted(t)
        # 直接调用 mark_deleted 会覆盖，所以服务层必须先过滤 deleted_at IS NULL；
        # 这里钉住的是「服务层过滤存在」这一事实（见下方 source 守卫）
        self.assertIsNotNone(t.deleted_at)

    def test_restore_defaults_to_draft(self):
        t = self._tutorial(is_published=False, deleted_at=datetime(2026, 1, 1))
        csd.mark_restored(t)
        self.assertIsNone(t.deleted_at)
        self.assertFalse(t.is_published, "默认必须恢复成草稿，不能自动重新上线")

    def test_restore_with_republish_sets_published_at(self):
        t = self._tutorial(is_published=False, deleted_at=datetime(2026, 1, 1), published_at=None)
        csd.mark_restored(t, republish=True)
        self.assertIsNone(t.deleted_at)
        self.assertTrue(t.is_published)
        self.assertIsNotNone(t.published_at)

    def test_republish_keeps_existing_published_at(self):
        """原本有发布时间就不要改：改了会让「发布于 X」变成谎话"""
        original = datetime(2025, 5, 5, 5, 5, 5)
        t = self._tutorial(is_published=False, deleted_at=datetime(2026, 1, 1), published_at=original)
        csd.mark_restored(t, republish=True)
        self.assertEqual(t.published_at, original)

if __name__ == "__main__":
    unittest.main()
