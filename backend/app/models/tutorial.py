"""教程模型"""

from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, DateTime, Text, Boolean, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class Tutorial(Base):
    """教程表"""
    __tablename__ = "tutorials"
    # 公开列表高频按 is_published + published_at 排序，复合索引避免全扫+filesort
    # 另两条对应公开列表的「最新 / 最热」排序（置顶 > 推荐 > 时间/热度），
    # 见迁移 add_list_sort_indexes
    __table_args__ = (
        Index("ix_tutorials_published_at", "is_published", "published_at"),
        Index("ix_tutorials_pin_reco_published", "is_pinned", "is_recommended", "published_at"),
        Index("ix_tutorials_pin_reco_views", "is_pinned", "is_recommended", "view_count"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    title: Mapped[str] = mapped_column(String(200), comment="标题")
    description: Mapped[str] = mapped_column(Text, comment="简介")
    introduction: Mapped[str | None] = mapped_column(Text(length=16777215), nullable=True, comment="课程介绍(HTML)")
    category: Mapped[str] = mapped_column(String(50), index=True, comment="分类")
    content: Mapped[str] = mapped_column(Text(length=16777215), comment="正文(Markdown)")  # MEDIUMTEXT
    cover_image: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="封面图URL")
    view_count: Mapped[int] = mapped_column(Integer, default=0, comment="浏览次数")
    status: Mapped[str] = mapped_column(String(20), default="updating", index=True, comment="更新状态: updating/completed")
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, index=True, comment="是否发布")
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", comment="是否置顶")
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", comment="是否推荐")
    # SEO 信息：仅写入页面 meta 标签供搜索引擎抓取，前台页面不展示
    seo_title: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="SEO标题")
    seo_description: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="SEO描述")
    seo_keywords: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="SEO关键词(逗号分隔)")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="发布时间")
    # 软删除（回收站）：NULL = 正常；非 NULL = 在回收站里，可通过后台恢复。
    # ⚠️ 删除时**同时**会把 is_published 置 0 —— 公开端全部读取路径本来就过滤
    # `is_published`，因此「删掉即不可见」不需要改任何公开查询（少改一处就少一个漏洞）。
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True, comment="软删除时间(回收站, NULL=正常)"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # updated_at 建索引：SEO 增量推送（scheduler/tasks.py::collect_recent_urls）每天按
    # `updated_at >= 26 小时前 ORDER BY updated_at DESC LIMIT 500` 扫这张表，无索引即全表 + filesort（P2-43）
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), index=True)
