"""HuggingFace 模型卡 / 数据集介绍拉取服务：可选附带文件列表。
地址：huggingface.co/{owner}/{model}、/datasets/{owner}/{name}（+ /tree/{rev}、/blob/{rev}/{path}），
hf-mirror.com 与官方同构。多源拉取：hf-mirror 镜像 → 官方直连 → 官方经 GITHUB_PROXY 兜底
（仅 HF 域名走代理）。中文优先：先探测中文 README，未命中按正文判语言；Gated 模型需 HF_TOKEN。"""

import posixpath
import re
from typing import Optional
from urllib.parse import quote

import httpx
from loguru import logger

from app.config import settings
from app.core.exceptions import AppException
from app.services.third_party_fetch.common import (
    MAX_RESPONSE_SIZE,
    SKIP_PREFIXES,
    ZH_README_CANDIDATES,
    capped_reason,
    check_content_size,
    detect_content_language,
    fetch_capped,
    fetch_text_or_error,
    is_zh_readme,
    render_files_section,
    rewrite_relative_urls,
    transfer_images_to_cos,
    unwrap_angle,
)

# 允许经出站代理请求的域名（仅 HuggingFace 官方，镜像直连）
_PROXY_ALLOWED_HOSTS = {"huggingface.co"}

# README 常见命名（中文候选未命中后的回退顺序）
_README_CANDIDATES = ("README.md", "readme.md", "Readme.md", "README.MD")

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# 名称合法字符（HF owner/name 允许字母数字与 - _ .）
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _parse_hf_url(url: str) -> tuple[str, str, str, str, Optional[str]]:
    """解析 HuggingFace 地址 → (kind, owner, name, rev, file_path)

    kind 为 "models" 或 "datasets"；ref 为分支 / 版本（默认 main）；
    file_path 为仓库内 Markdown 文件路径（None 表示 README）。
    """
    text = (url or "").strip()
    if not text:
        raise AppException(code=400, message="HuggingFace 地址不能为空")

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        text = "https://" + text

    m = re.match(r"^https?://(?:www\.)?(huggingface\.co|hf-mirror\.com)/(.+?)/?$", text, re.IGNORECASE)
    if not m:
        raise AppException(
            code=400,
            message="仅支持 HuggingFace 地址，如 https://huggingface.co/owner/model",
        )

    parts = [p for p in m.group(2).split("/") if p]
    if not parts:
        raise AppException(code=400, message="地址格式不正确，需包含 owner/name")

    if parts[0] == "datasets" and len(parts) >= 2:
        kind = "datasets"
        rest = parts[1:]
    else:
        kind = "models"
        rest = parts

    if len(rest) < 2:
        raise AppException(code=400, message="地址格式不正确，需包含 owner/name")
    owner, name = rest[0], rest[1]
    if not _NAME_RE.match(owner) or not _NAME_RE.match(name):
        raise AppException(code=400, message="地址格式不正确，owner/name 含非法字符")

    ref = "main"
    file_path: Optional[str] = None
    tail = rest[2:]
    if len(tail) >= 2 and tail[0] in ("tree", "blob", "raw"):
        # /tree/{rev}/{dir} 或 /blob/{rev}/{path}
        ref = tail[1]
        sub = "/".join(tail[2:])
        if tail[0] == "blob" and sub.lower().endswith((".md", ".markdown", ".mdx")):
            file_path = sub
    elif tail:
        candidate = "/".join(tail)
        if candidate.lower().endswith((".md", ".markdown", ".mdx")):
            file_path = candidate

    return kind, owner, name, ref, file_path


def _api_headers() -> dict:
    headers = {"User-Agent": "ai-cat-admin"}
    if settings.HF_TOKEN:
        headers["Authorization"] = f"Bearer {settings.HF_TOKEN}"
    return headers


