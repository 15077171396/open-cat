"""演示数据脚本的静态守卫（不连库）。

`seed_demo.py` 只在「有人真的去跑」时才会执行，字段名写错、模型改名、kwargs 拼错
这类问题在 CI 里完全看不见——等到用户 clone 下来跑演示数据时才会炸，而且报错是
SQLAlchemy 的 `TypeError: 'xxx' is an invalid keyword argument`，对非作者极不友好。

所以这里用 AST 把脚本里每个 `Model(...)` 调用拆出来，逐字段核对模型上是否真有该属性。
零依赖、不连库、毫秒级。
"""

import ast
import io
import unittest
from pathlib import Path

from PIL import Image

from app.models.announcement import Announcement
from app.models.category import Category
from app.models.comment import ContentComment
from app.models.feedback import Feedback
from app.models.point_transaction import PointTransaction
from app.models.tutorial import Tutorial
from app.models.tutorial_node import TutorialNode
from app.models.user import User

SEED = Path(__file__).resolve().parents[1] / "seed_demo.py"

MODELS = {
    "Announcement": Announcement,
    "Category": Category,
    "ContentComment": ContentComment,
    "Feedback": Feedback,
    "PointTransaction": PointTransaction,
    "Tutorial": Tutorial,
    "TutorialNode": TutorialNode,
    "User": User,
}


class SeedFieldNameTests(unittest.TestCase):
    def test_every_constructor_kwarg_is_a_model_attribute(self):
        tree = ast.parse(SEED.read_text(encoding="utf-8"))
        checked = 0
        problems: list[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            model = MODELS.get(node.func.id)
            if model is None:
                continue
            checked += 1
            for kw in node.keywords:
                if kw.arg is None:  # **kwargs 展开，无法静态判定
                    continue
                if not hasattr(model, kw.arg):
                    problems.append(f"{node.func.id}.{kw.arg}（第 {node.lineno} 行）")

        # 至少要真的扫到东西，否则说明 AST 结构与预期不符（例如改成了工厂函数）
        self.assertGreater(checked, 5, "没有识别到模型构造调用，守卫可能失效了")
        self.assertEqual(problems, [], f"seed_demo.py 用了模型上不存在的字段: {problems}")

    def test_required_columns_are_provided(self):
        """非空且无默认值的列必须显式给值，否则第一次跑就是 IntegrityError。

        这是「静态守卫」比运行时更早发现的那一类问题：模型加了必填列而种子没跟上。
        """
        tree = ast.parse(SEED.read_text(encoding="utf-8"))
        problems: list[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            model = MODELS.get(node.func.id)
            if model is None:
                continue
            provided = {kw.arg for kw in node.keywords if kw.arg}
            required = {
                col.name
                for col in model.__table__.columns
                if not col.primary_key
                and not col.nullable
                and col.default is None
                and col.server_default is None
                # 由数据库生成的（如 created_at）或由关系回填的不算
                and col.name not in {"created_at", "updated_at"}
            }
            missing = required - provided
            if missing:
                problems.append(f"{node.func.id}（第 {node.lineno} 行）缺少必填字段 {sorted(missing)}")

        self.assertEqual(problems, [], "seed_demo.py 缺少必填字段：" + "; ".join(problems))

    def test_mark_prefix_is_used(self):
        """幂等标记必须真的落到标题上，否则重复执行会灌两份数据。"""
        text = SEED.read_text(encoding="utf-8")
        self.assertIn("{MARK}{title}", text)


class PlaceholderImageTests(unittest.TestCase):
    def test_png_is_valid_and_sized(self):
        from seed_demo import _png

        data = _png("cover 1", (66, 99, 235))
        img = Image.open(io.BytesIO(data))
        self.assertEqual(img.format, "PNG")
        self.assertEqual(img.size, (640, 360))

    def test_avatar_size_can_be_overridden(self):
        from seed_demo import _png

        img = Image.open(io.BytesIO(_png("U1", (16, 163, 74), size=(128, 128))))
        self.assertEqual(img.size, (128, 128))


if __name__ == "__main__":
    unittest.main()
