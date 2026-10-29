"""图片搜索接口的请求模型。"""

from typing import Literal

from pydantic import BaseModel, Field


class ImageImportRequest(BaseModel):
    source: Literal[
        "pexels", "pixabay", "giphy", "tenor", "so360", "baidu", "sogou",
        "duitang", "doutupk", "adoutu", "memecub", "emoji", "local",
    ]
    source_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    alt: str | None = Field(None, max_length=300)
    query: str | None = Field(None, max_length=200)