def _make_hf_resolver(web_base: str, rev: str, readme_dir: str):
    """构造 HuggingFace 相对 URL 解析器

    - 图片 → resolve 直链（LFS 大图也能取到真实内容，raw 对 LFS 只返回指针文本）
    - 链接 → blob 页面（.md 可在线阅读）
    - 相对路径 → 相对 README 所在目录；/ 开头 → 相对仓库根
    """

    def _resolve(url: str, is_image: bool) -> Optional[str]:
        u = unwrap_angle(url)
        if not u or u.startswith(SKIP_PREFIXES):
            return None
        if u.startswith("/"):
            p = posixpath.normpath(u.lstrip("/"))
        elif readme_dir:
            p = posixpath.normpath(posixpath.join(readme_dir, u))
        else:
            p = posixpath.normpath(u)
        if not p or p.startswith(".."):
            return None
        seg = "resolve" if is_image else "blob"
        return f"{web_base}/{seg}/{quote(rev, safe='/')}/{quote(p)}"

    return _resolve


async def _fetch_readme_from(
    client: httpx.AsyncClient,
    web_base: str,
    rev: str,
    candidates: list[str],
) -> Optional[tuple[str, str]]:
    """从单一源拉取 README（按候选名探测），返回 (markdown, path)

    全部候选 404 返回 None；受限（401/403）与其他异常抛出。
    """
    headers = _api_headers()
    for cand in candidates:
        url = f"{web_base}/raw/{quote(rev, safe='/')}/{quote(cand, safe='/')}"
        resp = await fetch_text_or_error(
            client, url, headers=headers, label="HuggingFace README"
        )
        if resp.status_code == 200:
            return resp.text, cand
        if resp.status_code == 404:
            continue
        if resp.status_code in (401, 403):
            raise AppException(
                code=403,
                message="该资源为受限（Gated）资源或已触发访问限制，"
                "匿名无法拉取；如需访问请在服务端 .env 配置 HF_TOKEN",
            )
        raise AppException(
            code=502, message=f"HuggingFace 请求失败（HTTP {resp.status_code}），请稍后重试"
        )
    return None


async def _fetch_tree_from(
    client: httpx.AsyncClient,
    api_base: str,
    web_base: str,
    kind: str,
    owner: str,
    name: str,
    rev: str,
) -> Optional[list[dict]]:
    """从单一源拉取文件列表（Files and versions），统一为 {path, type, size, url, updated_at} 结构

    请求带 expand=true 以获取每文件最后提交时间（lastCommit.date），带
    recursive=true 递归包含子目录内文件（默认仅返回顶层，文件夹内容缺失）。
    单页上限 1000 条（Link 头翻页），配合渲染端 200 条截断已足够，不翻页。
    文件下载直链：{web_base}/resolve/{rev}/{path}（与 HF 页面下载按钮一致，
    LFS 大文件自动重定向到 CDN；web_base 为实际命中的源，镜像可达性更好）。
    """
    url = (
        f"{api_base}/{'datasets' if kind == 'datasets' else 'models'}/"
        f"{owner}/{name}/tree/{quote(rev, safe='/')}"
    )
    resp = await fetch_capped(
        client, url, MAX_RESPONSE_SIZE,
        params={"expand": "true", "recursive": "true"}, headers=_api_headers(),
    )
    if resp is None or resp.over_limit or resp.status_code != 200:
        logger.warning(f"HuggingFace 文件列表拉取失败（{capped_reason(resp)}）: {url}")
        return None
    try:
        tree = resp.json()
    except ValueError:
        return None
    if not isinstance(tree, list):
        return []
    entries: list[dict] = []
    for e in tree:
        path = e.get("path")
        if not path:
            continue
        entry = {
            "path": path,
            "type": e.get("type", "file"),
            "size": e.get("size"),
            "url": None,
            # 最后提交时间（ISO 8601，common 统一格式化为日期）
            "updated_at": (e.get("lastCommit") or {}).get("date"),
        }
        if entry["type"] == "file":
            entry["url"] = f"{web_base}/resolve/{quote(rev, safe='/')}/{quote(path)}"
        entries.append(entry)
    return entries


