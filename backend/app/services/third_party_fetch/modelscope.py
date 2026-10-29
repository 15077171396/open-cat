"""ModelScope 模型 / 数据集介绍拉取服务：可选附带文件列表。
地址：modelscope.cn/models|datasets/{owner}/{name}（/files、/preview 等子页面忽略）。
国内平台无需代理/镜像直连；文件（含 README）统一走 raw 仓库接口（Revision + FilePath）。
中文优先：先探测中文 README；模型未命中时额外尝试平台预翻译译文（数据集不支持），再按正文判语言。"""

import posixpath
import re
from typing import Optional
from urllib.parse import quote

import httpx
from loguru import logger

from app.core.exceptions import AppException
from app.services.third_party_fetch.common import (
    MAX_RESPONSE_SIZE,
    SKIP_PREFIXES,
    ZH_README_CANDIDATES,
    CappedBody,
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

_MS_API = "https://modelscope.cn/api/v1"

# 魔搭「文件不存在」的业务错误码（见 _is_file_missing）
_MS_FILE_MISSING_CODE = 10990101007

# 读取错误体的字节上限：只为拿业务错误码，不必读完
_ERROR_BODY_LIMIT = 4096

# 模型 README 中文译文接口（魔搭页面「译文」切换同源）：
# POST /api/v1/rm/fc?Type=translate-readme，返回平台预先翻译好的中文 Markdown，
# 命中则免去管理端 AI 翻译步骤。仅模型支持（数据集请求 500），译文中的
# 相对图片路径与原文一致，不影响后续相对链接重写。
_MS_TRANSLATE_API = "https://modelscope.cn/api/v1/rm/fc?Type=translate-readme"

# 译文标题末尾的 Pandoc 风格锚点 ID 残留（如 "## 1. 模型介绍 {#1-model-introduction}"）：
# 魔搭翻译管线生成 HTML 锚点后转回 Markdown 时遗留，渲染器不识别该语法会原样显示
_MS_ANCHOR_ID_RE = re.compile(r"^(#{1,6}\s+.+?)\s*\{#[^}]*\}\s*$", re.MULTILINE)

# 名称合法字符（owner/name 允许字母数字与 - _ .）
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_HEADERS = {"User-Agent": "ai-cat-admin"}

# README 常见命名（中文候选未命中后的回退顺序）
_README_CANDIDATES = ("README.md", "readme.md", "Readme.md", "README.MD")


def _parse_modelscope_url(url: str) -> tuple[str, str, str]:
    """解析 ModelScope 地址 → (kind, owner, name)

    kind 为 "models" 或 "datasets"；/models/{o}/{n}/files 等子页面路径
    忽略（仍拉取 README）。
    """
    text = (url or "").strip()
    if not text:
        raise AppException(code=400, message="ModelScope 地址不能为空")

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        text = "https://" + text

    m = re.match(r"^https?://(?:www\.)?modelscope\.cn/(.+?)/?$", text, re.IGNORECASE)
    if not m:
        raise AppException(
            code=400, message="仅支持 ModelScope 地址，如 https://modelscope.cn/models/owner/name"
        )

    parts = [p for p in m.group(1).split("/") if p]
    if len(parts) < 3 or parts[0] not in ("models", "datasets"):
        raise AppException(
            code=400,
            message="地址格式不正确，需形如 https://modelscope.cn/models/owner/name "
            "或 https://modelscope.cn/datasets/owner/name",
        )

    kind, owner, name = parts[0], parts[1], parts[2]
    if not _NAME_RE.match(owner) or not _NAME_RE.match(name):
        raise AppException(code=400, message="地址格式不正确，owner/name 含非法字符")

    return kind, owner, name


def _make_ms_resolver(
    kind: str, owner: str, name: str, rev: str, readme_dir: str
):
    """构造 ModelScope 相对 URL 解析器

    ModelScope 仓库文件 URL 为查询参数式
    （/api/v1/{kind}/{o}/{n}/repo?Revision=&FilePath=），不能直接用
    urljoin（相对路径拼接会丢查询参数），需按 POSIX 语义解析后重组：
    - 相对路径 → 相对 README 所在目录
    - / 开头 → 相对仓库根
    """

    def _resolve(url: str, is_image: bool) -> Optional[str]:
        u = unwrap_angle(url)
        if not u or u.startswith(SKIP_PREFIXES):
            return None
        if u.startswith("/"):
            fp = posixpath.normpath(u.lstrip("/"))
        elif readme_dir:
            fp = posixpath.normpath(posixpath.join(readme_dir, u))
        else:
            fp = posixpath.normpath(u)
        if not fp or fp.startswith(".."):
            # 越过仓库根的相对路径无法解析
            return None
        return (
            f"{_MS_API}/{kind}/{owner}/{name}/repo"
            f"?Revision={quote(rev)}&FilePath={quote(fp)}"
        )

    return _resolve


def _is_file_missing(resp: CappedBody) -> bool:
    """判断 ModelScope 的响应是否为「文件不存在」（而不是服务故障）

    🚨 线上事故（2026-10-11）修复点，改动前务必读完：

    **ModelScope 用 HTTP 500 表达「文件不存在」，状态码不稳定**。实测同一个
    不存在的文件，可能返回 404，也可能返回 500，错误体完全相同：

        {"Code":10990101007,"Message":"获取模型文件失败，文件内容为空",
         "RequestId":"...","Success":false}

    原实现只把 404 当「不存在」，其余一律 raise 502。而中文 README 候选
    （``README.zh-CN.md`` 等）排在候选列表**最前面** —— 绝大多数仓库没有中文
    README，于是**第一个候选就 500**、整个拉取直接 502：

        ModelScope 请求失败（HTTP 500），请稍后重试

    结果：**ModelScope 拉取全线不可用**（永远走不到真正存在的 README.md）。
    修法是「看错误体里的业务码，而不是只看状态码」。
    """
    if resp.status_code == 404:
        return True
    if resp.status_code < 500:
        return False
    try:
        data = resp.json()
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    return (
        data.get("Code") == _MS_FILE_MISSING_CODE
        or "文件内容为空" in str(data.get("Message") or "")
    )


async def _fetch_raw_file(
    client: httpx.AsyncClient, kind: str, owner: str, name: str, file_path: str
) -> Optional[str]:
    """拉取仓库内单个文件原文，不存在（404 或业务码表示不存在）返回 None"""
    url = f"{_MS_API}/{kind}/{owner}/{name}/repo"
    resp = await fetch_text_or_error(
        client, url,
        params={"Revision": "master", "FilePath": file_path},
        headers=_HEADERS,
        label="ModelScope 文件内容",
        # 必须读错误体：魔搭用 500 表达「文件不存在」，只看状态码会误判成服务故障
        error_body_limit=_ERROR_BODY_LIMIT,
    )
    if resp.status_code == 200:
        return resp.text
    if _is_file_missing(resp):
        return None
    raise AppException(
        code=502, message=f"ModelScope 请求失败（HTTP {resp.status_code}），请稍后重试"
    )


async def _fetch_translated_readme(
    client: httpx.AsyncClient, owner: str, name: str
) -> Optional[str]:
    """拉取魔搭平台预翻译的中文 README 译文（仅模型，失败返回 None 降级用原文）

    平台页面「译文」切换即此接口（无需登录）：
    - code=finished → content 为完整中文 Markdown 译文
    - code=noneed   → 原文即中文，无需译文
    - 其余 / 异常   → 静默降级（译文是锦上添花，不阻塞正文导入）
    """
    try:
        # 译文是整篇 README，体积由对方决定 —— 同样走流式（台账 P2-6）
        resp = await fetch_capped(
            client,
            _MS_TRANSLATE_API,
            MAX_RESPONSE_SIZE,
            method="POST",
            json_body={
                "type": "model",
                "owner": owner,
                "name": name,
                "preferLanguage": "zh_CN",
            },
        )
        # 网络异常 / 体积超限都归一为 None（降级用原文），不必区分
        if resp is None or resp.over_limit or resp.status_code != 200:
            return None
        data = resp.json()
        if not data.get("success") or data.get("code") != "finished":
            return None
        content = data.get("content") or ""
        if not content:
            return None
        # 清理标题锚点 ID 残留：{#1-model-introduction} 等，仅保留标题文本
        return _MS_ANCHOR_ID_RE.sub(r"\1", content)
    except ValueError:
        # 只可能来自 resp.json()：网络错误已由 fetch_capped 归一为 None（不抛异常）
        return None


async def _fetch_files(
    client: httpx.AsyncClient, kind: str, owner: str, name: str
) -> list[dict]:
    """拉取文件列表，统一为 {path, type, size, url, updated_at} 结构（失败返回空列表降级）

    - 模型：/repo/files?Recursive=true（递归含子目录文件，含 Size）
    - 数据集：/repo/tree?Recursive=true（递归含子目录文件；接口不支持
      Recursive 参数时回退为仅顶层重试），含 Size
    - 文件下载直链：/api/v1/{kind}/{owner}/{name}/repo?Revision=&FilePath=
      （与 ModelScope SDK 使用的下载端点一致）
    """
    if kind == "models":
        url = f"{_MS_API}/{kind}/{owner}/{name}/repo/files"
        params_list: list[dict] = [{"Revision": "master", "Recursive": "true"}]
    else:
        url = f"{_MS_API}/{kind}/{owner}/{name}/repo/tree"
        params_list = [
            {"Revision": "master", "Recursive": "true"},
            {"Revision": "master"},  # 旧接口不支持 Recursive 时回退仅顶层
        ]

    for params in params_list:
        resp = await fetch_capped(
            client, url, MAX_RESPONSE_SIZE, params=params, headers=_HEADERS
        )
        if resp is None or resp.over_limit or resp.status_code != 200:
            logger.warning(f"ModelScope 文件列表拉取失败（{capped_reason(resp)}）: {url}")
            continue
        try:
            data = resp.json()
            if data.get("Code") != 200:
                logger.warning(f"ModelScope 文件列表返回异常: {data.get('Message')}")
                continue
            files = data.get("Data", {}).get("Files") or []
        except ValueError:
            continue

        entries: list[dict] = []
        for f in files:
            path = f.get("Path") or f.get("Name")
            if not path:
                continue
            entry = {
                "path": path,
                "type": f.get("Type", "blob"),
                "size": f.get("Size"),
                "url": None,
                # 最后提交时间（unix 秒时间戳，common 统一格式化为日期）
                "updated_at": f.get("CommittedDate"),
            }
            if entry["type"] in ("file", "blob"):
                entry["url"] = (
                    f"{_MS_API}/{kind}/{owner}/{name}/repo"
                    f"?Revision=master&FilePath={quote(path)}"
                )
            entries.append(entry)
        return entries
    return []


async def fetch_modelscope_doc(url: str, include_files: bool = False) -> dict:
    """从 ModelScope 拉取模型 / 数据集介绍，返回 Markdown 文本及来源信息"""
    kind, owner, name = _parse_modelscope_url(url)
    title = f"{owner}/{name}"
    source_url = f"https://modelscope.cn/{kind}/{owner}/{name}"

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        # 中文版候选优先（404 逐个跳过），未命中回退默认 README 命名
        markdown: Optional[str] = None
        used_path: Optional[str] = None
        for cand in (*ZH_README_CANDIDATES, *_README_CANDIDATES):
            text = await _fetch_raw_file(client, kind, owner, name, cand)
            if text is not None:
                markdown, used_path = text, cand
                break

        if markdown is None:
            raise AppException(
                code=404,
                message=f"未找到{ '模型' if kind == 'models' else '数据集' }介绍"
                "（README），请确认地址正确且资源公开",
            )
        check_content_size(markdown)

        # 模型：优先取魔搭平台预翻译的中文译文（页面「译文」同源接口），
        # 命中则免去管理端 AI 翻译；译文超限或拉取失败降级用原文
        used_translation = False
        if kind == "models":
            translated = await _fetch_translated_readme(client, owner, name)
            if translated:
                try:
                    check_content_size(translated)
                except AppException:
                    logger.warning("ModelScope 中文译文超过大小上限，回退原文")
                else:
                    markdown = translated
                    used_translation = True

        # 可选：附带文件列表（失败降级为空，不阻塞正文导入）
        files_included = False
        if include_files:
            files = await _fetch_files(client, kind, owner, name)
            section = render_files_section(files)
            if section:
                markdown = f"{markdown}\n\n{section}"
                files_included = True

        # 相对图片 / 链接重写为仓库 API 绝对地址
        readme_dir = posixpath.dirname(used_path or "README.md")
        resolver = _make_ms_resolver(kind, owner, name, "master", readme_dir)
        markdown = rewrite_relative_urls(markdown, resolver)

        markdown, images_transferred = await transfer_images_to_cos(client, markdown)

    language = (
        "zh"
        if used_translation or is_zh_readme(used_path or "")
        else detect_content_language(markdown)
    )
    return {
        "content": markdown,
        "title": title,
        "platform": "modelscope",
        "resource_type": "model" if kind == "models" else "dataset",
        "language": language,
        "source": "modelscope_api",
        "source_url": source_url,
        "images_transferred": images_transferred,
        "files_included": files_included,
    }
