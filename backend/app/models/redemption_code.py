"""积分兑换码模型"""

from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, DateTime, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class RedemptionCode(Base):
    """积分兑换码

    max_uses: 最大兑换人数（=1 单人码 / >1 多人限量码，先到先得）
    status: active=启用 / disabled=已作废
    used_count 与 max_uses 的核减由原子 UPDATE(WHERE used_count < max_uses)保证并发不超发；
    同一用户重复兑换由 redemption_records 的 (code_id, user_id) 唯一约束兜底。
    """
    __tablename__ = "redemption_codes"
    # 后台列表按状态 + 创建时间倒序高频查询；code 唯一约束（MySQL 自动建唯一索引，查询同样走索引）
    __table_args__ = (
        Index("ix_redemption_codes_status_created", "status", "created_at"),
        UniqueConstraint("code", name="uq_redemption_codes_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    code: Mapped[str] = mapped_column(String(32), comment="兑换码")
    points: Mapped[int] = mapped_column(Integer, comment="每份兑换积分")
    max_uses: Mapped[int] = mapped_column(Integer, comment="最大兑换人数(单人码=1)")
    used_count: Mapped[int] = mapped_column(Integer, server_default="0", comment="已兑换人数")
    status: Mapped[str] = mapped_column(String(10), server_default="active", comment="active/disabled")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="过期时间(空=永不过期)")
    note: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="备注")
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="创建管理员ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")


class RedemptionRecord(Base):
    """兑换记录（同一用户对同一兑换码仅一条，唯一约束兜底并发重复请求）"""
    __tablename__ = "redemption_records"
    __table_args__ = (
        UniqueConstraint("code_id", "user_id", name="uq_redemption_code_user"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    code_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="兑换码ID")
    code: Mapped[str] = mapped_column(String(32), comment="兑换码快照(便于追溯)")
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="用户ID")
    points: Mapped[int] = mapped_column(Integer, comment="获得积分")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="兑换时间")
