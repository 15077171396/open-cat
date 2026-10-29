"""第三方资料拉取服务（统一入口）：按资源网址自动识别平台并分发到对应 fetcher，
拉取 README / 模型卡 / 数据集介绍 / 社区文章等 Markdown 正文供各内容模块导入。

支持平台（地址均兼容省略协议）：
- 仓库/模型类（resource_type=repo/model/dataset）：GitHub、Gitee、GitCode、
  ModelScope（/models|/datasets/{owner}/{name}）、HuggingFace（hf-mirror.com 同构），
  GitHub/Gitee 兼容 /tree/{branch}、/blob/{branch}/{path}
- 文章类（resource_type=article，详见 articles.py）：国外 X/Reddit/Medium/Dev.to/SO/HN，
  国内 知乎/掘金/CSDN/博客园/简书/微信公众号/SF/开源中国/V2EX；国外平台可配 ARTICLE_PROXY

统一返回 dict：content/title/platform/platform_label/resource_type/language/source/
source_url/images_transferred/files_included/author/summary/tags（见 _enrich）。
共同后处理：中文版优先探测、相对链接重写、白名单图片转存 COS（失败保留原 URL）、2MB 上限。
新增平台：仓库类新建 {platform}.py 并注册进 _PLATFORM_FETCHERS；文章类在 articles.py 加配置。
"""

import asyncio
import re
from typing import Callable
from urllib.parse import urlparse

from app.config import settings
from app.core.exceptions import AppException
from app.services.third_party_fetch.articles import (
    SUPPORTED_DOMESTIC,
    SUPPORTED_FOREIGN,
    resolve_article_fetcher,
)
from app.services.third_party_fetch.common import (
    PLATFORM_LABELS,
    build_summary,
    clean_tags,
)
from app.services.third_party_fetch.gitee import fetch_gitee_readme
from app.services.third_party_fetch.github import fetch_github_doc
from app.services.third_party_fetch.gitcode import fetch_gitcode_readme
from app.services.third_party_fetch.huggingface import fetch_huggingface_doc
from app.services.third_party_fetch.modelscope import fetch_modelscope_doc

# 平台分发表：域名（小写）→ fetcher(url, include_files)
# 注意与各平台 fetcher 的 URL 解析保持一致（如 www 前缀、镜像域名）
_PLATFORM_FETCHERS: dict[str, Callable[..., object]] = {
    "github.com": fetch_github_doc,
    "gitee.com": fetch_gitee_readme,
    "gitcode.com": fetch_gitcode_readme,
    "www.gitcode.com": fetch_gitcode_readme,
    "modelscope.cn": fetch_modelscope_doc,
    "www.modelscope.cn": fetch_modelscope_doc,
    "huggingface.co": fetch_huggingface_doc,
    "www.huggingface.co": fetch_huggingface_doc,
    "hf-mirror.com": fetch_huggingface_doc,
}

_REPO_PLATFORMS = "GitHub / Gitee / GitCode / ModelScope / HuggingFace"
_SUPPORTED_PLATFORMS = (
    f"{_REPO_PLATFORMS}；文章平台：国外 {SUPPORTED_FOREIGN}，国内 {SUPPORTED_DOMESTIC}"
)


def _enrich(result: dict) -> dict:
    """补全统一结构里的元信息字段（平台中文名 / 作者 / 简介 / 标签）

    各 fetcher 只在确实拿到时才填 author / tags，其余在此集中补齐，
    避免逐平台重复实现，也保证管理端「标题 / 简介 / 标签 / 出处」总有值可用：

    - platform_label：平台中文名（出处展示，如「掘金」）
    - author：仓库 / 模型类取 title 的 owner 段（owner/repo 形式）；文章类由
      fetcher 从页面 meta 或 API 字段提供
    - summary：正文自动提取的一句话简介
    - tags：无候选标签时回退平台名，保证「标签」字段可一键回填
    """
    platform = str(result.get("platform") or "")
    label = PLATFORM_LABELS.get(platform, platform)
    result["platform_label"] = result.get("platform_label") or label

    author = str(result.get("author") or "").strip()
    if not author and result.get("resource_type") in ("repo", "model", "dataset"):
        title = str(result.get("title") or "")
        if "/" in title:
            author = title.split("/", 1)[0].strip()
    result["author"] = author

    if not result.get("summary"):
        result["summary"] = build_summary(str(result.get("content") or ""))

    tags = clean_tags(result.get("tags") or [])
    result["tags"] = tags or ([label] if label else [])
    return result


async def fetch_third_party_content(url: str, include_files: bool = False) -> dict:
    """统一入口：按 URL 域名识别平台并拉取资料

    :param url: 资源网址（支持省略协议，如 github.com/owner/repo）
    :param include_files: 是否附带文件列表（ModelScope / HuggingFace 等平台支持）
    """
    text = (url or "").strip()
    if not text:
        raise AppException(code=400, message="资源地址不能为空")

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        text = "https://" + text

    try:
        host = (urlparse(text).hostname or "").lower()
    except ValueError:
        host = ""

    fetcher = _PLATFORM_FETCHERS.get(host)
    if fetcher is None:
        fetcher = resolve_article_fetcher(host)
    if fetcher is None:
        raise AppException(
            code=400,
            message=f"暂不支持该平台（{host or '无法识别'}），"
            f"目前支持 {_SUPPORTED_PLATFORMS}",
        )

    try:
        # 总耗时预算（第四轮全量审查 P1-66）：单环节超时各自有值，但**累加起来
        # 可以到分钟级**，而本服务只有 2 个 worker —— 一个慢源站就能长期占死一个。
        # 超预算时给出明确可执行的提示（重试或改为手动粘贴），而不是让前端白等。
        payload = await asyncio.wait_for(
            fetcher(text, include_files),
            timeout=settings.THIRD_PARTY_FETCH_BUDGET_SECONDS,
        )
    except asyncio.TimeoutError:
        raise AppException(
            code=502,
            message=f"该平台响应过慢（已超过 {settings.THIRD_PARTY_FETCH_BUDGET_SECONDS} 秒总预算），"
                    "请稍后重试，或直接把正文粘贴进编辑器",
        )
    return _enrich(payload)  # type: ignore[operator]