async def fetch_huggingface_doc(url: str, include_files: bool = False) -> dict:
    """从 HuggingFace 拉取 model card / 数据集介绍，返回 Markdown 文本及来源信息

    多源依次尝试（任一成功即止）：hf-mirror 镜像 → 官方直连 → 官方经代理。
    """
    kind, owner, name, rev, file_path = _parse_hf_url(url)
    title = f"{owner}/{name}"
    repo_prefix = "datasets/" if kind == "datasets" else ""

    candidates = [file_path] if file_path else [*ZH_README_CANDIDATES, *_README_CANDIDATES]

    # 镜像优先（国内可达）；镜像上的地址供相对链接重写使用（国内用户可达性更好）
    mirror_web = f"https://hf-mirror.com/{repo_prefix}{owner}/{name}"
    official_web = f"https://huggingface.co/{repo_prefix}{owner}/{name}"
    mirror_api = "https://hf-mirror.com/api"
    official_api = "https://huggingface.co/api"

    timeout = httpx.Timeout(15.0, connect=5.0)
    proxy_client: Optional[httpx.AsyncClient] = None
    if settings.GITHUB_PROXY:
        proxy_client = httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, proxy=settings.GITHUB_PROXY
        )

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            # 源 1: hf-mirror 镜像（国内可达）；源 2: 官方直连；源 3: 官方经代理
            # (源名称, 页面基址, API 基址, 客户端)
            sources: list[tuple[str, str, str, httpx.AsyncClient]] = [
                ("hf-mirror", mirror_web, mirror_api, client),
                ("HuggingFace", official_web, official_api, client),
            ]
            if proxy_client is not None:
                sources.append(
                    ("HuggingFace(代理)", official_web, official_api, proxy_client)
                )

            markdown: Optional[str] = None
            used_path: Optional[str] = None
            used_web_base = mirror_web
            failures: list[str] = []
            for src_name, web_base, api_base, src_client in sources:
                try:
                    result = await _fetch_readme_from(src_client, web_base, rev, candidates)
                except AppException as exc:
                    # 401/403 是资源级限制；400 是「内容过大」——同一份 README 在所有源
                    # 都一样大，继续轮询只会把明确可操作的提示淹没在一串失败信息里
                    if exc.code in (400, 401, 403):
                        raise
                    failures.append(f"{src_name}: {exc.message}")
                    continue
                except httpx.HTTPError as exc:
                    failures.append(f"{src_name}: {exc}")
                    continue
                if result is not None:
                    markdown, used_path = result
                    used_web_base = web_base
                    break

            if markdown is None:
                raise AppException(
                    code=502,
                    message="HuggingFace 拉取失败（" + "；".join(failures) + "）。"
                    "请确认资源存在且公开；服务器无法直连时，可在 .env 配置 "
                    "GITHUB_PROXY 出站代理",
                )
            check_content_size(markdown)

            # 可选：附带文件列表（镜像 → 官方直连 → 代理，失败降级不阻塞导入）
            files_included = False
            if include_files:
                files: Optional[list[dict]] = None
                for _, web_base, api_base, src_client in sources:
                    try:
                        files = await _fetch_tree_from(
                            src_client, api_base, web_base, kind, owner, name, rev
                        )
                    except httpx.HTTPError:
                        files = None
                    if files is not None:
                        break
                if files:
                    section = render_files_section(files)
                    if section:
                        markdown = f"{markdown}\n\n{section}"
                        files_included = True

            # 相对图片 / 链接重写为绝对地址（基于实际命中的源，镜像可达性更好）
            readme_dir = posixpath.dirname(used_path or "README.md")
            resolver = _make_hf_resolver(used_web_base, rev, readme_dir)
            markdown = rewrite_relative_urls(markdown, resolver)

            markdown, images_transferred = await transfer_images_to_cos(
                client, markdown, proxy_client=proxy_client, proxy_hosts=_PROXY_ALLOWED_HOSTS
            )
    finally:
        if proxy_client is not None:
            await proxy_client.aclose()

    language = (
        "zh"
        if is_zh_readme(used_path or "")
        else detect_content_language(markdown)
    )
    return {
        "content": markdown,
        "title": title,
        "platform": "huggingface",
        "resource_type": "model" if kind == "models" else "dataset",
        "language": language,
        "source": "hf-mirror" if "hf-mirror" in used_web_base else "huggingface",
        "source_url": f"https://huggingface.co/{repo_prefix}{owner}/{name}",
        "images_transferred": images_transferred,
        "files_included": files_included,
    }
