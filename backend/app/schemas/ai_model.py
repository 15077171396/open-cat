"""AI 模型管理接口的响应模型。

沿用后台 schema 的三条约定：字段全可选（少一个键不会 500）、extra=allow
（不静默丢字段）、写操作复用 ActionAck / CreatedResult。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

_PASSTHROUGH = ConfigDict(extra="allow")


class AiModelProviderItem(BaseModel):
    model_config = _PASSTHROUGH

    name: Optional[str] = None
    display_name: Optional[str] = None
    env_prefix: Optional[str] = None


class AiModelItem(BaseModel):
    model_config = _PASSTHROUGH

    id: Optional[int] = None
    code: Optional[str] = None
    display_name: Optional[str] = None
    provider: Optional[str] = None
    provider_label: Optional[str] = None
    model_name: Optional[str] = None
    effective_model_name: Optional[str] = None
    base_url: Optional[str] = None
    effective_base_url: Optional[str] = None
    api_key_source: Optional[str] = None
    api_key_masked: Optional[str] = None
    modality: Optional[str] = None
    image_price: Optional[float] = None
    input_price: Optional[float] = None
    output_price: Optional[float] = None
    is_enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    sort_order: Optional[int] = None
    remark: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AiModelListResult(BaseModel):
    model_config = _PASSTHROUGH

    items: list[AiModelItem] = Field(default_factory=list)
    providers: list[AiModelProviderItem] = Field(default_factory=list)
    total: int = 0


class AiModelOptionItem(BaseModel):
    model_config = _PASSTHROUGH

    code: Optional[str] = None
    display_name: Optional[str] = None
    provider: Optional[str] = None
    provider_label: Optional[str] = None
    model_name: Optional[str] = None
    modality: Optional[str] = None
    image_price: Optional[float] = None
    is_enabled: Optional[bool] = None
    is_default: Optional[bool] = None


class AiModelOptionsResult(BaseModel):
    model_config = _PASSTHROUGH

    items: list[AiModelOptionItem] = Field(default_factory=list)


class AiModelProbeResult(BaseModel):
    """「测试连接」的结果。

    ⚠️ 失败也走 200 + `ok=false`：连不上是这类体检的**正常结论**，
    前端要就地把原因显示在结果行里，而不是弹个 toast 什么也没留下。
    """

    model_config = _PASSTHROUGH

    ok: Optional[bool] = None
    model: Optional[str] = None
    display_name: Optional[str] = None
    provider: Optional[str] = None
    upstream_model: Optional[str] = None
    latency_ms: Optional[int] = None
    #: 成功时的回复片段（截断 100 字，够判断「是不是这个模型」）
    reply: Optional[str] = None
    #: 失败原因（上游报错正文 / 超时 / Base URL 不合法…）
    error: Optional[str] = None
    usage: Optional[dict] = None
