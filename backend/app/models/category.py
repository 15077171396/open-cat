"""分类模型"""
from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, DateTime, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base, generate_id


class Category(Base):
    """通用分类表"""
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("module", "name", name="uq_category_module_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    module: Mapped[str] = mapped_column(String(20), index=True, comment="模块: tutorial/resource/node/plugin")
    name: Mapped[str] = mapped_column(String(50), comment="分类名称")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="排序权重")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
