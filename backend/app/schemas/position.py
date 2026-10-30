"""岗位管理请求 / 响应模型。"""

from pydantic import BaseModel, Field


class PositionCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=1000)
    sort_order: int = 0
    is_enabled: bool = True


class PositionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=1000)
    sort_order: int | None = None
    is_enabled: bool | None = None


class PositionRow(BaseModel):
    id: int
    code: str
    name: str
    description: str | None = None
    sort_order: int
    is_enabled: bool


class PositionListData(BaseModel):
    items: list[PositionRow]
