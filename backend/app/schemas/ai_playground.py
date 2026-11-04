"""AI 对话调试台接口模型：请求载荷。"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

PlaygroundRole = Literal["user", "assistant"]


class PlaygroundTurn(BaseModel):
    role: PlaygroundRole
    content: str = Field(..., min_length=1, max_length=8000)


class PlaygroundRunRequest(BaseModel):
    """一次对话请求：messages 为完整上下文（调试台无服务端会话，前端持有历史）。"""

    model_config = ConfigDict(extra="ignore")

    # 面板里临时选择的文本模型；空值表示全局默认模型
    model_code: Optional[str] = Field(None, max_length=64)
    # 可选系统提示词：调试「人设 / 输出格式」时用，不落库
    system_prompt: str = Field("", max_length=2000)
    messages: list[PlaygroundTurn] = Field(..., min_length=1, max_length=20)
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, ge=1, le=4000)
