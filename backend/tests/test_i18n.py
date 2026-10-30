"""i18n 守卫（`app/core/i18n.py` + 菜单/权限名的英文文案）。

菜单与权限名由后端下发，所以「翻没翻全」只能在这里守。两个失效方式都是静默的：

1. **漏翻**：新增菜单/权限时忘了写英文 → 英文界面里孤零零一条中文，没人会发现；
2. **翻成空串**：英文缺失被当成 `""` 而不是回退中文 → 菜单出现空白项，点不到。

所以这里既断言「解析正确」，也断言「覆盖完整 + 永不返回空」。
"""

import unittest

from app.core.i18n import DEFAULT_LOCALE, pick, resolve_locale
from app.services.admin_menu import (
    MENU_FOOTER,
    MENU_GROUPS,
    MENU_LABELS_EN,
    menu_tree,
)
from app.services.rbac_catalog import (
    ACTION_NAMES_EN,
    MODULE_NAMES_EN,
    PERMISSION_CATALOG,
    permission_name_en,
    permissions_grouped_by_module,
)


class ResolveLocaleTests(unittest.TestCase):
    def test_defaults_to_chinese(self):
        self.assertEqual(resolve_locale(None), DEFAULT_LOCALE)
        self.assertEqual(resolve_locale(""), DEFAULT_LOCALE)

    def test_english_variants(self):
        for header in ("en", "en-US", "en-GB,en;q=0.9", "EN-us"):
            self.assertEqual(resolve_locale(header), "en", f"未识别: {header}")

    def test_chinese_variants(self):
        for header in ("zh", "zh-CN", "zh-Hans-CN", "zh_CN"):
            self.assertEqual(resolve_locale(header), "zh", f"未识别: {header}")

    def test_unsupported_falls_back_to_default(self):
        """只给了不支持的语言时不能返回空 locale，否则下游 pick 会拿到无意义值"""
        self.assertEqual(resolve_locale("fr-FR,de;q=0.8"), DEFAULT_LOCALE)

    def test_quality_value_wins(self):
        """q 值决定优先级：用户更偏好中文时不该被英文覆盖"""
        self.assertEqual(resolve_locale("en;q=0.3, zh-CN;q=0.9"), "zh")
        self.assertEqual(resolve_locale("zh;q=0.3, en;q=0.9"), "en")

    def test_malformed_quality_is_tolerated(self):
        self.assertEqual(resolve_locale("en;q=abc"), "en")


class PickTests(unittest.TestCase):
    def test_english_missing_falls_back_to_chinese(self):
        self.assertEqual(pick("en", "中文", None), "中文")
        self.assertEqual(pick("en", "中文", ""), "中文")

    def test_chinese_always_used_for_zh(self):
        self.assertEqual(pick("zh", "中文", "English"), "中文")


class MenuTranslationTests(unittest.TestCase):
    def test_every_menu_key_has_english(self):
        """漏翻不会报错，只会在英文界面里露出一条中文 —— 所以用测试兜住"""
        keys = [item.key for group in MENU_GROUPS for item in group.items]
        keys += [item.key for item in MENU_FOOTER]
        keys += [group.key for group in MENU_GROUPS]
        missing = sorted({key for key in keys if key not in MENU_LABELS_EN})
        self.assertEqual(missing, [], f"以下菜单缺英文文案: {missing}")

    def test_english_tree_has_no_chinese(self):
        tree = menu_tree(locale="en")
        labels = [g["label"] for g in tree["groups"]]
        labels += [i["label"] for g in tree["groups"] for i in g["items"]]
        labels += [i["label"] for i in tree["footer"]]
        self.assertTrue(labels)
        leftovers = [label for label in labels if any("\u4e00" <= ch <= "\u9fff" for ch in label)]
        self.assertEqual(leftovers, [], f"英文菜单里仍有中文: {leftovers}")

    def test_labels_are_never_empty(self):
        for locale in ("zh", "en"):
            tree = menu_tree(locale=locale)
            for group in tree["groups"]:
                self.assertTrue(group["label"].strip(), f"{locale} 分组标题为空: {group['key']}")
                for item in group["items"]:
                    self.assertTrue(item["label"].strip(), f"{locale} 菜单项文案为空: {item['key']}")

    def test_chinese_tree_is_unchanged(self):
        """默认语言必须与改造前逐字一致：存量中文站点不能因为引入 i18n 而变化"""
        tree = menu_tree(locale="zh")
        labels = {g["key"]: g["label"] for g in tree["groups"]}
        self.assertEqual(labels["overview"], "概览")
        self.assertEqual(labels["system"], "系统")
        items = {i["key"]: i["label"] for g in tree["groups"] for i in g["items"]}
        self.assertEqual(items["/tutorials"], "教程管理")
        self.assertEqual(items["/audit-logs"], "操作审计")


class PermissionTranslationTests(unittest.TestCase):
    def test_every_module_has_english(self):
        missing = sorted({p.module for p in PERMISSION_CATALOG} - set(MODULE_NAMES_EN))
        self.assertEqual(missing, [], f"以下权限模块缺英文: {missing}")

    def test_every_action_has_english(self):
        actions = {p.code.split(":")[1] for p in PERMISSION_CATALOG}
        missing = sorted(actions - set(ACTION_NAMES_EN))
        self.assertEqual(missing, [], f"以下权限动作缺英文: {missing}")

    def test_english_permission_names_have_no_chinese(self):
        """按「动作 + 模块」拼装，覆盖不到时会回退中文 —— 这里确保没有回退发生"""
        leftovers = [
            p.code for p in PERMISSION_CATALOG
            if any("\u4e00" <= ch <= "\u9fff" for ch in permission_name_en(p))
        ]
        self.assertEqual(leftovers, [], f"以下权限的英文名回退成了中文: {leftovers}")

    def test_english_tree_groups(self):
        groups = permissions_grouped_by_module("en")
        self.assertEqual(groups["tutorial"]["name"], "Tutorial")
        self.assertEqual(groups["tutorial"]["menu"]["name"], "View Tutorial")

    def test_chinese_tree_groups(self):
        groups = permissions_grouped_by_module("zh")
        self.assertEqual(groups["tutorial"]["name"], "教程管理")
        self.assertEqual(groups["tutorial"]["menu"]["name"], "教程查看")


if __name__ == "__main__":
    unittest.main()
