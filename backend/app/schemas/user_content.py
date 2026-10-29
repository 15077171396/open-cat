"""用户中心接口的响应模型（第五轮审查 EXT-06 第二批）。

第一批覆盖了 11 条公开内容接口（`schemas/content.py`）。这一批覆盖**用户中心**：
收藏 / 学习记录 / 下载历史 / 站内消息 / 积分明细 / 邀请信息。

为什么是这批：这些响应在前端是**手写类型**的（`types/entities.ts`、
`Profile.tsx`、`Invite.tsx`），后端改字段名时没有任何一处会报错 ——
前端读到 `undefined`，表现为「卡片少一行」「按钮不见了」。

⚠️ 与第一批同一约定：所有模型 `extra="allow"`。
pydantic 默认 `extra="ignore"`，那会在 `response_model` 序列化时**丢掉未声明的字段**，
比没有 schema 更糟（接口悄悄少字段，而 OpenAPI 看起来完全正确）。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

_PASSTHROUGH = ConfigDict(extra="allow")


class FavoriteItem(BaseModel):
    """收藏条目（`/users/favorites`）。

    `available=False` 表示目标已被删除或取消发布 —— 收藏是用户主动保存的资产，
    不静默隐藏，而是标「已下架」并保留取消收藏入口。
    """

    model_config = _PASSTHROUGH

    id: int
    target_type: str
    target_id: int
    title: str
    description: Optional[str] = None
    cover: Optional[str] = None
    created_at: Optional[str] = None
    available: Optional[bool] = Field(None, description="目标是否仍可访问；false 时前端标「已下架」")


class LearningRecordItem(BaseModel):
    """学习记录条目（`/users/learning`）。

    ⚠️ `id` 就是 `tutorial_id`（前端拿它跳转），不是学习记录自身的主键 ——
    历史原因，改动会影响两端跳转，不要「顺手修正」。
    """

    model_config = _PASSTHROUGH

    id: int
    tutorial_id: Optional[int] = None
    title: str
    chapter: Optional[str] = Field(None, description="当前章节标题")
    progress: Optional[int] = None
    status: Optional[str] = None
    #: 教程是否仍公开可见。False = 已删除或已取消发布，前端标「已下架」并禁用跳转
    #: （与收藏的 `available` 同一口径，见 services/content_availability.py）
    available: Optional[bool] = None
    cover: Optional[str] = None
    last_study_at: Optional[str] = None


class ContinueLearningItem(BaseModel):
    """「继续学习」卡片（`/users/learning/continue`，最多 2 条）。"""

    model_config = _PASSTHROUGH

    id: int
    tutorial_id: Optional[int] = None
    title: str
    chapter: Optional[str] = None
    progress: Optional[int] = None
    #: `in_progress` / `completed`。前端据此把「已学完」的条目标出来
    #: （这类条目是靠「最近学过」补位进来的，不是「接着学」）
    status: Optional[str] = None
    cover: Optional[str] = None


class MessageItem(BaseModel):
    """站内消息条目（`/users/messages`）。

    时间字段叫 `time` 而不是 `created_at`（历史约定，前端按 `time` 渲染）。
    """

    model_config = _PASSTHROUGH

    id: int
    type: str = Field(description="system / comment / like / private")
    title: str
    content: str
    time: Optional[str] = None
    read: Optional[bool] = None
    avatar: Optional[str] = None
    link: Optional[str] = None


class PointTransactionItem(BaseModel):
    """积分流水条目（`/users/points/transactions`）。"""

    model_config = _PASSTHROUGH

    id: int
    points: int = Field(description="正数为收入、负数为支出")
    balance_after: Optional[int] = None
    type: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[str] = None


class InviteInfo(BaseModel):
    """邀请信息（`/users/invite/info`）。"""

    model_config = _PASSTHROUGH

    invite_code: str
    enabled: bool
    reward_points: int
    invitee_count: int
    total_reward_points: int


class InviteeItem(BaseModel):
    """邀请记录条目（`/users/invite/invitees`）。"""

    model_config = _PASSTHROUGH

    id: Optional[int] = None
    invitee_account: Optional[str] = None
    reward_points: Optional[int] = None
    created_at: Optional[str] = None
