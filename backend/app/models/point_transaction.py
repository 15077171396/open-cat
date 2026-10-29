"""积分流水模型"""

from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class PointTransaction(Base):
    """积分流水 (每笔积分变动的不可变记录)

    type: consume=工具消耗 / admin_set=管理员设置 / admin_grant=管理员充值 / admin_deduct=管理员扣减 / redeem=兑换码充值
    points: 变动值（正=获得，负=消耗）
    balance_after: 变动后余额快照（对账用）
    """
    __tablename__ = "point_transactions"
    # 高频按 user_id 排序 created_at 倒序分页，复合索引避免 filesort
    __table_args__ = (
        Index("ix_point_tx_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="用户ID")
    points: Mapped[int] = mapped_column(Integer, comment="变动值(正=获得,负=消耗)")
    balance_after: Mapped[int] = mapped_column(Integer, comment="变动后余额")
    type: Mapped[str] = mapped_column(String(20), index=True, comment="类型 consume/admin_set/admin_grant/admin_deduct")
    description: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="描述")
    operator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="操作管理员ID(后台调整时)")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")
