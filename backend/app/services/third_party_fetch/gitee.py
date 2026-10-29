"""Gitee README 拉取服务：国内平台直连（匿名速率限制宽松）。
地址：gitee.com/{owner}/{repo}[/tree/{branch}|/blob/{branch}/{path}]（兼容省略协议与 .git 后缀）。
中文优先：先探测中文版 README，未命中回退默认并按需翻译。"""

import base64
import re
from typing import Optional
from urllib.parse import quote

import httpx
from loguru import logger

from app.core.exceptions import AppException
from app.services.third_party_fetch.common import (
    MAX_RESPONSE_SIZE,
    ZH_README_CANDIDATES,
    capped_reason,
    check_content_size,
    detect_content_language,
    fetch_capped,
    fetch_text_or_error,
    is_zh_readme,
    make_raw_file_resolver,
    render_files_section,
    rewrite_relative_urls,
    transfer_images_to_cos,
)

_GITEE_API = "https://gitee.com/api/v5/repos"

# 用户名 / 仓库名合法字符（含 gh_mirrors 等嵌套命名空间外的普通标识）
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_HEADERS = {"User-Agent": "ai-cat-admin", "Accept": "application/json"}


def _parse_gitee_url(url: str) -> tuple[str, str, Optional[str], Optional[str]]:
    """解析 Gitee 地址 → (owner, repo, ref, file_path)

    ref 为分支名（None 表示默认分支）；file_path 为仓库内 Markdown
    文件路径（None 表示仓库 README）。
    """
    text = (url or "").strip()
    if not text:
        raise AppException(code=400, message="Gitee 地址不能为空")

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        text = "https://" + text

    m = re.match(r"^https?://(?:www\.)?gitee\.com/(.+?)/?$", text, re.IGNORECASE)
    if not m:
        raise AppException(code=400, message="仅支持 Gitee 仓库地址，如 https://gitee.com/owner/repo")

    parts = [p for p in m.group(1).split("/") if p]
    if len(parts) < 2:
        raise AppException(code=400, message="地址格式不正确，需包含 owner/repo，如 https://gitee.com/owner/repo")

    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if not _REPO_NAME_RE.match(owner) or not _REPO_NAME_RE.match(repo):
        raise AppException(code=400, message="地址格式不正确，owner/repo 含非法字符")

    ref: Optional[str] = None
    file_path: Optional[str] = None
    if len(parts) >= 4 and parts[2] in ("tree", "blob", "raw"):
        if parts[2] == "tree":
            ref = "/".join(parts[3:])
        else:
            ref = parts[3]
            file_path = "/".join(parts[4:]) or None
    elif len(parts) >= 3:
        # 形如 /owner/repo/docs/guide.md 的直链文件；其余子路径
        # （releases / issues 等仓库页面）忽略，拉取默认 README
        candidate = "/".join(parts[2:])
        if candidate.lower().endswith((".md", ".markdown", ".mdx")):
            file_path = candidate

    return owner, repo, ref, file_path


def _branch_from_html_url(html_url: str) -> Optional[str]:
    """从 Gitee 文件页地址（.../blob/{branch}/{path}）提取分支名"""
    m = re.search(r"/blob/([^/]+)", html_url or "")
    return m.group(1) if m else None


async def _get_contents(
    client: httpx.AsyncClient, owner: str, repo: str, path: str, ref: Optional[str]
) -> dict:
    """Contents API 拉取单个文件元信息（含 base64 内容）

    注意：Gitee 对不存在的文件返回 HTTP 200 + 空列表（而非 404），
    目录返回条目列表——两者统一按「文件不存在」处理。
    """
    url = f"{_GITEE_API}/{owner}/{repo}/contents/{quote(path, safe='/')}"
    resp = await fetch_text_or_error(
        client, url,
        params={"ref": ref} if ref else None,
        headers=_HEADERS,
        label="Gitee 文件内容",
    )
    if resp.status_code == 404:
        raise AppException(code=404, message=f"Gitee 上未找到文件 {path}")
    if resp.status_code != 200:
        raise AppException(
            code=502, message=f"Gitee 请求失败（HTTP {resp.status_code}），请稍后重试"
        )
    try:
        data = resp.json()
    except ValueError:
        raise AppException(code=502, message="Gitee 返回内容无法解析")
    if not isinstance(data, dict):
        raise AppException(code=404, message=f"Gitee 上未找到文件 {path}")
    return data


def _decode_content(item: dict) -> str:
    """Contents / Readme API 响应的 base64 内容解码（附大小校验）"""
    content_b64 = item.get("content") or ""
    if item.get("encoding") != "base64" or not content_b64:
        raise AppException(code=502, message="无法获取 README 内容")
    try:
        markdown = base64.b64decode(content_b64).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        raise AppException(code=502, message="README 内容解码失败")
    check_content_size(markdown)
    return markdown


