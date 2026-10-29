"""「插件 → 扩展」改名的回归守卫（缺口台账 P2-2）。

产品决策把栏目名统一为「扩展」。这次改名最危险的地方**不是文案，而是数据**：
分类筛选按钮的文案来自数据库（`categories.name`），插件卡片的图标/配色则是
**按分类值查表**得到的（`PLUGIN_CARD_META[category]`）。也就是说存在两个
「改一处忘另一处」的静默失效：

1. 迁移把分类值改成「Coze 扩展」，但前端 `PLUGIN_CARD_META` 的 key 还是「Coze 插件」
   → **卡片全部落回默认样式**（不报错、不白屏，只是颜色图标不对，最难被发现）；
2. 三端（PC / 管理端 / 小程序）的兜底分类常量各写一份，改了两端漏一端
   → 小程序筛选按钮还显示旧名。

因此本文件把三条契约钉成会失败的断言：
- 迁移的新分类名不含「插件」，且旧名确实含「插件」（防止把方向写反）；
- 三端分类常量集合一致且不含「插件」；
- 样式元数据的 key 集合 == 分类值集合（两端都要对得上）。
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_DIR = REPO_ROOT / "backend" / "alembic" / "versions"

RENAME_MIGRATION = VERSIONS_DIR / "20261009_0033_rename_plugin_to_extension.py"
PC_CONSTANTS = REPO_ROOT / "frontend" / "src" / "utils" / "constants.ts"
ADMIN_CONSTANTS = REPO_ROOT / "admin-frontend" / "src" / "constants" / "index.ts"
MINI_CONSTANTS = REPO_ROOT / "miniprogram" / "utils" / "constants.js"

OLD_WORD = "插件"
NEW_WORD = "扩展"

# 期望的扩展分类集合（不含 'all'）——三端与样式元数据都必须与之一致
EXPECTED_CATEGORIES = {"ComfyUI 节点", "Coze 扩展", "其他扩展"}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _block_after(text: str, marker: str, end_char: str) -> str:
    """取出 marker 之后、到第一个「行首即 end_char」为止的代码块。"""
    start = text.find(marker)
    if start == -1:
        raise AssertionError(f"未找到标记: {marker}")
    lines = text[start:].splitlines()
    collected = []
    for line in lines[1:]:
        if line.strip().startswith(end_char):
            break
        collected.append(line)
    return "\n".join(collected)


def _quoted_keys(block: str, field: str | None = None) -> set[str]:
    """抽取块内被引号包裹的值：field 为 None 时取 `'xxx':`（对象 key），
    否则取 `field: 'xxx'`（对象字段值）。"""
    if field is None:
        return set(re.findall(r"'([^']+)'\s*:", block))
    return set(re.findall(rf"{field}:\s*'([^']+)'", block))


class RenameMigrationTests(unittest.TestCase):
    def test_migration_exists(self):
        self.assertTrue(RENAME_MIGRATION.exists(), f"缺少改名迁移: {RENAME_MIGRATION}")

    def test_rename_direction_is_correct(self):
        """旧名必须含「插件」、新名必须不含——写反了会把「扩展」改成「插件」"""
        text = _read(RENAME_MIGRATION)
        pairs = re.findall(r'\("([^"]+)",\s*"([^"]+)"\)', text)
        self.assertTrue(pairs, "迁移里没有解析到 (旧名, 新名) 对")
        for old, new in pairs:
            self.assertIn(OLD_WORD, old, f"旧名 {old} 不含「{OLD_WORD}」，方向可能写反")
            self.assertNotIn(OLD_WORD, new, f"新名 {new} 仍含「{OLD_WORD}」")

    def test_migration_touches_both_category_and_plugins(self):
        """分类值与插件行必须一起改：只改一边会出现「筛选按钮叫扩展、卡片标签叫插件」"""
        text = _read(RENAME_MIGRATION)
        self.assertIn("UPDATE categories", text)
        self.assertIn("UPDATE plugins", text)


class CategoryConstantParityTests(unittest.TestCase):
    """三端兜底常量必须一致（它们是接口失败时的显示来源）"""

    def test_pc_categories(self):
        block = _block_after(_read(PC_CONSTANTS), "PLUGIN_CATEGORIES_FALLBACK", "]")
        keys = _quoted_keys(block, "key") - {"all"}
        self.assertEqual(keys, EXPECTED_CATEGORIES)

    def test_admin_categories(self):
        block = _block_after(_read(ADMIN_CONSTANTS), "export const PLUGIN_CATEGORIES", "]")
        values = _quoted_keys(block, "value")
        self.assertEqual(values, EXPECTED_CATEGORIES)

    def test_miniprogram_categories(self):
        block = _block_after(_read(MINI_CONSTANTS), "const PLUGIN_CATEGORIES", "]")
        keys = _quoted_keys(block, "key") - {"all"}
        self.assertEqual(keys, EXPECTED_CATEGORIES)

    def test_no_legacy_word_in_constant_files(self):
        """常量文件里不允许再出现「插件」——出现即说明有地方漏改"""
        for path in (PC_CONSTANTS, ADMIN_CONSTANTS, MINI_CONSTANTS):
            text = _read(path)
            self.assertNotIn(
                OLD_WORD,
                text,
                f"{path.relative_to(REPO_ROOT)} 仍含「{OLD_WORD}」",
            )


class CardMetaKeyParityTests(unittest.TestCase):
    """样式元数据的 key 必须与分类值逐一对应。

    对不上的后果是「卡片全部落回默认样式」：不报错、不白屏，
    只是图标与配色不对——这是本次改名最容易漏掉且最难发现的静默回归。
    """

    def test_pc_meta_keys_match_categories(self):
        text = _read(PC_CONSTANTS)
        block = _block_after(text, "PLUGIN_CARD_META", "}")
        self.assertEqual(_quoted_keys(block), EXPECTED_CATEGORIES)

    def test_miniprogram_meta_keys_match_categories(self):
        text = _read(MINI_CONSTANTS)
        block = _block_after(text, "PLUGIN_CATEGORY_META", "}")
        self.assertEqual(_quoted_keys(block), EXPECTED_CATEGORIES)


if __name__ == "__main__":
    unittest.main()
