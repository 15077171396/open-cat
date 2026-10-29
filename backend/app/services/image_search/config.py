"""图片搜索运行配置：数据库设置优先，环境变量兜底。

密钥只在这里解密给服务端使用；`options` 接口只输出 configured 布尔值。
网站爬取类图源没有 Key，用独立开关控制；默认关闭，避免部署后被意外抓取。
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.crypto import decrypt_secret
from app.core.degrade import degrade_open
from app.models.system_setting import SystemSetting

_GIPHY_HOSTS = [
    "media.giphy.com", "i.giphy.com",
    "media0.giphy.com", "media1.giphy.com", "media2.giphy.com",
    "media3.giphy.com", "media4.giphy.com",
]

# 图源顺序 = 本字典的插入顺序（见文件末尾的 SOURCE_ORDER），不再单独维护一份清单。

SOURCE_META = {
    "pexels": {
        "name": "Pexels",
        "license": "Pexels License",
        "license_url": "https://www.pexels.com/license/",
        "requires_attribution": False,
        "types": ["photo"],
        "image_hosts": ["images.pexels.com"],
        "key_setting": "image_search_pexels_key",
        "env_attr": "IMAGE_SEARCH_PEXELS_KEY",
        "enabled_setting": "image_search_pexels_enabled",
        # (次数, 窗口秒)：官方额度留安全余量，Pexels 200/h
        "rate_limit": (180, 3600),
        "default_enabled": True,
        "requires_key": True,
        "web_images": False,
        "allow_http": False,
        "allow_animation": False,
        "referer": "",
        "help_url": "https://www.pexels.com/api/",
    },
    "pixabay": {
        "name": "Pixabay",
        "license": "Pixabay Content License",
        "license_url": "https://pixabay.com/service/license-summary/",
        "requires_attribution": True,
        "types": ["photo", "illustration"],
        "image_hosts": ["cdn.pixabay.com"],
        "key_setting": "image_search_pixabay_key",
        "env_attr": "IMAGE_SEARCH_PIXABAY_KEY",
        "enabled_setting": "image_search_pixabay_enabled",
        # Pixabay 官方 100/60s
        "rate_limit": (90, 60),
        "default_enabled": True,
        "requires_key": True,
        "web_images": False,
        "allow_http": False,
        "allow_animation": False,
        "referer": "",
        "help_url": "https://pixabay.com/api/docs/",
    },
    "giphy": {
        "name": "GIPHY",
        "license": "GIPHY",
        "license_url": "https://developers.giphy.com/",
        "requires_attribution": False,
        "types": ["meme"],
        "image_hosts": _GIPHY_HOSTS,
        "key_setting": "image_search_giphy_key",
        "env_attr": "IMAGE_SEARCH_GIPHY_KEY",
        "enabled_setting": "image_search_giphy_enabled",
        "rate_limit": (30, 3600),
        "default_enabled": True,
        "requires_key": True,
        "web_images": False,
        "allow_http": False,
        "allow_animation": True,
        "referer": "",
        "help_url": "https://developers.giphy.com/dashboard/",
    },
    "tenor": {
        "name": "Tenor 兼容（GIPHY）",
        "license": "Tenor / GIPHY",
        "license_url": "https://developers.giphy.com/docs/api/tenor-migration/",
        "requires_attribution": False,
        "types": ["meme"],
        "image_hosts": _GIPHY_HOSTS,
        "key_setting": "image_search_giphy_key",
        "env_attr": "IMAGE_SEARCH_GIPHY_KEY",
        "enabled_setting": "image_search_tenor_enabled",
        "rate_limit": (30, 3600),
        # Tenor 官方 API 已于 2026-06-30 停服；开启后走 GIPHY 的 Tenor 兼容端点。
        "default_enabled": False,
        "requires_key": True,
        "web_images": False,
        "allow_http": False,
        "allow_animation": True,
        "referer": "",
        "help_url": "https://developers.giphy.com/docs/api/tenor-migration/",
    },
    "so360": {
        "name": "360 图片",
        "license": "网络图片（360）",
        "license_url": "",
        "requires_attribution": False,
        "types": ["photo", "illustration", "meme"],
        "image_hosts": [],
        "key_setting": "",
        "env_attr": "",
        "enabled_setting": "image_search_so360_enabled",
        "rate_limit": (60, 3600),
        "default_enabled": False,
        "requires_key": False,
        "web_images": True,
        # 原图地址只给 http（qhimg 的 https 原图不通），缩略图是 https。
        "allow_http": True,
        "allow_animation": False,
        "referer": "https://image.so.com/",
        "help_url": "",
    },
    "baidu": {
        "name": "百度图片",
        "license": "网络图片（百度）",
        "license_url": "",
        "requires_attribution": False,
        "types": ["photo", "illustration", "meme"],
        "image_hosts": [],
        "key_setting": "",
        "env_attr": "",
        "enabled_setting": "image_search_baidu_enabled",
        "rate_limit": (60, 3600),
        "default_enabled": False,
        "requires_key": False,
        "web_images": True,
        "allow_http": True,
        "allow_animation": True,
        "referer": "https://image.baidu.com/",
        "help_url": "",
    },
    "sogou": {
        "name": "搜狗图片",
        "license": "网络图片（搜狗）",
        "license_url": "",
        "requires_attribution": False,
        "types": ["photo", "illustration", "meme"],
        "image_hosts": [],
        "key_setting": "",
        "env_attr": "",
        "enabled_setting": "image_search_sogou_enabled",
        "rate_limit": (60, 3600),
        "default_enabled": False,
        "requires_key": False,
        "web_images": True,
        "allow_http": True,
        "allow_animation": True,
        "referer": "https://pic.sogou.com/",
        "help_url": "",
    },
    "duitang": {
        "name": "堆糖",
        "license": "网络图片（堆糖）",
        "license_url": "",
        "requires_attribution": False,
        "types": ["photo", "illustration"],
        "image_hosts": [],
        "key_setting": "",
        "env_attr": "",
        "enabled_setting": "image_search_duitang_enabled",
        "rate_limit": (60, 3600),
        "default_enabled": False,
        "requires_key": False,
        "web_images": True,
        "allow_http": False,
        "allow_animation": False,
        "referer": "https://www.duitang.com/",
        "help_url": "",
    },
    "doutupk": {
        "name": "斗图啦",
        "license": "网络图片（斗图啦）",
        "license_url": "",
        "requires_attribution": False,
        "types": ["meme"],
        "image_hosts": ["img.doutupk.com"],
        "key_setting": "",
        "env_attr": "",
        "enabled_setting": "image_search_doutupk_enabled",
        "rate_limit": (60, 3600),
        "default_enabled": False,
        "requires_key": False,
        "web_images": False,
        "allow_http": False,
        "allow_animation": True,
        "referer": "https://www.doutupk.com/",
        "help_url": "https://www.doutupk.com/",
    },
    "adoutu": {
        "name": "爱斗图",
        "license": "网络图片（爱斗图）",
        "license_url": "",
        "requires_attribution": False,
        "types": ["meme"],
        # 图片域名固定，走精确白名单比「任意公网主机」安全得多
        "image_hosts": ["img.adoutu.com"],
        "key_setting": "",
        "env_attr": "",
        "enabled_setting": "image_search_adoutu_enabled",
        "rate_limit": (60, 3600),
        "default_enabled": False,
        "requires_key": False,
        "web_images": False,
        "allow_http": False,
        "allow_animation": True,
        "referer": "https://www.adoutu.com/",
        "help_url": "",
    },
    "memecub": {
        "name": "memecub",
        "license": "memecub（按图标注）",
        "license_url": "https://memecub.com/zh/licensing",
        "requires_attribution": False,
        "types": ["meme", "photo"],
        # 图片走官方 CDN，域名固定
        "image_hosts": ["gpic.code-nav.cn"],
        "key_setting": "image_search_memecub_key",
        "env_attr": "IMAGE_SEARCH_MEMECUB_KEY",
        "enabled_setting": "image_search_memecub_enabled",
        "rate_limit": (60, 3600),
        "default_enabled": False,
        # Key 可选：不填每个 IP 每天 20 次搜索，填了按月额度
        "requires_key": False,
        "web_images": False,
        "allow_http": False,
        "allow_animation": True,
        "referer": "",
        "help_url": "https://memecub.com/zh/developers",
    },
    "emoji": {
        "name": "EmojiPackage",
        "license": "GitHub EmojiPackage",
        "license_url": "https://github.com/getActivity/EmojiPackage",
        "requires_attribution": False,
        "types": ["meme"],
        "image_hosts": ["raw.githubusercontent.com"],
        "key_setting": "",
        "env_attr": "",
        "enabled_setting": "image_search_emoji_enabled",
        "rate_limit": (30, 3600),
        "default_enabled": False,
        "requires_key": False,
        "web_images": False,
        "allow_http": False,
        "allow_animation": True,
        "referer": "",
        "help_url": "https://github.com/getActivity/EmojiPackage",
    },
}

# 前端 tab 与 options 的顺序：直接取 SOURCE_META 的插入顺序，避免两份清单对不上。
SOURCE_ORDER = tuple(SOURCE_META)

_SETTING_KEYS = tuple(
    ["image_search_enabled", "image_search_page_size",
     "image_search_admin_hourly_limit", "image_search_daily_import_limit"]
    + [meta["enabled_setting"] for meta in SOURCE_META.values()]
    + sorted({meta["key_setting"] for meta in SOURCE_META.values() if meta["key_setting"]})
)


@dataclass(frozen=True)
class ImageSearchConfig:
    enabled: bool
    page_size: int
    hourly_limit: int
    daily_import_limit: int
    keys: dict[str, str]
    enabled_sources: dict[str, bool] = field(default_factory=dict)

    def is_enabled(self, source: str) -> bool:
        meta = SOURCE_META.get(source)
        if meta is None:
            return False
        return self.enabled_sources.get(source, bool(meta["default_enabled"]))

    def has_source(self, source: str) -> bool:
        meta = SOURCE_META.get(source)
        if meta is None or not self.is_enabled(source):
            return False
        if meta["requires_key"]:
            return bool(self.keys.get(source))
        return True


def _as_bool(raw: str | None, fallback: bool) -> bool:
    if raw is None or raw == "":
        return fallback
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(raw: str | None, fallback: int, *, low: int, high: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        value = fallback
    return max(low, min(high, value))


async def load_config(db: AsyncSession) -> ImageSearchConfig:
    raw: dict[str, str] = {}
    try:
        rows = await db.execute(
            select(SystemSetting).where(SystemSetting.setting_key.in_(_SETTING_KEYS))
        )
        raw = {row.setting_key: row.value for row in rows.scalars().all() if row.value}
    except (SQLAlchemyError, OSError) as exc:
        degrade_open("image_search.config", exc, detail="读取图片搜索配置失败，回退环境变量")

    keys: dict[str, str] = {}
    enabled_sources: dict[str, bool] = {}
    for source, meta in SOURCE_META.items():
        key_setting = meta.get("key_setting") or ""
        env_value = getattr(settings, meta.get("env_attr") or "", "")
        keys[source] = (
            decrypt_secret(raw.get(key_setting)) or env_value or ""
        ).strip() if key_setting else ""
        env_enabled = getattr(
            settings, f"IMAGE_SEARCH_{source.upper()}_ENABLED", meta["default_enabled"],
        )
        enabled_sources[source] = _as_bool(
            raw.get(meta["enabled_setting"]), bool(env_enabled),
        )

    return ImageSearchConfig(
        enabled=_as_bool(raw.get("image_search_enabled"), settings.IMAGE_SEARCH_ENABLED),
        page_size=_as_int(
            raw.get("image_search_page_size"), settings.IMAGE_SEARCH_PAGE_SIZE,
            low=6, high=40,
        ),
        hourly_limit=_as_int(
            raw.get("image_search_admin_hourly_limit"),
            settings.IMAGE_SEARCH_ADMIN_HOURLY_LIMIT, low=0, high=10000,
        ),
        daily_import_limit=_as_int(
            raw.get("image_search_daily_import_limit"),
            settings.IMAGE_SEARCH_DAILY_IMPORT_LIMIT, low=0, high=100000,
        ),
        keys=keys,
        enabled_sources=enabled_sources,
    )


def all_image_hosts() -> tuple[str, ...]:
    return tuple(
        host for meta in SOURCE_META.values() for host in meta.get("image_hosts", [])
    )


def options_payload(cfg: ImageSearchConfig) -> list[dict]:
    """图源能力清单；不含密钥。"""
    return [
        {
            "id": source,
            "name": SOURCE_META[source]["name"],
            "configured": cfg.has_source(source),
            "license": SOURCE_META[source]["license"],
            "license_url": SOURCE_META[source]["license_url"],
            "requires_attribution": SOURCE_META[source]["requires_attribution"],
            "types": SOURCE_META[source]["types"],
            "help_url": SOURCE_META[source]["help_url"],
        }
        for source in SOURCE_ORDER
    ]
