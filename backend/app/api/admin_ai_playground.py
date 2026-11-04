"""管理端 AI 对话调试台：选项与 SSE 执行流。

权限（core/rbac.py）：
- 独立权限码 `ai_playground:view`：调试台是「AI 行为配置的验证工具」，不该随
  「能编辑内容」（AI 写作的授权面）或「能改模型」（ai_model:edit）一起下发 ——
  能对话 = 能消耗全站 AI 额度，授权面必须显式。

SSE 约定与 AI 写作完全一致（见 admin_ai_writing.py 模块注释）：流开始后不再抛
HTTP 异常，所有失败都走 `event: error`；超时预算必须低于 gunicorn 120s。
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
from app.schemas.ai_playground import PlaygroundRunRequest
from app.services import ai_usage_service
from app.services.ai_playground import service as playground_service

# 只有 delta 证明上游真的产出了内容；meta 是流控事件，可能还没生成内容就失败
_CONTENT_EVENTS = frozenset({"delta"})

# 与 AI 写作同口径：硬预算必须低于 gunicorn --timeout(120s)，
# 超时由我们翻译成 SSE error(504)，而不是让 worker 被 master 杀掉
STREAM_FIRST_EVENT_TIMEOUT = 20.0
STREAM_TOTAL_TIMEOUT = 100.0


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _next_event(stream, budget: float) -> tuple[str, dict]:
    """取下一个对话事件，并施加本次剩余预算。

    单独成函数而不是在路由里直接 ``await anext(...)``：内置函数不是模块属性，
    会被「管理写路由审计覆盖」守卫判成「解析不到」而红灯。
    """
    async with asyncio.timeout(max(0.0, budget)):
        return await anext(stream)


@router.get("/ai/playground/options")
async def playground_options(
    db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin),
):
    """文本模型下拉（含禁用项，前端标灰）+ 默认模型，供调试台选择。"""
    return success_response(await playground_service.get_options(db))


@router.post("/ai/playground/run")
async def run_playground(
    req: PlaygroundRunRequest, request: Request, admin: User = Depends(require_admin),
):
    await playground_service.check_hourly_limit(admin.id)

    call_ticket = await ai_usage_service.reserve_quota()
    # 预占成功到响应体开始之间的失败必须归还票据，否则重试会把日/月计数越顶越高
    # （与 AI 写作同一守卫：只捕获后原样上抛，不吞异常）
    request_id = uuid.uuid4().hex[:12]
    usage: dict = {}
    produced = False

    async def event_stream() -> AsyncIterator[str]:
        nonlocal produced
        started = time.monotonic()
        try:
            yield _sse("meta", {"request_id": request_id})
            async with aclosing(
                playground_service.run(req, usage_out=usage, admin_id=admin.id)
            ) as stream:
                first = True
                while True:
                    # 首个事件单独 20s；之后用整体 100s 的剩余预算
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
                            f"AI 对话调试超时 request_id={request_id} admin={admin.id} "
                            f"first_event={first} elapsed={time.monotonic() - started:.1f}s"
                        )
                        yield _sse("error", {
                            "code": 504, "message": "AI 响应超时，请重试", "retryable": True,
                        })
                        return
                    first = False
                    if event in _CONTENT_EVENTS:
                        produced = True
                    yield _sse(event, data)
                    if await request.is_disconnected():
                        break
            if not produced:
                yield _sse("error", {
                    "code": 503, "message": "AI 返回空内容", "retryable": True,
                })
                return
            summary = await ai_usage_service.get_usage_summary(days=1)
            cfg = await ai_usage_service.get_quota_config()
            cost = ai_usage_service.estimate_usage_cost(
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
                str(usage.get("model") or ""),
                cfg,
            )
            yield _sse("usage", {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "cost": cost,
                "remaining_calls_today": summary["limits"]["remaining_today"],
            })
            yield _sse("done", {
                "finish_reason": usage.get("finish_reason") or "stop",
            })
            logger.info(
                f"AI 对话调试完成 request_id={request_id} admin={admin.id} "
                f"model={usage.get('model', '')} completion_tokens={usage.get('completion_tokens', 0)}"
            )
        except AppException as exc:
            logger.warning(f"AI 对话调试失败 request_id={request_id}: {exc.message}")
            yield _sse("error", {"code": exc.code, "message": exc.message,
                                 "retryable": exc.code >= 500})
        except Exception as exc:  # noqa: BLE001 流已开始，必须走 error 事件而不是 500 页面
            # 未知失败的方向必须是「拒绝」：状态码在首帧发出后已无法修改。
            # 记录交给 degrade_closed（`[降级-拒绝]` 可按标签检索），这里只翻译成 SSE 事件。
            try:
                degrade_closed(
                    f"ai_playground.stream request_id={request_id}", exc,
                    status=500, message="AI 服务异常，请稍后重试",
                )
            except AppException as closed:
                yield _sse("error", {
                    "code": closed.code, "message": closed.message, "retryable": True,
                })
        finally:
            if not produced:
                await ai_usage_service.release_quota(call_ticket)

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
