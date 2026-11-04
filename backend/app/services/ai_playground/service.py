"""AI 对话调试台服务层：模型选项、请求校验、小时限流与流式对话。

与 AI 写作的关系：写作是「编辑器里的动作」（复用内容编辑权限），调试台是
「AI 行为配置的验证工具」（模型管理页配完 Key/单价，当场对话确认通不通、
计费对不对）。二者共用 provider 抽象与用量记账，但入口独立 —— 调试台的
授权面（ai_playground:view）不该随「能编辑内容」一起下发。

SSE 语义与 AI 写作一致（见 api/admin_ai_writing.py 模块注释）：流开始后
不抛 HTTP 异常，所有失败都走 `event: error`。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.degrade import degrade_guard
from app.core.exceptions import AppException
from app.redis import redis_client
from app.services import ai_model_service, ai_usage_service
from app.services.ai import factory

# 与 AI 写作同口径：调试台是低频工具，60 次/小时挡的是「拿它当免费聊天窗口」
HOURLY_LIMIT = 60

MAX_MESSAGES = 20
MAX_MESSAGE_CHARS = 8000
MAX_SYSTEM_PROMPT_CHARS = 2000
MAX_TOKENS_CAP = 4000


async def check_hourly_limit(admin_id: int) -> None:
    """按管理员小时限流（键与 AI 写作隔离，各算各的）。

    Redis 不可用时放行并留降级日志：与 AI 写作同一取舍 —— 限流器故障不该
    把「调试模型」这个管理动作一起打死，成本护栏（全局日/月限额）仍然兜底。
    """
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    key = f"ai_playground:rate:{admin_id}:{hour}"
    count = 0
    with degrade_guard("ai_playground.check_hourly_limit", detail="Redis 不可用，小时限流放行"):
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, 3600)
    if not count:
        return
    if count > HOURLY_LIMIT:
        await redis_client.decr(key)
        raise AppException(code=429, message=f"对话调试调用过快（{HOURLY_LIMIT} 次/小时），请稍后再试")


async def get_options(db: AsyncSession) -> dict:
    """文本模型下拉：只给文本模态（对话调用的是聊天接口，图像模型选了必炸）。"""
    return await ai_model_service.list_model_options(db, modality="text")


def _validate_messages(messages: list) -> None:
    if not messages:
        raise AppException(code=400, message="至少需要一条用户消息")
    if len(messages) > MAX_MESSAGES:
        raise AppException(code=400, message=f"对话轮数过多（最多 {MAX_MESSAGES} 条）")
    for msg in messages:
        content = msg.content or ""
        if not 1 <= len(content) <= MAX_MESSAGE_CHARS:
            raise AppException(code=400, message="消息内容长度超出范围（1-8000 字符）")
    if messages[-1].role != "user":
        raise AppException(code=400, message="最后一条消息必须是用户消息")


async def run(
    req, *, usage_out: dict, admin_id: int | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """流式对话：产出 ("delta", {"text": ...}) 事件；用量与结束原因写回 usage_out。

    模型解析沿用 AI 写作的严格口径（load_enabled_model 绝不回退）：显式选择的
    模型不可用时直接 503，绝不悄悄换成别的模型 —— 换模型等于换钱也换质量。
    """
    _validate_messages(req.messages)
    temperature = 0.7 if req.temperature is None else req.temperature
    max_tokens = req.max_tokens or 2000

    if req.model_code:
        if await ai_model_service.load_enabled_model(req.model_code, modality="text") is None:
            raise AppException(code=503, message="选择的模型不可用，请重新选择")
        provider = await factory.get_provider(req.model_code)
    else:
        provider = await factory.get_provider(None)

    model_key = getattr(provider, "model_key", "") or getattr(provider, "provider_name", "") or ""
    usage_out["model"] = model_key

    messages: list[dict] = []
    if req.system_prompt:
        messages.append({"role": "system", "content": req.system_prompt})
    messages.extend({"role": m.role, "content": m.content} for m in req.messages)

    produced = False
    try:
        async for chunk in provider.stream_chat(
            messages, usage_out=usage_out, max_tokens=max_tokens, temperature=temperature,
        ):
            if chunk:
                produced = True
                yield "delta", {"text": chunk}
        if not produced:
            raise AppException(code=503, message="AI 返回空内容")
    finally:
        # openai 兼容层写 usage_out 时会 clear()（避免残留上一次的 token），
        # 顺带把这里写入的 model 也擦掉；API 层用 usage.get("model") 计价，
        # 必须在记账/返回前恢复，否则会回落到全局单价（与 AI 写作同款坑）。
        usage_out["model"] = model_key
        await ai_usage_service.record_provider_usage(provider, usage=usage_out)
