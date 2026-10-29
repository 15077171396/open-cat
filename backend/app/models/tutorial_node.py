"""教程章节模型（树形结构，PandaWiki 文档节点模式）

教程正文从 tutorials.content 单大字段拆分为按章存储：
- 每章一行，parent_id 自关联支持任意层级
- 前台左树 = tutorial_nodes 的文档间层级（跨页面导航）
- 前台右大纲 = 单章正文内的 h1-h6 标题（章内定位），两级数据源彻底分离
"""

from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, DateTime, Text, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class TutorialNode(Base):
    """教程章节表（自关联树）"""
    __tablename__ = "tutorial_nodes"
    # 章节树高频按教程查询 + 同级排序：tutorial_id + sort 复合索引一次定位
    __table_args__ = (
        Index("ix_tutorial_nodes_tutorial_sort", "tutorial_id", "sort"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    tutorial_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tutorials.id"), index=True, comment="所属教程ID")
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("tutorial_nodes.id"), nullable=True, index=True, comment="父章节ID(顶级为NULL)")
    sort: Mapped[int] = mapped_column(Integer, default=0, comment="同级排序(小在前)")
    title: Mapped[str] = mapped_column(String(200), comment="章节标题")
    content: Mapped[str] = mapped_column(Text(length=16777215), default="", comment="章节正文(HTML)")  # MEDIUMTEXT
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # updated_at 建索引：SEO 增量推送（scheduler/tasks.py::collect_recent_urls）每天按
    # `updated_at >= 26 小时前 ORDER BY updated_at DESC LIMIT 500` 扫这张表，无索引即全表 + filesort（P2-43）
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), index=True)
