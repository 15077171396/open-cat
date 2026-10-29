"""管理端 AI 写作接口：选项、SSE 执行流与提示词配置。

权限（core/rbac.py）：
- 执行面（options / run）复用内容编辑权限 `_CONTENT_EDIT_ANY` —— 写作是编辑器里的动作，
  不该要求内容管理员再拿一份 AI 专属码；
- 提示词配置复用 `ai_model:view / ai_model:edit`：它与模型管理同属「AI 行为配置」，
  只读管理员能看、能改提示词的必须有模型写权限。

SSE 约定：流开始后不再抛 HTTP 异常，所有失败都走 `event: error` —— 一旦状态码已发出，
改成 5xx 只会让前端拿到半截文本。
"""

import asyncio
import json
import time
import uuid
from contextlib import aclosing
from typing import AsyncIterator

from fastapi import Depends, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.degrade import degrade_closed
from app.core.exceptions import AppException, success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.ai_writing import WritingPromptUpdate, WritingRunRequest
from app.services import ai_usage_service
from app.services.ai_writing import service as writing_service
from app.services.ai_writing.prompts import list_prompts, reset_prompt, update_prompt

# 只有「正文/结果」事件证明上游真的产出了内容；meta/status 是流控与过程事件
# （例如 illustrations 的 status: generating），此时生成可能还没发生就失败，
# 不能据此保留全局调用额度。
_CONTENT_EVENTS = frozenset({"delta", "result", "image"})

# 硬预算必须低于 gunicorn --timeout(120s)：超时由我们自己翻译成 SSE error(504)，
# 而不是让 worker 被 master 杀掉 —— 后者前端只能看到连接中断，且无法归还额度。
STREAM_FIRST_EVENT_TIMEOUT = 20.0
STREAM_TOTAL_TIMEOUT = 100.0


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _next_event(stream, budget: float) -> tuple[str, dict]:
    """取下一个写作事件，并施加本次剩余预算。

    单独成函数而不是在路由里直接 ``await anext(...)``：内置函数不是模块属性，
    会被「管理写路由审计覆盖」守卫判成「解析不到」而红灯。
    """
    async with asyncio.timeout(max(0.0, budget)):
        return await anext(stream)


@router.get("/ai/writing/options")
async def writing_options(
    db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin),
):
    payload = await writing_service.get_options(db)
    return success_response(payload)


