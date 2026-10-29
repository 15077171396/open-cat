"""图片代理的安全策略：哪些主机可被本站中转、什么 MIME 可同源返回、失败时按什么顺序换源。
独立成模块便于其它「中转外部图片」入口复用；传输层（流式/重定向/并发闸门）仍在 api/images.py。
行为由 tests/test_image_proxy_policy.py 钉住。"""

from urllib.parse import unquote, urlparse

from app.config import settings
from app.services.storage import storage
from app.services.github_readme_service import GITHUB_IMAGE_HOSTS, raw_to_jsdelivr

# 允许中转的主机：**精确相等**匹配，不做后缀/前缀判断
ALLOWED_HOSTS = GITHUB_IMAGE_HOSTS

# 安全图片 MIME 精确白名单：不能用 image/ 前缀判断——image/svg+xml 可携带脚本，
# 同源返回时可读取登录态 Cookie（等价账号劫持），与上传白名单一致排除 SVG
SAFE_IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "image/bmp", "image/avif", "image/x-icon", "image/vnd.microsoft.icon",
}

# 允许的中转协议：file/gopher/dict 等都是 SSRF 的经典跳板
ALLOWED_SCHEMES = frozenset({"http", "https"})


def is_allowed_url(url: str) -> bool:
    """协议 + 主机白名单校验（重定向的每一跳都要重新过一遍）"""
    parsed = urlparse(url or "")
    hostname = parsed.hostname or ""
    return parsed.scheme in ALLOWED_SCHEMES and hostname in ALLOWED_HOSTS


def own_cos_object_key(url: str) -> str | None:
    """若 URL 指向**本站 COS 桶**，返回对象 key；否则 None。

    与 GitHub 中转是两条通道：这里的地址不直接发起网络请求，而是取出 key 后
    交给存储层用服务端凭证读取 —— 因此不存在「开放代理 / SSRF」问题，
    key 里出现 `..` 也只在对象存储的命名空间内。
    """
    if not storage.is_configured():
        return None
    text = (url or "").strip()
    if not text:
        return None
    for prefix in storage.public_url_prefixes():
        if not text.startswith(prefix):
            continue
        key = unquote(text[len(prefix):].split("?", 1)[0].split("#", 1)[0])
        if not key or ".." in key:
            return None
        return key
    return None


def fetch_candidates(url: str) -> list[str]:
    """服务端拉取候选源：原始 URL → jsDelivr 变体 → 镜像前缀变体。

    顺序即优先级：原始源最可能命中且不含额外信任传递，jsDelivr 是 GitHub 限流时的
    兜底，自建镜像排最后（它由 `GITHUB_MIRROR_PREFIX` 显式开启）。
    """
    candidates = [url]
    jsd = raw_to_jsdelivr(url)
    if jsd and jsd not in candidates:
        candidates.append(jsd)
    if settings.GITHUB_MIRROR_PREFIX:
        wrapped = settings.GITHUB_MIRROR_PREFIX.rstrip("/") + "/" + url
        if wrapped not in candidates:
            candidates.append(wrapped)
    return candidates
