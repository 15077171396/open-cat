"""GitHub README 拉取服务：供管理后台正文导入。相对链接重写 / 图片转存 COS / 大小校验
已提炼到 third_party_fetch/common.py（多平台共用）。中文优先：先探测 README.zh-CN.md 等
命名，命中返回 language="zh"，未命中回退英文供管理端按需翻译。
多源拉取（应对国内无法直连 GitHub）：jsDelivr CDN → 官方 API → 官方 API 经 GITHUB_PROXY
（仅 GitHub 域名走代理）→ GITHUB_MIRROR_PREFIX 镜像前缀。
地址兼容 github.com/{owner}/{repo}[/tree/{branch} | /blob/{branch}/{path}]（可省协议与 .git 后缀）；
分支名含斜杠且指定深层文件时按「首段为分支、其余为路径」处理（GitHub URL 本身的歧义）。
"""

import asyncio
import base64
import re
from typing import Optional
from urllib.parse import quote, urlparse

import httpx
from loguru import logger

from app.config import settings
from app.core.exceptions import AppException
from app.services.third_party_fetch.common import (
    GITHUB_IMAGE_HOSTS,  # noqa: F401 - re-export，api/images.py 等旧引用保持不变
    JSDELIVR_HOST,
    MAX_CONTENT_SIZE,
    MAX_RESPONSE_SIZE,
    ZH_README_CANDIDATES,
    check_content_size,
    fetch_capped,
    fetch_text_or_error,
    is_zh_readme,
    make_raw_file_resolver,
    rewrite_relative_urls,
    transfer_images_to_cos,
)

# GitHub 用户名 / 仓库名合法字符
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# 允许经 GITHUB_PROXY 出站的域名（GitHub 域名白名单）。代理只服务
# GitHub 请求：COS、jsDelivr、内网地址一律直连，互不影响
_PROXY_ALLOWED_HOSTS = (GITHUB_IMAGE_HOSTS - {JSDELIVR_HOST}) | {"api.github.com"}

# jsDelivr 模式下 README 常见命名（GitHub 仓库文件名区分大小写，按概率排序）。
# 末尾裸 README 兼容无扩展名的老仓库（如 octocat/Hello-World）
_JSDELIVR_README_CANDIDATES = (
    "README.md",
    "readme.md",
    "Readme.md",
    "README.MD",
    "README.markdown",
    "README.rst",
    "README.txt",
    "README",
)

# 无版本号时 jsDelivr 解析到最新 release tag：release 包超过 50MB 会 403、
# 仓库没有 release 会 404（"Failed to fetch version info"），此时依次探测
# 常见默认分支名（显式分支不受 release 包体积限制，如 opencode@dev）
_JSDELIVR_BRANCH_FALLBACKS = ("main", "master", "dev")

_GITHUB_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "ai-cat-admin",
}


def _parse_github_url(url: str) -> tuple[str, str, Optional[str], Optional[str]]:
    """解析 GitHub 地址 → (owner, repo, ref, file_path)

    ref 为分支名（None 表示默认分支）；file_path 为仓库内 Markdown
    文件路径（None 表示仓库 README）。
    """
    text = (url or "").strip()
    if not text:
        raise AppException(code=400, message="GitHub 地址不能为空")

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        text = "https://" + text

    m = re.match(r"^https?://(?:www\.)?github\.com/(.+?)/?$", text, re.IGNORECASE)
    if not m:
        raise AppException(code=400, message="仅支持 GitHub 仓库地址，如 https://github.com/owner/repo")

    parts = [p for p in m.group(1).split("/") if p]
    if len(parts) < 2:
        raise AppException(code=400, message="地址格式不正确，需包含 owner/repo，如 https://github.com/owner/repo")

    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if not _REPO_NAME_RE.match(owner) or not _REPO_NAME_RE.match(repo):
        raise AppException(code=400, message="地址格式不正确，owner/repo 含非法字符")

    ref: Optional[str] = None
    file_path: Optional[str] = None
    if len(parts) >= 4 and parts[2] in ("tree", "blob", "raw"):
        if parts[2] == "tree":
            # /tree/{branch}：余下全部视为分支名（可含斜杠）
            ref = "/".join(parts[3:])
        else:
            # /blob/{branch}/{path}：首段为分支，其余为文件路径
            ref = parts[3]
            file_path = "/".join(parts[4:]) or None
    elif len(parts) >= 3:
        # 形如 /owner/repo/docs/guide.md 的直链文件；其余子路径
        # （releases / issues 等仓库页面）忽略，拉取默认 README
        candidate = "/".join(parts[2:])
        if candidate.lower().endswith((".md", ".markdown", ".mdx")):
            file_path = candidate

    return owner, repo, ref, file_path


