"""节点百科 + 扩展模型"""

from datetime import datetime
from sqlalchemy import BigInteger, Boolean, String, Integer, DateTime, Text, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import JSON, ForeignKey

from app.database import Base, generate_id


class Node(Base):
    """ComfyUI 节点百科"""
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    name: Mapped[str] = mapped_column(String(200), comment="节点名称")
    category: Mapped[str] = mapped_column(String(50), index=True, comment="分类")
    description: Mapped[str] = mapped_column(Text, comment="简介")
    content: Mapped[str | None] = mapped_column(Text(length=16777215), nullable=True, comment="正文内容(Markdown/HTML)")
    inputs: Mapped[list | dict | None] = mapped_column(JSON, nullable=True, comment="输入参数")
    outputs: Mapped[list | dict | None] = mapped_column(JSON, nullable=True, comment="输出参数")
    params: Mapped[list | dict | None] = mapped_column(JSON, nullable=True, comment="参数详解")
    examples: Mapped[list | dict | None] = mapped_column(JSON, nullable=True, comment="使用示例")
    view_count: Mapped[int] = mapped_column(Integer, default=0, comment="浏览次数", index=True)
    is_published: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", comment="是否已发布（0=草稿，前台不可见）"
    )
    # 卡片封面（2026-10-12）：列表卡片左侧 1/3 展示首图。
    cover_image: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="封面图URL")
    # 发布作者（与扩展/资源同一约定）：NULL = 未填写，前台不渲染作者行。
    # ⚠️ `author_name` / `author_avatar_url` 只是**发布当时的快照**，用于作者账号被删除后的兜底；
    # 前台展示以 `author_id` 指向的账号为准（改昵称/换头像要能联动，见 content_author.py）
    author_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True, comment="发布作者用户ID")
    author_name: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="发布作者(快照)")
    author_avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="作者头像URL(快照)")
    # SEO 信息（台账 P1-17）：仅输出到详情页 meta 标签供搜索引擎抓取，前台页面不展示。
    # 与 resources / tutorials 同名同宽（迁移 0022/0023 的既有范式），
    # 未填写（NULL）时前台回落「节点名 - ComfyUI节点详解 - 站名」的默认标题格式。
    seo_title: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="SEO标题")
    seo_description: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="SEO描述")
    seo_keywords: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="SEO关键词(逗号分隔)")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # 索引：节点列表默认排序即 updated_at DESC（node_service），无索引会 filesort
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), index=True)


class Plugin(Base):
    """扩展

    2026-10-12 起扩展模块**只保留预览量一个指标**：`platform` / `version` /
    `rating` / `install_count` 四列已随迁移 `20261012_0040` 删除。
    原因：这四个字段在前后台都没有展示位（安装量/评分曾展示过，产品决定下线），
    留着只会让「排序按看不见的指标」「后台能填但永远不显示」这类问题反复出现。
    """

    __tablename__ = "plugins"
    # 索引：公开列表默认（也是唯一剩下的非时间）排序是 `view_count DESC, id DESC`，
    # 排序键必须有索引，否则每次翻页都 filesort（迁移 0035 的立论；0040 删旧列时把
    # 它建的两个索引一起删了，接替索引由迁移 `add_plugin_view_count_index` 补上，R5-07）。
    __table_args__ = (
        Index("ix_plugins_view_count", "view_count", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    name: Mapped[str] = mapped_column(String(200), comment="扩展名称")
    category: Mapped[str] = mapped_column(String(50), index=True, comment="comfyui/coze/other")
    view_count: Mapped[int] = mapped_column(Integer, default=0, comment="浏览次数")
    is_published: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", comment="是否已发布（0=草稿，前台不可见）"
    )
    description: Mapped[str] = mapped_column(Text, comment="简介")
    content: Mapped[str | None] = mapped_column(Text(length=16777215), nullable=True, comment="正文内容(Markdown/HTML)")
    # 发布作者（前台扩展卡片/详情页展示作者名与头像）：由发布账号自动带出，
    # 或从第三方导入返回的 meta.author 自动回填。为空时前台**不渲染作者行**
    # （NULL = 未填写，与 seo_* 同一惯例，不用空串表示缺失）
    # ⚠️ `author_name` / `author_avatar_url` 是**发布当时的快照**（作者账号被删后兜底）；
    # 展示以 `author_id` 指向的账号为准 —— 账号改昵称/换头像要能联动
    author_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True, comment="发布作者用户ID")
    author_name: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="发布作者(快照)")
    author_avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="作者头像URL(快照)")
    features: Mapped[list | dict | None] = mapped_column(JSON, nullable=True, comment="核心功能列表")
    install_steps: Mapped[list | dict | None] = mapped_column(JSON, nullable=True, comment="安装步骤")
    # SEO 信息（台账 P1-17）：同 Node，仅写入详情页 meta 标签，前台不展示
    seo_title: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="SEO标题")
    seo_description: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="SEO描述")
    seo_keywords: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="SEO关键词(逗号分隔)")
    # updated_at 建索引：SEO 增量推送（scheduler/tasks.py::collect_recent_urls）每天按
    # `updated_at >= 26 小时前 ORDER BY updated_at DESC LIMIT 500` 扫这张表，无索引即全表 + filesort（P2-43）
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), index=True)
    # 索引：扩展列表「最新」排序按 created_at DESC
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    sources: Mapped[list["PluginSource"]] = relationship(back_populates="plugin", cascade="all, delete-orphan")


class PluginSource(Base):
    """扩展获取来源"""
    __tablename__ = "plugin_sources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    plugin_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("plugins.id"), index=True)
    source_name: Mapped[str] = mapped_column(String(50), comment="github/quark/baidu/mobile/official")
    url: Mapped[str] = mapped_column(String(1000), comment="链接")
    extract_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    plugin: Mapped["Plugin"] = relationship(back_populates="sources")
