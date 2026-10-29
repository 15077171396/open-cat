import unittest

from app.models.ai_model import AiModel
from app.models.ai_writing_prompt import AiWritingPrompt


class AiWritingSchemaTests(unittest.TestCase):
    def test_ai_model_has_modality_and_image_price(self):
        cols = {c.name for c in AiModel.__table__.columns}
        self.assertIn("modality", cols)
        self.assertIn("image_price", cols)

    def test_ai_writing_prompts_table(self):
        cols = {c.name for c in AiWritingPrompt.__table__.columns}
        self.assertEqual(AiWritingPrompt.__tablename__, "ai_writing_prompts")
        self.assertTrue(
            {"operation", "system_prompt", "user_template", "output_type", "model_code",
             "temperature", "max_tokens", "max_input_chars", "is_enabled"} <= cols
        )
