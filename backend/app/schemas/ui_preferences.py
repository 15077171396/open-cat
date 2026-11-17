"""界面偏好与锁屏口令的请求/响应 Schema。

偏好字段只允许下列白名单（未知字段由 ``extra="ignore"`` 丢弃）；PUT 使用
``model_dump(exclude_unset=True)`` 只落调用方显式提交的项，实现增量更新。
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class UiPreferences(BaseModel):
    """用户界面偏好（31 项：12 个外观选项 + 18 个显示开关 + 新手引导完成态）。"""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    mode: Literal["light", "dark", "system"] = "light"
    layout: Literal["side", "top", "mixed", "double"] = "side"
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    primary_color: str = Field(default="#3F5CE8", pattern=r"^#[0-9a-fA-F]{6}$")
    menu_style: Literal["design", "dark", "light"] = "design"
    box_style: Literal["border", "shadow"] = "border"
    page_width: Literal["full", "fixed"] = "full"
    menu_width: int = Field(default=230, ge=180, le=320, multiple_of=10)
    tab_style: Literal["capsule", "card", "google"] = "google"
    page_transition: Literal["none", "fade", "slide-left", "slide-bottom", "slide-top"] = "slide-left"
    radius: Literal[0, 0.25, 0.5, 0.75, 1] = 0
    component_size: Literal["default", "large", "small"] = "default"

    show_tabs: bool = True
    accordion: bool = True
    show_menu_button: bool = True
    show_fast_enter: bool = True
    show_refresh_button: bool = True
    show_crumbs: bool = True
    show_language: bool = True
    show_menu_search: bool = True
    show_fullscreen: bool = True
    show_size_select: bool = True
    show_notification: bool = True
    show_nprogress: bool = True
    color_weak: bool = False
    watermark: bool = False
    show_app_logo: bool = True
    show_guide: bool = True
    gray_mode: bool = False
    ai_assistant: bool = False

    guide_done: bool = False

    def only_set_fields(self) -> dict:
        """只返回调用方显式提交的字段（未知字段已在校验阶段丢弃）。"""
        return self.model_dump(exclude_unset=True)


class PreferencesPayload(BaseModel):
    """GET 响应：``exists=False`` 时 ``preferences`` 为 None（前端保留本地值）。"""

    exists: bool
    preferences: Optional[UiPreferences] = None
    has_lock_password: bool = False


class LockPasswordRequest(BaseModel):
    """设置/更新锁屏口令（4~72 字符；字节上限由 security.validate_password_length 兜底）。"""

    password: str = Field(min_length=4, max_length=72)


class LockVerifyRequest(BaseModel):
    password: str = Field(min_length=1, max_length=72)


class LockVerifyPayload(BaseModel):
    unlock_token: str
    expires_in: int


class LockPasswordPayload(BaseModel):
    has_lock_password: bool = True
