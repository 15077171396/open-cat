"""GitHub 适配器：复用 github_readme_service 多源拉取能力，适配为第三方拉取统一结构。
新增平台适配器参照本模块：实现 fetch(url, include_files) -> dict 并在分发表注册。"""

from app.services import github_readme_service
from app.services.third_party_fetch.common import (
    detect_content_language,
    is_zh_readme,
)


async def fetch_github_doc(url: str, include_files: bool = False) -> dict:
    """从 GitHub 拉取 README（复用既有服务），适配统一返回结构"""
    result = await github_readme_service.fetch_github_readme(url)
    return {
        "content": result["content"],
        "title": result["repo"],
        "platform": "github",
        "resource_type": "repo",
        # 文件名探测未命中时按正文内容兜底判断（英文 README 偶含中文段落不误判）
        "language": (
            "zh"
            if is_zh_readme(result.get("path"))
            else detect_content_language(result["content"])
        ),
        "source": result["source"],
        "source_url": result["source_url"],
        "images_transferred": result["images_transferred"],
        "files_included": False,
    }
