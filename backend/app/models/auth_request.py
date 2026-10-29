"""知识星球认证申请模型"""

from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import UniqueConstraint

from app.database import Base, generate_id


# 待审核唯一键的取值；终态（approved/rejected）该列置 NULL。
# MySQL 唯一索引中 NULL 不参与冲突判定，因此 (user_id, pending_key) 可以做到
# 「同一用户最多一条 pending，但历史终态记录条数不限」——这正是业务需要的语义。
PENDING_KEY = "pending"

# 申请状态取值与展示名：界面文案的唯一事实来源。
# ⚠️ 放后端而不是管理端常量 —— 两端各手写一份的话，新增状态时管理端不会报错，
# 只会静默显示成裸代码（审核台的 `comment` 就出过这事）。
AUTH_REQUEST_STATUSES = ("pending", "approved", "rejected")
AUTH_REQUEST_STATUS_LABELS = {
    "pending": "待审核",
    "approved": "已通过",
    "rejected": "已驳回",
}

# 通过时可选的开通时长（与 audit_service.VIP_DURATIONS 的键一致）
VIP_DURATION_LABELS = {
    "1month": "1 个月",
    "3month": "3 个月",
    "1year": "1 年",
}


class AuthRequest(Base):
    """知识星球认证申请"""
    __tablename__ = "auth_requests"
    __table_args__ = (
        # 并发提交认证申请的兜底：先查后插在并发下必然能钻过去（两个请求同时查到
        # 「无 pending」），此唯一约束让数据库成为最终裁判（P1-8）。
        # pending_key 为 NULL 的终态行不参与冲突，历史记录可无限累积。
        UniqueConstraint("user_id", "pending_key", name="uq_auth_request_pending"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="用户ID")
    star_code: Mapped[str] = mapped_column(String(50), comment="知识星球号")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True, comment="pending/approved/rejected")
    # 仅在 pending 时填入 PENDING_KEY，终态置 NULL（配合上方唯一约束）
    pending_key: Mapped[str | None] = mapped_column(
        String(20), nullable=True, comment="待审核唯一键: pending 时填 'pending'，终态为 NULL"
    )
    vip_duration: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="1month/3month/1year/custom")
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="审核管理员ID")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="审核时间")
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="驳回原因")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="提交时间")
