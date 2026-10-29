"""图源适配器的统一数据结构与搜索参数。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SearchQuery:
    q: str
    page: int = 1
    per_page: int = 24
    image_type: str = "all"
    orientation: str = "all"

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError("page must be >= 1")
        if self.per_page < 1:
            raise ValueError("per_page must be >= 1")
        if self.image_type not in {"all", "photo", "illustration", "meme"}:
            raise ValueError("unsupported image_type")
        if self.orientation not in {"all", "landscape", "portrait", "square"}:
            raise ValueError("unsupported orientation")


@dataclass(frozen=True)
class ImageHit:
    source: str
    source_id: str
    thumb_url: str
    download_url: str
    width: int
    height: int
    alt: str = ""
    title: str = ""
    author: str = ""
    author_url: str = ""
    page_url: str = ""
    license: str = ""
    license_url: str = ""
    requires_attribution: bool = False
    # 原图可能 403/防盗链；导入时依次尝试这些备用地址（如搜索结果的缩略图）。
    fallback_urls: tuple[str, ...] = ()

    def public_dict(self) -> dict:
        """接口/缓存用：不暴露 download_url，导入时由服务端重新解析。"""
        return {
            "source": self.source,
            "source_id": self.source_id,
            "thumb_url": self.thumb_url,
            "preview_url": self.download_url,
            "width": self.width,
            "height": self.height,
            "alt": self.alt,
            "title": self.title,
            "author": self.author,
            "author_url": self.author_url,
            "page_url": self.page_url,
            "license": self.license,
            "license_url": self.license_url,
            "requires_attribution": self.requires_attribution,
        }


@dataclass(frozen=True)
class SearchPage:
    items: list[ImageHit] = field(default_factory=list)
    total: int | None = None
    has_more: bool = False
