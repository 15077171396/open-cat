"""管理后台 · 站内消息通知

- GET  /admin/messages   已发通知批次列表（按 batch_id 聚合，含送达数/已读数）
- POST /admin/messages   群发系统通知（全站 / 指定用户）

为什么发送权限单独成码（`message:send`）：这是**面向全体用户**的触达能力，
发错一次就是全站可见事故，与「查看已发记录」不是同一类授权。
"""

from typing import Optional

from fastapi import Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.permissions import require_admin
from app.core.exceptions import success_response
from app.database import get_db
from app.models.user import User
from app.schemas.admin import ActionAck, MessageBatchItem, SendMessageResult
from app.schemas.common import ApiResponse
from app.schemas.content import PageResult
from app.services.admin_audit_service import record_admin_action
from app.services.admin_message_service import list_message_batches, send_system_message
from app.services import message_template_service


class MessageTemplateItem(BaseModel):
    """一条站内通知模板（name 唯一，作为套用/删除的标识）"""

    name: str = Field(..., min_length=1, max_length=50)
    title: str = Field(..., min_length=1, max_length=100)
    content: str = Field(..., min_length=1, max_length=2000)


class MessageTemplatesSaveRequest(BaseModel):
    """整份覆盖保存模板清单（前端把增删改后的清单整体提交）"""

    templates: list[MessageTemplateItem] = Field(default_factory=list, max_length=20)


@router.get("/message-templates", response_model=ApiResponse[ActionAck])
async def admin_list_message_templates(
    admin: User = Depends(require_admin),
):
    """站内通知模板清单（群发弹窗「套用模板」下拉的数据源；message:view 即可读）。"""
    return success_response({"templates": message_template_service.list_templates()})


@router.put("/message-templates", response_model=ApiResponse[ActionAck])
async def admin_save_message_templates(
    req: MessageTemplatesSaveRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """整份保存通知模板（上限 20 条；写审计 message_template_update）。

    模板内容最终会以管理员名义群发给用户，增删与发送同级授权（message:send）。
    """
    result = await message_template_service.save_with_audit(
        db, admin, [item.model_dump() for item in req.templates]
    )
    return success_response({"ok": True, "changed": result["changed"], "templates": result["templates"]})


class SendMessageRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=60, description="通知标题")
    content: str = Field(..., min_length=1, max_length=1000, description="通知正文")
    link: Optional[str] = Field(
        None, max_length=500, description="站内跳转路径（以 / 开头，可空）"
    )
    target: str = Field("all", pattern="^(all|users)$", description="all=全站 / users=指定用户")
    user_ids: Optional[list[int]] = Field(
        None, max_length=500, description="target=users 时的接收用户 ID 列表"
    )


@router.get("/messages", response_model=ApiResponse[PageResult[MessageBatchItem]])
async def admin_list_messages_api(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """已发送的系统通知（按批次聚合）。

    只列人工群发的批次：内容下架、评论回复这类自动通知是业务操作的副产品，
    混进「运营发过什么」的清单里只会干扰判断。
    """
    result = await list_message_batches(db, page, page_size)
    return success_response(result)


@router.post("/messages", response_model=ApiResponse[SendMessageResult])
async def admin_send_message_api(
    req: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """群发系统通知（写入每个接收用户的站内消息）。"""
    result = await send_system_message(
        db,
        admin.id,
        title=req.title,
        content=req.content,
        link=req.link,
        target=req.target,
        user_ids=req.user_ids,
    )
    # 审计：全站触达属于「发错一次就是事故」的操作，必须能回答
    # 「谁在什么时候发了什么、发给多少人」。detail 只放规模与目标，不放正文
    #（正文可能很长，且审计表按 365 天保留）。
    record_admin_action(
        db,
        admin,
        "message_send",
        target_type="message",
        summary=f"发送系统通知《{req.title[:40]}》给 {result['sent_count']} 人",
        detail={
            "batch_id": result["batch_id"],
            "target": req.target,
            "sent_count": result["sent_count"],
            "link": req.link or None,
        },
    )
    return success_response(result, f"已发送给 {result['sent_count']} 位用户")
