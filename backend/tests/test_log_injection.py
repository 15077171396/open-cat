"""日志注入防护（缺口台账 P2-50⑤）。

loguru 的**文件 sink 用 `serialize=True`**，输出 JSON：换行被转义、字段成对，注入无效。
但 **console/stderr sink 是纯文本**，也就是 `docker logs` 与 journald 实际读到的东西。
把用户可控串直接插进 f-string，一条含 `\\n` 的输入就能凭空多出一整行
「用户 1 登录成功」——伪造的是**取证时间线本身**，而 `core/request_context.py`
那套关联 ID 的全部意义就是这条时间线可信。

所以本文件守的不是「日志好不好看」，而是「日志里的某一行能不能被外部构造出来」。
"""

import unittest

from app.utils.sanitize import scrub_log_value

# 攻击者想让它成为「独立的一行日志」的内容
FORGED = "2026-10-12 03:00:00.000 | INFO | app.services.auth_service:281 - 用户 1 登录成功"
PAYLOAD = "STAR-99\n" + FORGED + "\ntail"


class ScrubHelperTests(unittest.TestCase):
    def test_control_chars_are_collapsed_into_one_line(self):
        out = scrub_log_value(PAYLOAD)
        self.assertNotIn("\n", out)
        self.assertNotIn("\r", out)
        # 内容仍在（排查要看得到），只是不再能分行
        self.assertIn("STAR-99", out)
        self.assertIn("登录成功", out)

    def test_other_control_sequences_are_not_skipped(self):
        self.assertNotIn("\x00", scrub_log_value("a\x00b\x0bc\x0cc"))
        self.assertNotIn("\t", scrub_log_value("a\tb"))

    def test_none_and_non_string_inputs_are_safe(self):
        self.assertEqual(scrub_log_value(None), "")
        self.assertEqual(scrub_log_value(12345), "12345")

    def test_length_is_capped(self):
        out = scrub_log_value("x" * 5_000, max_len=120)
        self.assertEqual(len(out), 121)  # 120 + 省略号
        self.assertTrue(out.endswith("…"))

    def test_forgery_would_land_without_scrubbing(self):
        """反向对照（本文件的「有牙」证明）：不净化时注入行确实会成为独立一行。"""
        unsanitized = f"用户 1 提交知识星球认证: {PAYLOAD}"
        self.assertIn(FORGED, unsanitized.splitlines())

        sanitized = f"用户 1 提交知识星球认证: {scrub_log_value(PAYLOAD)}"
        self.assertNotIn(FORGED, sanitized.splitlines())


# 注：原先还有一条「知识星球认证提交」的日志注入端到端用例，
# 随实名审核模块下线删除；scrub_log_value 的单元契约保留在上面。


if __name__ == "__main__":
    unittest.main()
