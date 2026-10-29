"""富文本 XSS 出口的守卫测试（第三轮增量审查 2026-10-11 / 2.1）。

背景
----
`sanitize_content` 是**全部富文本字段的唯一 XSS 出口**（公告 / 教程 / 资源 / 扩展 /
节点 / 反馈共 11 处调用）。但变异实验实测：把它改成 `return content`
（即整个 XSS 防线失效）→ **551 个用例全绿**，一个都没变红。

「零测试」比「没有测试」更危险：它给出的是虚假的安全感。本文件把该出口钉住。

断言值均为**实测所得**（nh3/ammonia 的真实输出），不是凭想象写的：
- 非白名单标签（script/iframe）整段丢弃；
- 事件属性（onclick/onerror）被剥离，标签本身保留；
- `javascript:` href 被丢弃并补 `rel="noopener noreferrer"`；
- `data:` URL 只保留位图格式，`text/html` 与 `image/svg+xml` 被中和
  （含**实体编码变体** `data&#58;text/html`，那是预处理正则看不见、只能靠后过滤兜底的路径）；
- 编辑器往返所需的 `<col width>` / `<td colspan>` 必须保留，否则表格列宽与合并单元格会丢。
"""

import unittest

from app.utils.sanitize import (
    escape_like,
    sanitize_content,
    sanitize_plain_text,
)


class SanitizeContentTests(unittest.TestCase):
    # (输入, 期望输出) —— 逐条实测
    CASES = [
        # ---- 标签白名单 ----
        ("<p>ok</p><script>alert(1)</script>", "<p>ok</p>"),
        ('<iframe src="https://evil.com"></iframe>', ""),
        ("<style>body{display:none}</style>", ""),
        ("<p onclick=\"x()\">t</p>", "<p>t</p>"),
        # ---- 属性白名单 / 事件属性 ----
        ("<img src=x onerror=alert(1)>", '<img src="x">'),
        # ---- 链接：危险协议被丢弃，且强制补 rel ----
        ('<a href="javascript:alert(1)">x</a>', '<a rel="noopener noreferrer">x</a>'),
        (
            '<a href="https://a.com" target="_blank">x</a>',
            '<a href="https://a.com" target="_blank" rel="noopener noreferrer">x</a>',
        ),
        # ---- data: URL：只放行位图 ----
        (
            '<a href="data:text/html,<script>alert(1)</script>">x</a>',
            '<a href="" rel="noopener noreferrer">x</a>',
        ),
        # 实体编码变体：预处理正则看不见（`&#58;` 不是字面冒号），
        # 靠 nh3 解码后的**后过滤**兜底 —— 这条是防绕过的关键用例
        (
            '<a href="data&#58;text/html,<b>x</b>">x</a>',
            '<a href="" rel="noopener noreferrer">x</a>',
        ),
        (
            '<img src="data:image/svg+xml;base64,PHN2Zz48c2NyaXB0Pjwvc2NyaXB0Pjwvc3ZnPg==">',
            '<img src="">',
        ),
        (
            '<img src="data:image/png;base64,iVBORw0KGgo=">',
            '<img src="data:image/png;base64,iVBORw0KGgo=">',
        ),
        # ---- 编辑器往返：表格列宽与合并单元格必须保留 ----
        (
            '<table><colgroup><col width="100"></colgroup>'
            '<tr><td colspan="2">a</td></tr></table>',
            '<table><colgroup><col width="100"></colgroup>'
            '<tbody><tr><td colspan="2">a</td></tr></tbody></table>',
        ),
    ]

    def test_whitelist_and_neutralisation(self):
        for raw, expected in self.CASES:
            with self.subTest(raw=raw[:48]):
                self.assertEqual(sanitize_content(raw), expected)

    def test_no_dangerous_marker_survives(self):
        """黑盒兜底：任何输入都不该在输出里留下可执行标记"""
        for raw, _expected in self.CASES:
            with self.subTest(raw=raw[:48]):
                out = sanitize_content(raw)
                for marker in ("<script", "javascript:", "onerror=", "onload=",
                               "data:text/html", "data:image/svg+xml"):
                    self.assertNotIn(marker, out)

    def test_falsy_passthrough(self):
        """None / 空串原样返回，避免写库时把 None 变成 "" """
        self.assertIsNone(sanitize_content(None))
        self.assertEqual(sanitize_content(""), "")


class SanitizePlainTextTests(unittest.TestCase):
    def test_all_tags_stripped(self):
        self.assertEqual(sanitize_plain_text("<b>x</b><script>1</script>"), "x")

    def test_falsy_passthrough(self):
        self.assertIsNone(sanitize_plain_text(None))
        self.assertEqual(sanitize_plain_text(""), "")


class EscapeLikeTests(unittest.TestCase):
    """LIKE 通配符转义（防 LIKE 注入把用户输入当通配符匹配全表）"""

    def test_wildcards_escaped(self):
        self.assertEqual(escape_like("100%"), "100\\%")
        self.assertEqual(escape_like("a_b"), "a\\_b")
        self.assertEqual(escape_like("c\\d"), "c\\\\d")

    def test_plain_text_untouched(self):
        self.assertEqual(escape_like("普通关键词"), "普通关键词")


if __name__ == "__main__":
    unittest.main()
