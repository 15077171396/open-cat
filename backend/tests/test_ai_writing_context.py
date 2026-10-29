import unittest

from app.schemas.ai_writing import (
    ContextPayload, HistoryTurn, SelectionPayload, WritingRunRequest,
)
from app.services.ai_writing.context import build_messages, html_to_text, truncate_middle
from app.services.ai_writing.prompts import PromptDefault


class ContextTests(unittest.TestCase):
    def test_html_to_text_keeps_block_boundaries(self):
        html = "<h2>标题</h2><p>第一段<script>alert(1)</script></p><ul><li>甲</li><li>乙</li></ul>"
        text = html_to_text(html)
        self.assertIn("标题\n", text)
        self.assertIn("第一段", text)
        self.assertIn("甲", text)
        self.assertNotIn("alert", text)

    def test_truncate_middle_marks(self):
        text = "a" * 100
        out = truncate_middle(text, 20)
        self.assertLessEqual(len(out), 40)
        self.assertIn("内容已截断", out)

    def test_build_messages_wraps_selection(self):
        prompt = PromptDefault(
            operation="rewrite", display_name="改写", description="",
            system_prompt="你是编辑", user_template="<selection>{{selection}}</selection>{{instruction}}",
            output_type="markdown", model_code="", temperature=0.7,
            max_tokens=1000, max_input_chars=4000, sort_order=0,
        )
        req = WritingRunRequest(
            operation="rewrite", content_type="node",
            selection=SelectionPayload(index=0, length=2, text="原文"),
            context=ContextPayload(title="标题"), instruction="更短",
        )
        messages = build_messages(prompt, req)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("<selection>原文</selection>", messages[1]["content"])
        self.assertIn("更短", messages[1]["content"])

    def test_history_keeps_newest_turns_when_budget_exceeded(self):
        """预算不足时必须丢最旧的轮次：多轮生成里「接着刚才那段」靠的就是最近的上下文。"""
        prompt = PromptDefault(
            operation="rewrite", display_name="改写", description="",
            system_prompt="你是编辑", user_template="{{selection}}",
            output_type="markdown", model_code="", temperature=0.7,
            max_tokens=1000, max_input_chars=4000, sort_order=0,
        )
        # 6 轮 × 799 字 = 4794 字，超出 4000 上限；预算内只能容下最近 5 轮
        req = WritingRunRequest(
            operation="rewrite", content_type="node",
            selection=SelectionPayload(index=0, length=2, text="原文"),
            history=[
                HistoryTurn(role="user" if i % 2 == 0 else "assistant", content=f"[{i}]" + "a" * 796)
                for i in range(6)
            ],
        )
        messages = build_messages(prompt, req)
        history = messages[1:-1]
        self.assertEqual(
            [m["content"][:3] for m in history], ["[1]", "[2]", "[3]", "[4]", "[5]"],
            "历史轮次应按时间正序保留最近的几轮，且不得超出 4000 字预算",
        )
        self.assertLessEqual(sum(len(m["content"]) for m in history), 4000)
        self.assertNotIn("[0]", "".join(m["content"] for m in history), "最旧的轮次应被丢弃")
        self.assertIn("[5]", history[-1]["content"], "最新的一轮必须保留")

    def test_build_messages_converts_quill_html_to_plain_text(self):
        """集成守卫：最终发给模型的提示词里不能再出现标签/属性/脚本内容。"""
        prompt = PromptDefault(
            operation="summarize", display_name="全文摘要", description="",
            system_prompt="你是编辑",
            user_template=(
                "<article_context>\n{{article}}\n</article_context>\n"
                "<article_context>\n{{before}}\n</article_context>\n"
                "<article_context>\n{{after}}\n</article_context>"
            ),
            output_type="markdown", model_code="", temperature=0.5,
            max_tokens=1000, max_input_chars=4000, sort_order=0,
        )
        req = WritingRunRequest(
            operation="summarize", content_type="tutorial_node",
            context=ContextPayload(
                title="标题",
                article_text=(
                    '<h2 class="ql-title">第一章</h2>'
                    '<p style="text-align:center">正文 <strong>加粗</strong>'
                    '<script>alert("x")</script></p>'
                    '<p>第二段<img src="x.png"></p>'
                ),
                before='<p class="ql-before">光标前文</p>',
                after='<div style="color:red">选区后文</div>',
            ),
        )
        text = build_messages(prompt, req)[-1]["content"]
        for leaked in ("<h2", "<p", "<div", "<script", "<strong", "<img",
                       "class=", "style=", "alert(", "src="):
            self.assertNotIn(leaked, text, f"提示词泄露了 HTML 片段: {leaked}")
        self.assertIn("第一章", text)
        self.assertIn("正文 加粗", text)
        self.assertIn("光标前文", text)
        self.assertIn("选区后文", text)
