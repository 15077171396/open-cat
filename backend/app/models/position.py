"""岗位目录（组织架构的轻量一层，暂不与用户绑定）。"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("code", name="uq_positions_code"),
        UniqueConstraint("name", name="uq_positions_name"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, default=generate_id
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False, index=True, comment="岗位编码")
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="岗位名称")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="岗位职责说明")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="排序")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