def _rewrite_relative_urls(
    markdown: str, download_url: str, path_in_repo: str, owner: str, repo: str
) -> str:
    """将 README 中的相对图片 / 链接重写为绝对地址（委托 common 通用实现）

    - 图片（markdown ![]() 与 src 属性）→ raw.githubusercontent.com 直链
    - 链接（markdown []() 与 href 属性）→ github.com blob 页面
    - 以 / 开头 → 相对仓库根；其余 → 相对 README 所在目录
    """
    resolver = make_raw_file_resolver(
        download_url,
        path_in_repo,
        (
            (
                f"https://raw.githubusercontent.com/{owner}/{repo}/",
                f"https://github.com/{owner}/{repo}/blob/",
            ),
        ),
    )
    return rewrite_relative_urls(markdown, resolver)


async def _fetch_raw(client: httpx.AsyncClient, url: str) -> str:
    """从 raw 直链（或镜像包装后的直链）拉取超大文件内容

    ⚠️ 必须走**流式下载**（台账 P2-6 / 代码审查 D4）：原实现 ``await client.get(url)``
    会先把整个响应体读进内存，再判断 ``len(resp.content) > MAX_CONTENT_SIZE`` ——
    那只是「事后报警」，一个 500MB 的 raw 文件足以在检查之前就把进程内存打满。
    ``fetch_capped`` 边读边计数，命中上限即断开连接。
    """
    body = await fetch_capped(client, url, MAX_CONTENT_SIZE)
    if body is None:
        raise AppException(code=502, message="README 原始内容拉取失败")
    if body.over_limit:
        raise AppException(code=400, message="README 内容超过 2MB 上限，请手动精简后粘贴")
    if body.status_code != 200:
        raise AppException(code=502, message="README 原始内容拉取失败")
    return body.text


def _mirror_wrap(url: str) -> str:
    """配置了镜像前缀时在 URL 前拼接（ghproxy 类镜像用法：prefix + 完整 URL）"""
    prefix = settings.GITHUB_MIRROR_PREFIX
    if not prefix:
        return url
    return prefix.rstrip("/") + "/" + url


def raw_to_jsdelivr(url: str) -> Optional[str]:
    """raw.githubusercontent.com URL → jsDelivr gh URL（图片下载兜底源）

    https://raw.githubusercontent.com/o/r/main/a.png →
    https://cdn.jsdelivr.net/gh/o/r@main/a.png
    """
    parsed = urlparse(url)
    if parsed.hostname != "raw.githubusercontent.com":
        return None
    parts = [p for p in (parsed.path or "").split("/") if p]
    if len(parts) < 4:
        return None
    owner, repo, ref = parts[0], parts[1], parts[2]
    path = "/".join(parts[3:])
    if not path:
        return None
    return f"https://{JSDELIVR_HOST}/gh/{owner}/{repo}@{quote(ref, safe='/')}/{path}"


def _image_fetch_candidates(url: str) -> list[str]:
    """图片下载的多源候选：原始 URL → jsDelivr 变体 → 镜像前缀变体"""
    candidates = [url]
    jsd = raw_to_jsdelivr(url)
    if jsd and jsd not in candidates:
        candidates.append(jsd)
    if settings.GITHUB_MIRROR_PREFIX:
        wrapped = _mirror_wrap(url)
        if wrapped not in candidates:
            candidates.append(wrapped)
    return candidates


