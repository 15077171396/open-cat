"""数据字典：字典类型 + 字典项。

字典是“给运营维护的枚举”：例如内容状态、反馈状态、审核状态。
业务代码可以按 `type_code` 取启用项；后台在这里增删改，不必为改一个文案发版。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class DictType(Base):
    __tablename__ = "dict_types"
    __table_args__ = (UniqueConstraint("code", name="uq_dict_types_code"),)

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, default=generate_id
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False, index=True, comment="类型编码（业务引用）")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="显示名称")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="说明")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DictItem(Base):
    __tablename__ = "dict_items"
    __table_args__ = (UniqueConstraint("type_id", "value", name="uq_dict_items_type_value"),)

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, default=generate_id
    )
    type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dict_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False, comment="显示标签")
    value: Mapped[str] = mapped_column(String(100), nullable=False, comment="存储值")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="排序")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="是否默认项")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
