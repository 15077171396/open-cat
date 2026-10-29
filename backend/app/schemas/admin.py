"""后台与杂项接口的响应模型（第五轮审查 EXT-06 第三批）。

前两批覆盖 23 条「前端手写类型、消费方多」的接口。这一批把剩下的
`ApiResponse[dict]` 全部替换掉 —— 目标是**每一条路由在 OpenAPI 里都有名字**，
前端可以直接生成类型，而不是面对一堆裸 `object`。

## 字段是怎么来的

不是猜的：`Announcement` / `Feedback` / `AdminAuditLog` / `RedemptionCode` /
`RedemptionRecord` / `ContentComment` / `Role` / `Department` / `Permission`
这些模型的字段直接取自 **ORM 表的列名**（后台列表就是把这些列序列化出去的）。

## 三条安全约定

1. **所有字段可选**（`Optional[...] = None`）。响应模型一旦有必填字段，
   某个接口少返回一个键就会 500 —— 那是比「没有 schema」严重得多的事故。
2. **一律 `extra="allow"`**。pydantic 默认 `extra="ignore"` 会静默丢掉未声明字段，
   等于接口悄悄少字段，而 OpenAPI 看起来完全正确。
3. **列表统一 `PageResult[T]`**（见 `schemas/content.py`），不另造分页形状。

因此这一批是**纯增量**：OpenAPI 多了类型，运行期行为与字段一字不变。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

_PASSTHROUGH = ConfigDict(extra="allow")


class _Item(BaseModel):
    """所有条目/结果模型的共同基类：全字段可选 + 透传。"""

    model_config = _PASSTHROUGH


# ---------------------------------------------------------------------------
# 写操作的返回
# ---------------------------------------------------------------------------


class CreatedResult(_Item):
    """新建成功后的返回（多数后台 create 只回一个 id）。"""

    id: Optional[int] = None
    message: Optional[str] = None


class DeletedResult(_Item):
    """删除/批量删除的返回。"""

    deleted: Optional[int] = Field(None, description="实际删除条数")
    id: Optional[int] = None
    message: Optional[str] = None


class AffectedResult(_Item):
    """批量操作的返回（批量发布/批量禁用等）。"""

    updated: Optional[int] = None
    deleted: Optional[int] = None
    disabled: Optional[int] = None
    count: Optional[int] = None
    message: Optional[str] = None


class ActionAck(_Item):
    """动作类接口的返回（审核通过/驳回、标记已读、上下架切换…）。

    这类接口的返回结构最不稳定（有的回 `{ok: true}`、有的回切换后的状态），
    所以这里不声明具体字段，靠 `extra="allow"` 原样透传 —— 只为了让 OpenAPI
    里有个**名字**，而不是裸 object。
    """


# ---------------------------------------------------------------------------
# 后台各域条目
# ---------------------------------------------------------------------------


class AnnouncementItem(_Item):
    """公告（`/admin/announcements`、`/announcements`）。"""

    id: Optional[int] = None
    title: Optional[str] = None
    content: Optional[str] = None
    tag: Optional[str] = None
    color: Optional[str] = None
    is_published: Optional[bool] = None
    published_at: Optional[str] = None
    # 定时发布时间（NULL=立即；未来时间=到点才可见）
    publish_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class FeedbackItem(_Item):
    """用户反馈（`/admin/feedbacks`）。"""

    id: Optional[int] = None
    user_id: Optional[int] = None
    content: Optional[str] = None
    contact: Optional[str] = None
    # 截图 URL 列表（本站 COS/CDN 地址），管理端展示为可点开的缩略图
    images: Optional[Any] = None
    status: Optional[str] = None
    admin_note: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class MessageBatchItem(_Item):
    """已发送的系统通知批次（`/admin/messages`，按 batch_id 聚合）。"""

    batch_id: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    link: Optional[str] = None
    sent_count: Optional[int] = None
    read_count: Optional[int] = None
    created_at: Optional[str] = None


class SendMessageResult(_Item):
    """群发系统通知的返回。"""

    batch_id: Optional[str] = None
    sent_count: Optional[int] = None


class AuditLogItem(_Item):
    """管理操作审计（`/admin/audit-logs`）。"""

    id: Optional[int] = None
    admin_id: Optional[int] = None
    admin_account: Optional[str] = None
    action: Optional[str] = None
    target_type: Optional[str] = None
    target_id: Optional[int] = None
    summary: Optional[str] = None
    detail: Optional[Any] = None
    ip: Optional[str] = None
    created_at: Optional[str] = None


class RedemptionCodeItem(_Item):
    """兑换码（`/admin/redemption-codes`）。"""

    id: Optional[int] = None
    code: Optional[str] = None
    points: Optional[int] = None
    max_uses: Optional[int] = None
    used_count: Optional[int] = None
    status: Optional[str] = None
    expires_at: Optional[str] = None
    note: Optional[str] = None
    created_at: Optional[str] = None


class RedemptionRecordItem(_Item):
    """兑换记录（`/admin/redemption-codes/{id}/records`）。"""

    id: Optional[int] = None
    code_id: Optional[int] = None
    code: Optional[str] = None
    user_id: Optional[int] = None
    points: Optional[int] = None
    created_at: Optional[str] = None


class AdminCommentItem(_Item):
    """评论（后台视角：含 `deleted_at` 软删标记）。"""

    id: Optional[int] = None
    target_type: Optional[str] = None
    target_id: Optional[int] = None
    user_id: Optional[int] = None
    parent_id: Optional[int] = None
    content: Optional[str] = None
    images: Optional[Any] = None
    deleted_at: Optional[str] = None
    created_at: Optional[str] = None


class RoleItem(_Item):
    """RBAC 角色。"""

    id: Optional[int] = None
    code: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    is_system: Optional[bool] = None
    created_at: Optional[str] = None


class DepartmentItem(_Item):
    """RBAC 部门。"""

    id: Optional[int] = None
    name: Optional[str] = None
    parent_id: Optional[int] = None
    sort_order: Optional[int] = None
    created_at: Optional[str] = None


class PermissionItem(_Item):
    """RBAC 权限项。"""

    id: Optional[int] = None
    code: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    module: Optional[str] = None
    module_name: Optional[str] = None
    sort: Optional[int] = None


class AdminUserItem(_Item):
    """后台用户列表条目（含 RBAC 角色与部门）。"""

    id: Optional[int] = None
    account: Optional[str] = None
    nickname: Optional[str] = None
    role: Optional[str] = None
    points: Optional[int] = None
    is_active: Optional[bool] = None
    vip_expire_time: Optional[str] = None
    created_at: Optional[str] = None
    # 软删除时间：非空 = 该账号已注销/被删除。列表页据此显示「已注销」标记与「恢复」入口
    deleted_at: Optional[str] = None
    roles: Optional[Any] = Field(None, description="角色码列表（RBAC）")
    departments: Optional[Any] = None


class ModerationLogItem(_Item):
    """机审留痕。"""

    id: Optional[int] = None
    user_id: Optional[int] = None
    target_type: Optional[str] = None
    content: Optional[str] = None
    action: Optional[str] = None
    hits: Optional[Any] = None
    reviewed: Optional[bool] = None
    created_at: Optional[str] = None


class ReportItem(_Item):
    """举报（`/admin/reports`、`/reports/mine`）。

    ⚠️ 字段必须与 `report_service._report_to_dict` 逐字对齐。此前这里声明的是
    `user_id` / `target_type` / `description`（service 从不产出），而 service 产出的
    `reporter_id` / `content_type` / `detail` 靠 `extra="allow"` 透传，结果是：
    每条举报都带 3 个恒为 null 的字段；更糟的是 `target_id` 被声明成 `int` 而库里是
    `String(64)`，Pydantic 会把 "11" 强转成 11，前端 `String(targetId) === target_id`
    永远不成立 →「已举报过」的入口抑制静默失效。
    """

    id: Optional[int] = None
    reporter_id: Optional[int] = None
    content_type: Optional[str] = None
    content_type_label: Optional[str] = None
    # 与 `content_reports.target_id`（String(64)）同类型，不要改成 int
    target_id: Optional[str] = None
    reason: Optional[str] = None
    reason_label: Optional[str] = None
    detail: Optional[str] = None
    status: Optional[str] = None
    status_label: Optional[str] = None
    handler_id: Optional[int] = None
    handled_at: Optional[str] = None
    handle_note: Optional[str] = None
    created_at: Optional[str] = None


# ---------------------------------------------------------------------------
# 杂项：统计 / 设置 / 上传 / 工具运行
# ---------------------------------------------------------------------------


class StatsResult(_Item):
    """站点统计（`/stats`、`/nodes/stats`、`/resources/stats`、`/users/stats` …）。

    各端点字段完全不同（`total` / `total_users` / `in_progress` / `orphans`…），
    所以这里**一个字段都不声明**，统一靠 `_Item` 的 `extra="allow"` 原样透传。

    ⚠️ 曾经声明了 `total` / `categories` / `types` 三个"共有"字段，实际只有少数端点返回
    其中之一，其余响应里就恒挂 3 个 `null`（`{"total":null,"categories":null,...}`），
    既污染契约又增大体积。声明不存在的字段 = 凭空造 null，不要再加回来。
    """


class UploadResult(_Item):
    """上传成功后的返回（图片/文件/评论配图/头像）。"""

    url: Optional[str] = None
    file_url: Optional[str] = None
    avatar_url: Optional[str] = None


class SettingsPayload(_Item):
    """设置接口返回（公开设置 / 后台设置）。"""

    values: Optional[Any] = None
    definitions: Optional[Any] = None
    groups: Optional[Any] = None


class LegalDocument(_Item):
    """法务文书（`/settings/legal/{doc_type}`）。"""

    title: Optional[str] = None
    content: Optional[str] = None
    doc_type: Optional[str] = None
