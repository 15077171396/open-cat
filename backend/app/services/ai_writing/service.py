"""写作操作编排：模型解析、消息组装、JSON 校验、小时限流与事件流。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import AsyncIterator

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.degrade import degrade_guard
from app.core.exceptions import AppException
from app.redis import redis_client
from app.schemas.ai_writing import (
    IllustratePromptResult, ImageRequest, KeywordsResult, TitlesResult,
    WritingRunRequest,
)
from app.services import ai_model_service, ai_usage_service
from app.services.ai import factory
from app.services.ai_writing.context import build_messages
from app.services.ai_writing.prompts import (
    PromptDefault, ensure_default_prompts, get_prompt, list_prompts,
)

HOURLY_LIMIT = 60
_JSON_RESULTS = {
    "titles": TitlesResult, "keywords": KeywordsResult, "illustrate_prompt": IllustratePromptResult,
}
_IMAGE_STYLES = [
    {"value": "flat", "label": "扁平插画"},
    {"value": "photo", "label": "写实照片"},
    {"value": "render", "label": "3D 渲染"},
    {"value": "line", "label": "线稿"},
]
_IMAGE_SIZES = ["1024*1024", "1280*720", "720*1280", "1024*768"]


async def is_enabled() -> bool:
    return (await ai_usage_service.get_quota_config()).writing_enabled


async def check_hourly_limit(admin_id: int) -> None:
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    key = f"ai_writing:rate:{admin_id}:{hour}"
    count = 0
    with degrade_guard("ai_writing.check_hourly_limit", detail="Redis 不可用，小时限流放行"):
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, 3600)
    if not count:
        return
    if count > HOURLY_LIMIT:
        await redis_client.decr(key)
        raise AppException(code=429, message=f"AI 写作调用过快（{HOURLY_LIMIT} 次/小时），请稍后再试")


async def get_options(db: AsyncSession) -> dict:
    await ensure_default_prompts(db)
    cfg = await ai_usage_service.get_quota_config()
    prompts = await list_prompts(db)
    models = await ai_model_service.list_models(db)
    text_models = [
        {"code": row["code"], "display_name": row["display_name"]}
        for row in models["items"]
        if row.get("modality", "text") == "text" and row.get("is_enabled")
    ]
    image_models = [
        {
            "code": row["code"],
            "display_name": row["display_name"],
            "image_price": float(row.get("image_price") or 0),
            "is_default": bool(row.get("is_default")),
        }
        for row in models["items"]
        if row.get("modality") == "image" and row.get("is_enabled")
    ]
    summary = await ai_usage_service.get_usage_summary(days=1)
    return {
        "enabled": cfg.writing_enabled,
        "operations": [
            {"operation": p.operation, "display_name": p.display_name, "output_type": p.output_type}
            for p in prompts if p.is_enabled and p.operation != "illustrate"
        ],
        "text_models": text_models,
        "image_models": image_models,
        "image_model": await ai_model_service.get_default_image_model(db),
        "styles": _IMAGE_STYLES,
        "sizes": _IMAGE_SIZES,
        "image_daily_limit": cfg.image_daily_limit,
        "image_used_today": summary["today"]["images"],
        "hourly_limit": HOURLY_LIMIT,
    }


def _prompt_default(row) -> PromptDefault:
    return PromptDefault(
        operation=row.operation, display_name=row.display_name, description=row.description or "",
        system_prompt=row.system_prompt, user_template=row.user_template,
        output_type=row.output_type, model_code=row.model_code or "",
        # 0 是合法温度（确定性输出），只有 NULL 才回退默认值 ——
        # 用 `row.temperature or 0.7` 会把运营显式配置的 0 悄悄改成 0.7。
        temperature=float(row.temperature if row.temperature is not None else 0.7),
        max_tokens=int(row.max_tokens or 2000),
        max_input_chars=int(row.max_input_chars or 12000), sort_order=int(row.sort_order or 0),
    )


async def validate_image_request(req: WritingRunRequest | ImageRequest) -> ImageRequest:
    # 运行时调用方传 WritingRunRequest；测试与 Task 7 的流水线会直接传 ImageRequest。
    # 两种入参都接受，避免调用方为了校验再包一层。
    image = req if isinstance(req, ImageRequest) else req.image
    if image is None or not image.prompt.strip():
        raise AppException(code=400, message="请先生成或填写配图提示词")
    return image


def _validation_field_paths(exc: Exception) -> list[str]:
    """只取字段路径用于日志：Pydantic 的错误文本会带整段输入（模型全文），不能落日志。"""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return []
    paths: list[str] = []
    try:
        for item in errors():
            loc = item.get("loc") if isinstance(item, dict) else None
            if loc:
                paths.append(".".join(str(part) for part in loc))
    except (TypeError, ValueError, AttributeError, KeyError):
        # 日志辅助函数自身不能掩盖原始解析失败
        return []
    return paths


def _parse_json_result(operation: str, raw: str) -> dict:
    model = _JSON_RESULTS.get(operation)
    if model is None:
        raise AppException(code=400, message="该操作不支持 JSON 输出")
    try:
        return model.model_validate(json.loads(raw)).model_dump()
    except (ValueError, TypeError) as exc:
        # 不记录 raw / exc 文本：模型输出可能很长，且校验错误会回显整段输入。
        # 只留操作名、错误类型与字段路径，足够定位问题又不泄露正文。
        logger.warning(
            "AI 写作 JSON 解析失败 operation={} error={} fields={}",
            operation, type(exc).__name__, _validation_field_paths(exc),
        )
        raise ValueError("JSON 不合法") from exc


def _merge_usage(sink: dict, part: dict) -> None:
    for key in ("prompt_tokens", "completion_tokens"):
        sink[key] = int(sink.get(key, 0)) + int(part.get(key, 0))


async def iter_text_events(req: WritingRunRequest, prompt: PromptDefault, provider, usage_out: dict):
    messages = build_messages(prompt, req)
    kwargs = {"max_tokens": prompt.max_tokens, "temperature": prompt.temperature}
    if prompt.output_type == "json":
        raw = await provider.chat(messages, usage_out=usage_out, **kwargs)
        for attempt in range(2):
            try:
                yield "result", _parse_json_result(req.operation, raw)
                return
            except ValueError:
                if attempt == 1:
                    raise AppException(code=502, message="AI 返回格式不正确，请重试")
                repair = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": "上面的输出不是合法 JSON。请只返回符合要求的 JSON，不要解释。"},
                ]
                usage_part = {}
                raw = await provider.chat(repair, usage_out=usage_part, **kwargs)
                _merge_usage(usage_out, usage_part)
        return
    async for chunk in provider.stream_chat(messages, usage_out=usage_out, **kwargs):
        if chunk:
            yield "delta", {"text": chunk}


async def _resolve_text_provider(prompt: PromptDefault, override: str | None = None):
    """解析文本 Provider。

    模型优先级：请求级临时选择 > 提示词绑定 > 全局默认。
    有显式 model_code 时（无论来自面板还是提示词）必须严格命中启用中的同 code
    文本模型，缺失/禁用直接 503，绝不回退到别的模型（换模型=换钱也换质量，
    而且配置者看不见）。
    """
    requested = (override or "").strip() or prompt.model_code
    if not requested:
        return await factory.get_provider(None)
    if await ai_model_service.load_enabled_model(requested, modality="text") is None:
        message = (
            "选择的模型不可用，请重新选择"
            if (override or "").strip()
            else "提示词绑定的模型不可用，请到 AI 写作设置修改"
        )
        raise AppException(code=503, message=message)
    provider = await factory.get_provider(requested)
    # 防御竞态/缓存：解析结果必须是同一个 code，否则视为配置失效而不是「将就用」。
    actual = getattr(provider, "model_key", "") or getattr(provider, "provider_name", "") or ""
    if actual != requested:
        message = (
            "选择的模型不可用，请重新选择"
            if (override or "").strip()
            else "提示词绑定的模型不可用，请到 AI 写作设置修改"
        )
        raise AppException(code=503, message=message)
    return provider


async def _resolve_image_provider(code: str | None):
    """解析插图 Provider：面板选择优先，空值交给流水线取默认图像模型。"""
    requested = (code or "").strip()
    if not requested:
        return None
    if await ai_model_service.load_enabled_model(requested, modality="image") is None:
        raise AppException(code=503, message="选择的插图模型不可用，请重新选择")
    provider = await factory.get_image_provider(requested)
    actual = getattr(provider, "model_key", "") or ""
    if actual != requested:
        raise AppException(code=503, message="选择的插图模型不可用，请重新选择")
    return provider


async def run(
    req: WritingRunRequest, *, usage_out: dict, admin_id: int | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    validate_request_size(req)
    if req.operation == "illustrate":
        # 延迟导入：Task 6 先交付服务与额度，Task 7 才创建 image_pipeline；
        # 顶层导入会让 Task 6 的测试因模块不存在而无法运行。
        from app.services.ai_writing import image_pipeline
        image_req = await validate_image_request(req)
        provider = await _resolve_image_provider(image_req.model_code)
        async for event in image_pipeline.iter_images(
            image_req, provider=provider, usage_out=usage_out, admin_id=admin_id,
        ):
            yield event
        return
    prompt_row = await get_prompt_by_operation(req.operation)
    prompt = _prompt_default(prompt_row)
    provider = await _resolve_text_provider(prompt, req.model_code)
    model_key = getattr(provider, "model_key", "") or getattr(provider, "provider_name", "") or ""
    usage_out["model"] = model_key
    produced = False
    try:
        async for event in iter_text_events(req, prompt, provider, usage_out):
            produced = True
            yield event
        if not produced:
            # 上游正常结束却没有任何正文/结果：这不是成功，必须让前端拿到失败信号，
            # 否则会落成一个「done 但内容为空」的回合（用户以为模型没话说）。
            raise AppException(code=503, message="AI 返回空内容")
    finally:
        # openai 兼容层写 usage_out 时会 clear()（避免残留上一次的 token），
        # 顺带把这里写入的 model 也擦掉；API 层用 usage.get("model") 计价，
        # 必须在记账/返回前恢复，否则会回落到全局单价。
        usage_out["model"] = model_key
        await ai_usage_service.record_provider_usage(provider, usage=usage_out)


def validate_request_size(req: WritingRunRequest) -> None:
    """请求体按字段长度近似封顶，避免一次请求把内存与上游 token 一起打爆。"""
    total = len(req.context.article_text) + len(req.context.before) + len(req.context.after)
    total += len(req.selection.text) if req.selection else 0
    total += len(req.instruction) + sum(len(t.content) for t in req.history)
    if total > 256_000:
        raise AppException(code=413, message="提交给 AI 的内容过长，请缩小选区或章节范围")


async def get_prompt_by_operation(operation: str):
    from app.database import async_session
    async with async_session() as session:
        await ensure_default_prompts(session)
        row = await get_prompt(session, operation)
        if row is None or not row.is_enabled:
            raise AppException(code=400, message="该 AI 操作不可用")
        return row