async def _fetch_via_jsdelivr(
    client: httpx.AsyncClient, owner: str, repo: str, ref: Optional[str], file_path: Optional[str]
) -> Optional[tuple[str, str, str]]:
    """jsDelivr CDN 拉取 README，返回 (markdown, path_in_repo, jsdelivr_url)

    不可用（源异常 / 全部候选 404）返回 None，由上层回退下一源。
    未指定文件时按常见命名探测（GitHub 文件名区分大小写）。

    ref 策略：用户指定了分支则只用该分支；否则先无版本号（jsDelivr 解析到
    最新 release tag），遇到版本解析失败（无 release 仓库）或 release 包超
    50MB 上限（403）时，再依次探测常见默认分支名——显式分支不受 release 包体积
    限制，大仓库（如 anomalyco/opencode）经 @dev 仍可拉取。

    未指定文件时优先探测中文版 README（README.zh-CN.md 等），命中即返回，
    未命中回退英文命名候选。
    """
    if file_path:
        candidates = [file_path]
    else:
        candidates = [*ZH_README_CANDIDATES, *_JSDELIVR_README_CANDIDATES]
    refs: list[Optional[str]] = [ref] if ref else [None, *_JSDELIVR_BRANCH_FALLBACKS]
    for r in refs:
        base = f"https://{JSDELIVR_HOST}/gh/{owner}/{repo}"
        if r:
            base += f"@{quote(r, safe='/')}"
        for p in candidates:
            url = f"{base}/{quote(p, safe='/')}"
            resp = await fetch_capped(client, url, MAX_RESPONSE_SIZE)
            if resp is None:
                logger.warning(f"jsDelivr 拉取异常，回退下一源: {url}")
                return None
            if resp.over_limit:
                # 超限在所有源都一样，直接放弃：逐源重下 8MB 没有意义
                logger.warning(
                    f"jsDelivr 内容过大（超过 {MAX_RESPONSE_SIZE // (1024 * 1024)}MB），放弃: {url}"
                )
                return None
            if resp.status_code == 200:
                return resp.text, p, url
            if resp.status_code == 404:
                # 无版本号时版本解析失败（仓库不存在或无 release）：换文件名无
                # 意义，转显式分支探测；文件级 404 则继续尝试下一候选名
                if r is None and resp.text.startswith("Failed to fetch version info"):
                    logger.warning(f"jsDelivr 无版本号解析失败（仓库不存在或无 release），转分支探测: {url}")
                    break
                continue
            # 403/5xx 包级异常：无版本号（release 包）失败时显式分支可能仍可
            # 访问，转分支探测；显式分支失败说明源不可用，回退下一源
            logger.warning(f"jsDelivr 返回 HTTP {resp.status_code}: {url}")
            if r is None:
                break
            return None
    return None


async def _fetch_via_api(
    client: httpx.AsyncClient,
    api_url: str,
    headers: dict,
    ref: Optional[str],
    file_path: Optional[str],
    use_mirror: bool,
) -> tuple[str, dict]:
    """GitHub Contents API 拉取 README，返回 (markdown, meta)

    use_mirror=True 时 download_url 直链拉取也拼镜像前缀
    （超过 1MB 的文件 API 不返回 base64，需从直链取）。
    """
    resp = await fetch_text_or_error(
        client, api_url,
        params={"ref": ref} if ref else None,
        headers=headers,
        label="GitHub README",
    )
    if resp.status_code == 404:
        raise AppException(
            code=404, message="未找到 README，请确认仓库地址正确、仓库公开且存在 README"
        )
    if resp.status_code == 403:
        raise AppException(
            code=403,
            message="GitHub API 访问受限（可能已达匿名速率上限或触发风控），"
            "请稍后重试或在服务端配置 GITHUB_TOKEN",
        )
    if resp.status_code != 200:
        raise AppException(code=502, message=f"GitHub 请求失败（HTTP {resp.status_code}）")

    try:
        meta = resp.json()
    except ValueError:
        raise AppException(code=502, message="GitHub 返回内容无法解析")

    if isinstance(meta, list):
        raise AppException(code=400, message="该地址指向目录，请填写仓库地址或 Markdown 文件地址")

    content_b64 = meta.get("content") or ""
    download_url = meta.get("download_url") or ""
    if meta.get("encoding") == "base64" and content_b64:
        try:
            markdown = base64.b64decode(content_b64).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            raise AppException(code=502, message="README 内容解码失败")
        check_content_size(markdown)
    elif download_url:
        raw_target = _mirror_wrap(download_url) if use_mirror else download_url
        markdown = await _fetch_raw(client, raw_target)
    else:
        raise AppException(code=502, message="无法获取 README 内容")
    return markdown, meta