@router.post("/ai/writing/run")
async def run_writing(
    req: WritingRunRequest, request: Request, admin: User = Depends(require_admin),
):
    if not await writing_service.is_enabled():
        raise AppException(code=503, message="AI 写作未开启，请联系管理员")
    await writing_service.check_hourly_limit(admin.id)

    call_ticket = await ai_usage_service.reserve_quota()
    image_ticket = None
    try:
        if req.operation == "illustrate" and req.image is not None:
            image_ticket = await ai_usage_service.reserve_image_quota(req.image.count)
        # 预占成功到响应体开始之间还有一次记账；这一段的任何失败（含取消）都还没有
        # 产生用量，必须把两张票据一起归还，否则重试会把日/月计数越顶越高。
        # 只捕获后原样上抛，不吞异常，也不新增宽泛捕获。
        await ai_usage_service.record_writing_operation(req.operation)
    except BaseException:
        await ai_usage_service.release_quota(call_ticket)
        if image_ticket is not None:
            await ai_usage_service.release_image_quota(image_ticket)
        raise

    request_id = uuid.uuid4().hex[:12]
    usage: dict = {}
    produced = False
    images = 0

    async def event_stream() -> AsyncIterator[str]:
        nonlocal produced, images
        started = time.monotonic()
        try:
            yield _sse("meta", {"request_id": request_id, "operation": req.operation})
            # aclosing：超时、断线与异常都必须把上游生成器真正关掉（触发 provider/service
            # 的 finally 记账并释放 httpx 流）。只是 break 出 for 循环不会关闭它。
            async with aclosing(writing_service.run(req, usage_out=usage, admin_id=admin.id)) as stream:
                first = True
                while True:
                    # 首个事件（首 token / 首张状态）单独 20s；之后用整体 100s 的剩余预算。
                    budget = (
                        STREAM_FIRST_EVENT_TIMEOUT if first
                        else STREAM_TOTAL_TIMEOUT - (time.monotonic() - started)
                    )
                    try:
                        event, data = await _next_event(stream, budget)
                    except StopAsyncIteration:
                        break
                    except TimeoutError:
                        logger.warning(
                            f"AI 写作超时 request_id={request_id} operation={req.operation} "
                            f"first_event={first} elapsed={time.monotonic() - started:.1f}s"
                        )
                        yield _sse("error", {
                            "code": 504, "message": "AI 响应超时，请重试", "retryable": True,
                        })
                        return
                    first = False
                    if event in _CONTENT_EVENTS:
                        produced = True
                    if event == "image":
                        images += 1
                    yield _sse(event, data)
                    if await request.is_disconnected():
                        break
            # 上游「正常结束但一个字都没吐」不能落成成功的空结果：
            # 这不是 done，而是失败（额度会由 finally 归还）。
            if req.operation != "illustrate" and not produced:
                yield _sse("error", {
                    "code": 503, "message": "AI 返回空内容", "retryable": True,
                })
                return
            summary = await ai_usage_service.get_usage_summary(days=1)
            cfg = await ai_usage_service.get_quota_config()
            cost = usage.get("images_cost")
            if cost is None:
                cost = ai_usage_service.estimate_usage_cost(
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                    str(usage.get("model") or ""),
                    cfg,
                )
            yield _sse("usage", {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "images": images,
                "cost": cost,
                "remaining_calls_today": summary["limits"]["remaining_today"],
                "remaining_images_today": summary["limits"]["remaining_images"],
            })
            yield _sse("done", {
                # 上游真实结束原因（stop / length / content_filter ...）；图像链路没有
                # 这个概念，保持原行为给 stop。前端据此判断结果是否被截断。
                "finish_reason": usage.get("finish_reason") or "stop",
            })
            logger.info(
                f"AI 写作完成 request_id={request_id} admin={admin.id} "
                f"operation={req.operation} images={images} "
                f"completion_tokens={usage.get('completion_tokens', 0)}"
            )
        except AppException as exc:
            logger.warning(f"AI 写作失败 request_id={request_id} operation={req.operation}: {exc.message}")
            yield _sse("error", {"code": exc.code, "message": exc.message,
                                 "retryable": exc.code >= 500})
        except Exception as exc:  # noqa: BLE001 流已开始，必须走 error 事件而不是 500 页面
            # 未知失败的方向必须是「拒绝」：状态码在首帧发出后已无法修改，只能落成
            # 可重试的 error 事件。记录交给 degrade_closed（`[降级-拒绝]` 可按标签检索），
            # 这里只把它抛出的 AppException 翻译成 SSE 事件，不再自行记日志。
            try:
                degrade_closed(
                    f"ai_writing.stream request_id={request_id}", exc,
                    status=500, message="AI 服务异常，请稍后重试",
                )
            except AppException as closed:
                yield _sse("error", {
                    "code": closed.code, "message": closed.message, "retryable": True,
                })
        finally:
            if not produced:
                await ai_usage_service.release_quota(call_ticket)
            if image_ticket is not None:
                # 成功图片已在 pipeline 里按张 record；预留是独立计数，必须全量释放，
                # 只释放未用部分会把成功张数一直挂在预留计数上，压低下一次限额。
                await ai_usage_service.release_image_quota(image_ticket)

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/ai/writing/prompts")
async def writing_prompts(
    db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin),
):
    from app.services.ai_writing.prompts import ensure_default_prompts
    await ensure_default_prompts(db)
    rows = await list_prompts(db)
    return success_response({"items": [
        {"operation": r.operation, "display_name": r.display_name,
         "description": r.description or "", "system_prompt": r.system_prompt,
         "user_template": r.user_template, "output_type": r.output_type,
         "model_code": r.model_code or "", "temperature": float(r.temperature or 0),
         "max_tokens": r.max_tokens, "max_input_chars": r.max_input_chars,
         "is_enabled": r.is_enabled, "sort_order": r.sort_order}
        for r in rows
    ]})


@router.put("/ai/writing/prompts/{operation}")
async def writing_prompt_update(
    operation: str, payload: WritingPromptUpdate,
    db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin),
):
    try:
        await update_prompt(db, operation, payload.model_dump(exclude_unset=True, exclude_none=True), admin)
    except ValueError as exc:
        # 校验失败（操作不存在 / 模型不可用）是用户输入问题，翻译成 400 而不是 500
        raise AppException(code=400, message=str(exc))
    return success_response({"ok": True}, "提示词已保存")


@router.post("/ai/writing/prompts/{operation}/reset")
async def writing_prompt_reset(
    operation: str, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin),
):
    try:
        await reset_prompt(db, operation, admin)
    except ValueError as exc:
        raise AppException(code=400, message=str(exc))
    return success_response({"ok": True}, "已恢复默认")
