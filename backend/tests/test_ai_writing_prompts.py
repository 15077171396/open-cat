import unittest
from unittest import mock

from app.models.ai_writing_prompt import AiWritingPrompt
from app.services.ai_writing import prompts as prompt_module
from app.services.ai_writing.prompts import (
    DEFAULT_PROMPTS, ensure_default_prompts, render_template, reset_prompt,
)
from tests.conftest import make_user
from tests.fake_db import MemorySession


class PromptTests(unittest.IsolatedAsyncioTestCase):
    def test_render_known_and_unknown_vars(self):
        out = render_template("{{title}}/{{unknown}}/{{title}}", {"title": "T"})
        self.assertEqual(out, "T/{{unknown}}/T")

    async def test_seed_is_idempotent(self):
        db = MemorySession()
        self.assertEqual(await ensure_default_prompts(db), len(DEFAULT_PROMPTS))
        self.assertEqual(await ensure_default_prompts(db), 0)
        self.assertEqual(len(db.all_rows(AiWritingPrompt)), len(DEFAULT_PROMPTS))

    async def test_reset_restores_default(self):
        db = MemorySession()
        admin = make_user(role="admin")
        await ensure_default_prompts(db)
        with mock.patch.object(prompt_module, "record_admin_action"):
            await reset_prompt(db, "rewrite", admin)

    def test_default_prompt_set_is_complete(self):
        """种子集合是前端操作菜单与配置页的共同来源：缺一项 = 该操作永远取不到提示词。"""
        self.assertEqual(len(DEFAULT_PROMPTS), 10)
        # illustrate 走配图流水线（Task 7），不经过提示词渲染，误加会多出一行无法使用的配置
        self.assertNotIn("illustrate", {p.operation for p in DEFAULT_PROMPTS})