async def fetch_github_readme(url: str) -> dict:
    """对外入口：加一层**总耗时预算**（第四轮全量审查 P1-66）。

    下面那个实现会串起 jsDelivr / 官方 API / 代理 / 镜像四个源，每个都带自己的
    超时，最坏情况是四者相加；且调用方是管理端「导入 README」按钮，慢到这里
    没有任何反馈。超预算即快速失败并给出可执行的下一步。
    """
    try:
        return await asyncio.wait_for(
            _fetch_github_readme_impl(url),
            timeout=settings.THIRD_PARTY_FETCH_BUDGET_SECONDS,
        )
    except asyncio.TimeoutError:
        raise AppException(
            code=502,
            message=f"GitHub 内容拉取过慢（已超过 {settings.THIRD_PARTY_FETCH_BUDGET_SECONDS} 秒总预算），"
                    "请稍后重试，或改用镜像/手动粘贴",
        )


async def _fetch_github_readme_impl(url: str) -> dict:
    """从 GitHub 拉取 README，返回 Markdown 文本及来源信息

    多源依次尝试（任一成功即止）：
    1. jsDelivr CDN —— 国内服务器无需代理可达（首选）
    2. 官方 GitHub API —— 信息最全，可直连 GitHub 时命中
    3. 官方 GitHub API 经 GITHUB_PROXY —— 可选出站代理兜底
       （直连失败 / 匿名限流 403 时启用，代理不可用自动跳过）
    4. 镜像前缀 GITHUB_MIRROR_PREFIX —— 可选配置的加速镜像
    地址 / 仓库本身错误（400/404）直接抛出，换源无意义。
    """
    owner, repo, ref, file_path = _parse_github_url(url)

    if file_path:
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{quote(file_path, safe='/')}"
    else:
        api_url = f"https://api.github.com/repos/{owner}/{repo}/readme"

    headers = dict(_GITHUB_API_HEADERS)
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"

    async def _fetch_api_prefer_zh(
        api_client: httpx.AsyncClient, hdrs: dict, use_mirror: bool, mirror_wrap: bool = False
    ) -> tuple[str, dict]:
        """Contents API 拉取：未指定文件时优先探测中文 README 候选名
        （文件级 404 逐个跳过，中文候选全部未命中再拉仓库默认 README）；
        其余错误（限流 / 网络异常）原样抛出，由上层换源或提示。
        mirror_wrap=True 时所有 API URL 经 GITHUB_MIRROR_PREFIX 镜像前缀中转。
        """
        if file_path:
            target = _mirror_wrap(api_url) if mirror_wrap else api_url
            return await _fetch_via_api(api_client, target, hdrs, ref, file_path, use_mirror)
        for cand in ZH_README_CANDIDATES:
            cand_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{quote(cand)}"
            if mirror_wrap:
                cand_url = _mirror_wrap(cand_url)
            try:
                return await _fetch_via_api(api_client, cand_url, hdrs, ref, cand, use_mirror)
            except AppException as exc:
                if exc.code != 404:
                    raise
        target = _mirror_wrap(api_url) if mirror_wrap else api_url
        return await _fetch_via_api(api_client, target, hdrs, ref, file_path, use_mirror)

    # 连接超时单独收紧：服务器无法直连 GitHub 时最多等 5s 即切换源
    timeout = httpx.Timeout(15.0, connect=5.0)
    markdown: Optional[str] = None
    meta: dict = {}
    source = "jsdelivr"
    failures: list[str] = []

    # GITHUB_PROXY 客户端（可选）：独立出站通道，仅用于 GitHub 域名请求
    # （官方 API 兜底源 + 图片转存下载兜底）；jsDelivr / 镜像 / COS 上传
    # 走直连 client，内网服务与外部用户访问不受代理影响
    proxy_client: Optional[httpx.AsyncClient] = None
    if settings.GITHUB_PROXY:
        proxy_client = httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, proxy=settings.GITHUB_PROXY
        )

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            # 源 1: jsDelivr CDN（国内可达，无需代理）
            try:
                r = await _fetch_via_jsdelivr(client, owner, repo, ref, file_path)
                if r:
                    markdown, path, jsd_url = r
                    check_content_size(markdown)
                    # 以 jsdelivr URL 作为相对链接重写的 base：图片重写为 jsdelivr
                    # 绝对地址（转存与前台代理均可达），无需知道默认分支名
                    meta = {"path": path, "download_url": jsd_url, "html_url": None}
            except httpx.HTTPError as exc:
                failures.append(f"jsDelivr: {exc}")

            # 源 2: 官方 GitHub API（直连）
            if markdown is None:
                source = "github_api"
                try:
                    markdown, meta = await _fetch_api_prefer_zh(client, headers, use_mirror=False)
                except AppException as exc:
                    if exc.code in (400, 404):
                        raise
                    failures.append(f"GitHub API: {exc.message}")
                except httpx.TimeoutException:
                    failures.append("GitHub API: 连接超时")
                except httpx.HTTPError as exc:
                    failures.append(f"GitHub API: {exc}")

            # 源 3: 官方 GitHub API 经 GITHUB_PROXY（服务器无法直连 GitHub
            # 或匿名限流 403 时兜底；代理不可用自动跳过，回到直连行为）
            if markdown is None and proxy_client is not None:
                source = "github_api_proxy"
                try:
                    markdown, meta = await _fetch_api_prefer_zh(
                        proxy_client, headers, use_mirror=False
                    )
                except AppException as exc:
                    if exc.code in (400, 404):
                        raise
                    failures.append(f"GitHub API(代理): {exc.message}")
                except httpx.TimeoutException:
                    failures.append("GitHub API(代理): 连接超时")
                except httpx.HTTPError as exc:
                    failures.append(f"GitHub API(代理): {exc}")

            # 源 4: 镜像前缀（可选配置）
            if markdown is None and settings.GITHUB_MIRROR_PREFIX:
                source = "mirror"
                # 安全（SEC-016）：镜像前缀指向第三方公共代理，携带 Authorization
                # 会让 GITHUB_TOKEN 随请求完整发送给第三方（长期 PAT 泄露），
                # 故镜像路径必须剥离认证头（匿名速率限制足够 README 拉取用）
                mirror_headers = dict(_GITHUB_API_HEADERS)
                try:
                    markdown, meta = await _fetch_api_prefer_zh(
                        client, mirror_headers, use_mirror=True, mirror_wrap=True
                    )
                except AppException as exc:
                    if exc.code in (400, 404):
                        raise
                    failures.append(f"镜像: {exc.message}")
                except httpx.HTTPError as exc:
                    failures.append(f"镜像: {exc}")

            if markdown is None:
                raise AppException(
                    code=502,
                    message="GitHub 拉取失败（" + "；".join(failures) + "）。"
                    "服务器无法直连 GitHub 时，可在 .env 配置 GITHUB_PROXY 出站代理"
                    "或 GITHUB_MIRROR_PREFIX 加速镜像",
                )

            # 相对链接重写 → 图片转存 COS（jsDelivr/镜像走直连，GitHub 源经代理兜底）。
            # 转存域名白名单为各平台统一白名单（common 中定义），jsDelivr / 镜像
            # 变体作为额外下载候选，GITHUB_PROXY 仅对 GitHub 域名请求生效
            markdown = _rewrite_relative_urls(
                markdown, meta.get("download_url") or "", meta.get("path") or file_path or "", owner, repo
            )
            markdown, images_transferred = await transfer_images_to_cos(
                client,
                markdown,
                extra_candidates_fn=lambda u: [c for c in _image_fetch_candidates(u) if c != u],
                proxy_client=proxy_client,
                proxy_hosts=_PROXY_ALLOWED_HOSTS,
            )

        return {
            "content": markdown,
            "repo": f"{owner}/{repo}",
            "path": meta.get("path") or file_path or "README.md",
            "source_url": meta.get("html_url") or f"https://github.com/{owner}/{repo}",
            "images_transferred": images_transferred,
            "source": source,
            # 命中中文版 README 时为 "zh"（用户端直接展示中文），
            # 管理端可据此提示是否需要「翻译为中文」
            "language": "zh" if is_zh_readme(meta.get("path") or file_path) else "en",
        }
    finally:
        if proxy_client is not None:
            await proxy_client.aclose()
