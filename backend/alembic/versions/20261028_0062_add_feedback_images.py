"""反馈支持附带截图：`feedbacks` 增加 `images` JSON 列

Revision ID: add_feedback_images
Revises: seed_mail_setting_permission
Create Date: 2026-10-28

为什么加：
    反馈里「哪个按钮点了没反应」「布局错位」这类问题，纯文字描述成本高且容易漏细节。
    评论配图（`content_comments.images`）已经跑通一条完整链路：用户上传到 COS →
    提交时只带 URL → 服务端按「必须本站 COS/CDN 前缀 + 数量上限」再校验一次。
    反馈沿用同一套口径（`utils/image_urls.validate_uploaded_image_urls`），
    存储形态也保持一致（JSON 数组，NULL = 无图）。

为什么是 JSON 而不是关联表：
    与评论配图同构：图片只随反馈展示、不单独查询、不需要外键约束，
    单独建表只会多一次 join。数量上限 3 张，JSON 列的写入成本可以忽略。

兼容性：
    nullable=True 且无默认值 —— 存量反馈全部为 NULL，服务端 `to_dict` 统一转成 `[]`，
    前端无需判空分支。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "add_feedback_images"
down_revision: Union[str, None] = "seed_mail_setting_permission"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "feedbacks",
        sa.Column(
            "images",
            sa.JSON(),
            nullable=True,
            comment="截图URL列表(最多3张)",
        ),
    )


def downgrade() -> None:
    op.drop_column("feedbacks", "images")
