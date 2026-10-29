"""AI 写作接口模型：请求、SSE 事件载荷与配置响应。"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

WritingOperation = Literal[
    "rewrite", "polish", "expand", "shorten", "continue",
    "summarize", "seo_description", "titles", "keywords",
    "illustrate_prompt", "illustrate",
]


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=4000)


class SelectionPayload(BaseModel):
    index: int = Field(0, ge=0)
    length: int = Field(0, ge=0)
    text: str = Field("", max_length=20000)


class ContextPayload(BaseModel):
    title: str = Field("", max_length=200)
    category: str = Field("", max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=20)
    chapter_title: str = Field("", max_length=200)
    outline: list[str] = Field(default_factory=list, max_length=100)
    article_text: str = Field("", max_length=50000)
    before: str = Field("", max_length=10000)
    after: str = Field("", max_length=10000)


class ImageRequest(BaseModel):
    # 面板里临时选择的图像模型；空值表示使用默认图像模型
    model_code: Optional[str] = Field(None, max_length=64)
    prompt: str = Field("", max_length=2000)
    alt: str = Field("", max_length=200)
    style: Literal["flat", "photo", "render", "line"] = "flat"
    size: Literal["1024*1024", "1280*720", "720*1280", "1024*768"] = "1280*720"
    count: int = Field(1, ge=1, le=4)


class WritingRunRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    operation: WritingOperation
    content_type: Literal["tutorial_node", "node", "plugin", "resource"]
    content_id: Optional[int] = None
    # 面板里临时选择的文本模型；空值表示跟随提示词配置 / 全局默认
    model_code: Optional[str] = Field(None, max_length=64)
    instruction: str = Field("", max_length=500)
    target_length: Optional[int] = Field(None, ge=50, le=5000)
    selection: Optional[SelectionPayload] = None
    context: ContextPayload = Field(default_factory=ContextPayload)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=6)
    image: Optional[ImageRequest] = None


class WritingPromptUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=50)
    system_prompt: Optional[str] = Field(None, min_length=1, max_length=20000)
    user_template: Optional[str] = Field(None, min_length=1, max_length=20000)
    output_type: Optional[Literal["markdown", "json"]] = None
    model_code: Optional[str] = Field(None, max_length=64)
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, ge=100, le=8000)
    max_input_chars: Optional[int] = Field(None, ge=500, le=50000)
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = Field(None, ge=0, le=1000)


class TitlesResult(BaseModel):
    titles: list[str] = Field(..., min_length=1, max_length=8)


class KeywordsResult(BaseModel):
    keywords: list[str] = Field(..., min_length=1, max_length=12)


class IllustratePromptResult(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    alt: str = Field("", max_length=200)


class WritingOperationItem(BaseModel):
    operation: str
    display_name: str
    output_type: str


class WritingOptionsResult(BaseModel):
    enabled: bool
    operations: list[WritingOperationItem] = Field(default_factory=list)
    text_models: list[dict] = Field(default_factory=list)
    image_model: Optional[dict] = None
    styles: list[dict] = Field(default_factory=list)
    sizes: list[str] = Field(default_factory=list)
    image_daily_limit: int = 0
    image_used_today: int = 0
    hourly_limit: int = 60
