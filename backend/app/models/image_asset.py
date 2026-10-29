"""图片资产台账：图库导入、本地上传与 AI 生成图片的统一索引。

表同时承担三件事：导入去重、版权凭证（作者/原图页面/许可证）、本站素材库检索。
`source_id` 对图源是图片 ID，对上传是 sha256；两者都非空才能用唯一索引去重。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Index, Integer, String, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class ImageAsset(Base):
    __tablename__ = "image_assets"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_image_assets_source_source_id"),
        Index("ix_image_assets_sha256", "sha256"),
        Index("ix_image_assets_is_active", "is_active"),
        Index("ix_image_assets_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, default=generate_id, comment="主键",
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="图源：pexels / pixabay / upload / ai",
    )
    source_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="图源图片 ID，上传/生成用 sha256",
    )
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="image", server_default="image",
        index=True, comment="素材类型：image/file/video/audio/document/archive/other",
    )
    mime_type: Mapped[str] = mapped_column(
        String(100), nullable=False, default="", server_default="", comment="MIME 类型",
    )
    filename: Mapped[str] = mapped_column(
        String(300), nullable=False, default="", server_default="", comment="原始文件名",
    )
    sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="内容 sha256（去重/完整性）",
    )
    title: Mapped[str] = mapped_column(
        String(300), nullable=False, default="", server_default="", comment="标题/文件名",
    )
    alt: Mapped[str] = mapped_column(
        String(300), nullable=False, default="", server_default="", comment="Alt 文本",
    )
    author: Mapped[str] = mapped_column(
        String(200), nullable=False, default="", server_default="", comment="作者",
    )
    author_url: Mapped[str | None] = mapped_column(
        String(1000), nullable=True, comment="作者主页",
    )
    page_url: Mapped[str | None] = mapped_column(
        String(1000), nullable=True, comment="原图页面（署名/下架追踪）",
    )
    license: Mapped[str] = mapped_column(
        String(60), nullable=False, default="", server_default="", comment="许可证名称",
    )
    license_url: Mapped[str | None] = mapped_column(
        String(1000), nullable=True, comment="许可证链接",
    )
    requires_attribution: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0", comment="是否强制署名",
    )
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cos_url: Mapped[str] = mapped_column(String(1000), nullable=False, comment="站内 COS 地址")
    thumb_url: Mapped[str | None] = mapped_column(
        String(1000), nullable=True, comment="缩略图地址（COS 或图源 CDN）",
    )
    query: Mapped[str] = mapped_column(
        String(200), nullable=False, default="", server_default="", comment="导入/生成时的检索词",
    )
    tags: Mapped[str] = mapped_column(
        String(500), nullable=False, default="", server_default="", comment="逗号分隔标签",
    )
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="上传/导入管理员 ID",
    )
    usage_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0", comment="被复制/插入次数",
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近一次使用时间",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1", comment="是否可检索",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )
