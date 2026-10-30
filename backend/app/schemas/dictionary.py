"""字典管理请求 / 响应模型。"""

from pydantic import BaseModel, Field


class DictTypeCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=80)
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    is_enabled: bool = True


class DictTypeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    is_enabled: bool | None = None


class DictItemCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)
    value: str = Field(..., min_length=1, max_length=100)
    sort_order: int = 0
    is_enabled: bool = True
    is_default: bool = False
    remark: str | None = Field(default=None, max_length=255)


class DictItemUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    value: str | None = Field(default=None, min_length=1, max_length=100)
    sort_order: int | None = None
    is_enabled: bool | None = None
    is_default: bool | None = None
    remark: str | None = Field(default=None, max_length=255)


class DictTypeRow(BaseModel):
    id: int
    code: str
    name: str
    description: str | None = None
    is_enabled: bool
    item_count: int = 0


class DictTypeListData(BaseModel):
    items: list[DictTypeRow]


class DictItemRow(BaseModel):
    id: int
    type_id: int
    label: str
    value: str
    sort_order: int
    is_enabled: bool
    is_default: bool
    remark: str | None = None
