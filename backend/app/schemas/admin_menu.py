"""菜单管理（展示层覆盖）请求 / 响应模型。"""

from pydantic import BaseModel, Field


class MenuSettingEntry(BaseModel):
    key: str
    kind: str
    label: str
    label_zh: str
    label_en: str
    default_label_zh: str
    default_label_en: str
    icon: str
    default_icon: str
    visible: bool
    sort_order: int
    is_custom: bool
    route: str | None = None
    perm: str | None = None
    perms: list[str] = Field(default_factory=list)
    url: str | None = None
    module_enabled: bool = True
    items: list["MenuSettingEntry"] | None = None


class MenuSettingsData(BaseModel):
    groups: list[MenuSettingEntry]
    footer: list[MenuSettingEntry]
    custom_count: int


class MenuOverrideUpdate(BaseModel):
    """只允许改展示字段；null / 空串 = 清除该字段覆盖，回到代码默认。"""

    key: str = Field(..., min_length=1, max_length=100)
    label_zh: str | None = Field(default=None, max_length=64)
    label_en: str | None = Field(default=None, max_length=64)
    icon: str | None = Field(default=None, max_length=50)
    visible: bool | None = None


class MenuOrderItem(BaseModel):
    key: str = Field(..., min_length=1, max_length=100)
    sort_order: int = Field(..., ge=-9999, le=9999)


class MenuOrderUpdate(BaseModel):
    items: list[MenuOrderItem] = Field(..., min_length=1, max_length=200)
