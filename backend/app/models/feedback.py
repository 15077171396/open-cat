"""用户问题反馈模型"""

from datetime import datetime
from sqlalchemy import JSON, BigInteger, String, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id

# 反馈状态流转：待处理 → 处理中 → 已解决 / 已关闭
FEEDBACK_STATUSES = ("pending", "processing", "resolved", "closed")

# 单条反馈可附带的截图张数上限（与评论配图同口径）
FEEDBACK_MAX_IMAGES = 3

# 状态展示名：界面文案的唯一事实来源。
# ⚠️ 放在后端而不是管理端常量 —— 两端各手写一份的话，写入端加状态时管理端不会报错，
# 只会静默显示成裸代码（审核台的 `comment` 就出过这事）。
# tests/test_feedback_labels.py 断言本表的键与 FEEDBACK_STATUSES 完全相等。
FEEDBACK_STATUS_LABELS = {
    "pending": "待处理",
    "processing": "处理中",
    "resolved": "已解决",
    "closed": "已关闭",
}


class Feedback(Base):
    """用户问题反馈（前台右侧工具栏提交，管理后台审阅）"""
    __tablename__ = "feedbacks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="提交用户ID")
    content: Mapped[str] = mapped_column(Text, comment="反馈内容")
    contact: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="联系方式(可选)")
    # 截图 URL 列表（JSON）。只允许本站 COS/CDN 域名下的地址，见 utils/image_urls 校验
    images: Mapped[list | None] = mapped_column(
        JSON, nullable=True, default=None, comment=f"截图URL列表(最多{FEEDBACK_MAX_IMAGES}张)"
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True, comment="状态: pending/processing/resolved/closed")
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True, comment="管理员备注(内部)")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
