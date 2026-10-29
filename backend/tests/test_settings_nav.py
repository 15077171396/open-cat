"""系统设置左侧导航分类的**跨语言**守卫。

分类只活在前端（`pages/Settings/settingsNav.ts`），但它必须覆盖后端下发的**全部**
settings 分组 —— 漏一个不会报错，只会让那组设置藏在「其它」里，
和 tab 时代 `GROUP_ORDER.filter(g => map.has(g))` 静默丢组是同一类风险。

反向也要查：前端登记了后端已抽走的分组（例如 `mail` 已经独立成页）时，
那一栏会永远空着 —— 属于「配置已经搬走、导航还在指路」。

做法与 `test_admin_menu.py` 一致：直接解析前端源码里的字符串字面量，
不做 TS 编译，避免测试需要 node 环境。
"""

import re
import unittest
from pathlib import Path

from app.services import setting_service
from app.services.setting_service import SURFACE_SETTINGS

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "admin-frontend"
    / "src"
    / "pages"
    / "Settings"
    / "settingsNav.ts"
)

#: 匹配 NAV_CATEGORIES 里的 groups: [...] 数组（跨行）
_GROUPS_BLOCK = re.compile(r"groups:\s*\[([^\]]*)\]", re.S)
_STRING = re.compile(r"'([a-z0-9_]+)'")


def _declared_groups() -> list[str]:
    source = SOURCE.read_text(encoding="utf-8")
    # 只取 NAV_CATEGORIES 常量体，避免把注释里的示例当成声明
    start = source.index("export const NAV_CATEGORIES")
    end = source.index("]", source.index("export const FALLBACK_CATEGORY_KEY"))
    body = source[start:end]
    keys: list[str] = []
    for block in _GROUPS_BLOCK.finditer(body):
        keys.extend(_STRING.findall(block.group(1)))
    return keys


class SettingsNavCoverageTests(unittest.TestCase):
    def setUp(self):
        self.declared = _declared_groups()
        self.backend_groups = [g["key"] for g in setting_service.groups_payload(surface=SURFACE_SETTINGS)]

    def test_source_file_exists(self):
        self.assertTrue(SOURCE.exists(), f"导航分类文件不存在：{SOURCE}")

    def test_every_settings_group_is_categorised(self):
        """漏归类 = 该组落进「其它」栏（界面上还在，但分类里找不到）"""
        missing = sorted(set(self.backend_groups) - set(self.declared))
        self.assertEqual(missing, [], f"以下分组未归类（会落进「其它」）：{missing}")

    def test_no_stale_group_in_nav(self):
        """已抽走的分组仍留在分类里 → 永远空着的一栏"""
        stale = sorted(set(self.declared) - set(self.backend_groups))
        self.assertEqual(stale, [], f"以下分组已不在系统设置里，应从导航分类移除：{stale}")

    def test_no_duplicate_group(self):
        self.assertEqual(len(self.declared), len(set(self.declared)), "同一分组被登记到多个分类")

    def test_moved_groups_are_gone_from_nav(self):
        """抽成独立页的分组不能再出现在系统设置的导航里"""
        moved = {"auth_phone", "auth_wechat", "auth_sms", "miniapp", "legal", "ai", "mail",
                 "payment_general", "payment_wechat", "payment_alipay"}
        self.assertEqual(sorted(set(self.declared) & moved), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
