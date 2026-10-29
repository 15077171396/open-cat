"""资源模型 (含网盘链接)"""

from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, DateTime, Text, Boolean, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import JSON

from app.database import Base, generate_id


class Resource(Base):
    """资源表"""
    __tablename__ = "resources"
    # 按下载量排序时前缀为 (is_pinned, is_featured, download_count)，
    # 迁移 add_resource_sort_index(0020) 的索引以 updated_at 收尾覆盖不到，故另建
    __table_args__ = (
        Index("ix_resources_pin_feat_download", "is_pinned", "is_featured", "download_count"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    name: Mapped[str] = mapped_column(String(200), comment="资源名称")
    description: Mapped[str] = mapped_column(Text, comment="简介")
    content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="正文内容(Markdown/HTML)")
    resource_type: Mapped[str] = mapped_column(
        String(30),
        index=True,
        comment="资源分类，取值由 categories 表(resource 类型)决定；常见 tool/model/dataset/source/doc/other",
    )
    file_size: Mapped[str] = mapped_column(String(20), comment="显示大小(如12.5GB)")
    content_list: Mapped[list | dict | None] = mapped_column(JSON, nullable=True, comment="包含内容列表")
    usage_notes: Mapped[list | dict | None] = mapped_column(JSON, nullable=True, comment="使用说明")
    download_count: Mapped[int] = mapped_column(Integer, default=0, comment="下载次数")
    view_count: Mapped[int] = mapped_column(Integer, default=0, comment="浏览次数")
    # 是否精选推荐：前台资源页「精选资源推荐」优先展示后台开启推荐的资源
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否精选推荐")
    # 是否置顶：前台资源列表最优先展示
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", comment="是否置顶")
    is_published: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", comment="是否已发布（0=草稿，前台不可见）"
    )
    # 封面图 URL：前台资源卡片顶部封面与详情页 Hero 背景
    cover_image: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="封面图URL")
    # 发布作者（前台资源卡片/详情页展示作者名与头像，与扩展同一约定）：
    # 由发布账号自动带出。为空时前台**不渲染作者行**（NULL = 未填写，不用空串表示缺失）
    # ⚠️ `author_name` / `author_avatar_url` 是**发布当时的快照**（作者账号被删后兜底）；
    # 展示以 `author_id` 指向的账号为准 —— 账号改昵称/换头像要能联动
    author_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True, comment="发布作者用户ID")
    author_name: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="发布作者(快照)")
    author_avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="作者头像URL(快照)")
    # 资源标签：JSON 数组，后台灵活添加
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="资源标签")
    # SEO 信息：仅写入页面 meta 标签供搜索引擎抓取，前台页面不展示
    seo_title: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="SEO标题")
    seo_description: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="SEO描述")
    seo_keywords: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="SEO关键词(逗号分隔)")
    # updated_at 建索引：SEO 增量推送（scheduler/tasks.py::collect_recent_urls）每天按
    # `updated_at >= 26 小时前 ORDER BY updated_at DESC LIMIT 500` 扫这张表，无索引即全表 + filesort（P2-43）
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), index=True)
    # 软删除（回收站）：语义与 tutorials.deleted_at 完全一致（见该模型注释）
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True, comment="软删除时间(回收站, NULL=正常)"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 关联
    netdisk_links: Mapped[list["ResourceNetdiskLink"]] = relationship(back_populates="resource", cascade="all, delete-orphan")


class ResourceNetdiskLink(Base):
    """资源网盘链接"""
    __tablename__ = "resource_netdisk_links"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    resource_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("resources.id"), index=True, comment="资源ID")
    platform: Mapped[str] = mapped_column(String(50), comment="baidu/quark/mobile/aliyun/github/official/other")
    url: Mapped[str] = mapped_column(String(1000), comment="网盘链接")
    extract_code: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="提取码")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="排序权重")

    resource: Mapped["Resource"] = relationship(back_populates="netdisk_links")
