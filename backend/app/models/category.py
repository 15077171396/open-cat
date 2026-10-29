"""分类模型：内容分类 + 分类分组

分组与分类的关系
----------------
- **分组**（`CategoryGroup`）是同一内容类型内部的次级组织方式（如「入门」「进阶」）；
  它**不参与内容筛选**，内容上存的仍是分类名，所以改名/删除分组不会动内容。
- **分类**（`Category`）必须属于某个内容类型（`module`），可选属于某个分组
  （`group_id` 为 NULL = 未分组，兼容分组功能上线前的存量分类）。

为什么分组也带 `module`
-----------------------
内容类型（教程/资源/…）之间的分类互不通用；分组若全局共享，就会出现「教程的『入门』
下面挂着资源的分类」这种越界组合。让分组也归属 module，越界在数据层就写不出来，
前端也只需要按当前内容类型取一次分组清单。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class CategoryGroup(Base):
    """分类分组（同一内容类型内部的次级组织，不参与内容筛选）"""

    __tablename__ = "category_groups"
    __table_args__ = (
        UniqueConstraint("module", "name", name="uq_category_group_module_name"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, default=generate_id
    )
    module: Mapped[str] = mapped_column(String(20), index=True, comment="内容类型键")
    name: Mapped[str] = mapped_column(String(50), comment="分组名称")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="排序权重（小的在前）")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Category(Base):
    """通用分类表"""

    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("module", "name", name="uq_category_module_name"),)

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, default=generate_id
    )
    module: Mapped[str] = mapped_column(String(20), index=True, comment="内容类型键")
    # 所属分组；NULL = 未分组（分组功能上线前的存量分类都落在这里）
    group_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True, comment="所属分组 id；NULL=未分组"
    )
    name: Mapped[str] = mapped_column(String(50), comment="分类名称")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="排序权重")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