async def _fetch_tree(
    client: httpx.AsyncClient, owner: str, repo: str, branch: str
) -> list[dict]:
    """拉取仓库文件树（递归），统一为 {path, type, size, url} 结构

    Gitee trees API 不返回文件大小，size 置 None（列表展示为 '-'）。
    文件下载直链：gitee.com/{owner}/{repo}/raw/{branch}/{path}（raw 重定向）。
    """
    url = f"{_GITEE_API}/{owner}/{repo}/git/trees/{quote(branch, safe='/')}"
    resp = await fetch_capped(
        client, url, MAX_RESPONSE_SIZE, params={"recursive": 1}, headers=_HEADERS
    )
    if resp is None or resp.over_limit or resp.status_code != 200:
        logger.warning(f"Gitee 文件树拉取失败（{capped_reason(resp)}）: {url}")
        return []
    try:
        tree = resp.json().get("tree") or []
    except ValueError:
        return []
    entries: list[dict] = []
    for e in tree:
        path = e.get("path")
        if not path:
            continue
        entry = {"path": path, "type": e.get("type", "blob"), "size": None, "url": None}
        if entry["type"] == "blob":
            entry["url"] = (
                f"https://gitee.com/{owner}/{repo}/raw/"
                f"{quote(branch, safe='/')}/{quote(path, safe='/')}"
            )
        entries.append(entry)
    return entries


async def fetch_gitee_readme(url: str, include_files: bool = False) -> dict:
    """从 Gitee 拉取 README，返回 Markdown 文本及来源信息"""
    owner, repo, ref, file_path = _parse_gitee_url(url)

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        # 中文版候选优先（文件级 404 逐个跳过），未命中拉默认 README
        item: Optional[dict] = None
        zh_path: Optional[str] = None
        if not file_path:
            for cand in ZH_README_CANDIDATES:
                try:
                    item = await _get_contents(client, owner, repo, cand, ref)
                    zh_path = cand
                    break
                except AppException as exc:
                    if exc.code != 404:
                        raise
        if item is None:
            if file_path:
                item = await _get_contents(client, owner, repo, file_path, ref)
            else:
                resp = await fetch_text_or_error(
                    client,
                    f"{_GITEE_API}/{owner}/{repo}/readme",
                    params={"ref": ref} if ref else None,
                    headers=_HEADERS,
                    label="Gitee README",
                )
                if resp.status_code == 404:
                    raise AppException(
                        code=404,
                        message="未找到 README，请确认仓库地址正确、仓库公开且存在 README",
                    )
                if resp.status_code != 200:
                    raise AppException(
                        code=502,
                        message=f"Gitee 请求失败（HTTP {resp.status_code}），请稍后重试",
                    )
                try:
                    item = resp.json()
                except ValueError:
                    raise AppException(code=502, message="Gitee 返回内容无法解析")
                if not isinstance(item, dict):
                    raise AppException(
                        code=404,
                        message="未找到 README，请确认仓库地址正确、仓库公开且存在 README",
                    )

        markdown = _decode_content(item)
        path: str = item.get("path") or zh_path or file_path or "README.md"
        download_url: str = item.get("download_url") or ""
        branch = ref or _branch_from_html_url(item.get("html_url") or "") or "master"

        # 相对图片 / 链接重写为绝对地址（图片 → raw 直链，链接 → blob 页面）
        if not download_url:
            download_url = f"https://gitee.com/{owner}/{repo}/raw/{quote(branch, safe='/')}/{quote(path, safe='/')}"
        resolver = make_raw_file_resolver(
            download_url,
            path,
            (
                (
                    f"https://gitee.com/{owner}/{repo}/raw/",
                    f"https://gitee.com/{owner}/{repo}/blob/",
                ),
            ),
        )
        markdown = rewrite_relative_urls(markdown, resolver)

        # 可选：附带文件列表（失败降级为提示，不阻塞正文导入）
        files_included = False
        if include_files:
            tree = await _fetch_tree(client, owner, repo, branch)
            section = render_files_section(tree)
            if section:
                markdown = f"{markdown}\n\n{section}"
                files_included = True
            else:
                logger.warning(f"Gitee 文件列表拉取为空，跳过: {owner}/{repo}")

        markdown, images_transferred = await transfer_images_to_cos(client, markdown)

    return {
        "content": markdown,
        "title": f"{owner}/{repo}",
        "platform": "gitee",
        "resource_type": "repo",
        # 文件名探测未命中时按正文内容兜底判断（Gitee 上 README.md 常为中文）
        "language": (
            "zh" if is_zh_readme(zh_path or path) else detect_content_language(markdown)
        ),
        "source": "gitee_api",
        "source_url": f"https://gitee.com/{owner}/{repo}",
        "images_transferred": images_transferred,
        "files_included": files_included,
    }
