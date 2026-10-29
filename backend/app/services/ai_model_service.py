"""AI 模型目录：后台 CRUD + 运行时解析的**唯一事实来源**。

职责边界：
- `services/ai/registry.py` 只管协议实现（deepseek/openai/qwen 及其 env 前缀）；
- 本模块管「有哪些可调用模型」：Key / BaseURL / 上游模型名 / 单价 / 启停 / 默认；
- `ai_config_service` 负责把本模块解析出的配置缓存起来并交给 factory 建实例。

安全约定：
- API Key 只以 `encrypt_secret()` 的密文落库，列表接口只回显末四位掩码；
- BaseURL 复用设置页的 SSRF/主机白名单校验（`setting_service.validate_ai_base_url`）；
- 所有写操作只审计字段名，绝不记录 Key / BaseURL 的值。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.crypto import encrypt_secret, looks_like_mask, mask_secret
from app.core.degrade import degrade_guard
from app.core.exceptions import AppException
from app.database import async_session
from app.models.ai_model import AiModel
from app.models.ai_writing_prompt import AiWritingPrompt
from app.models.tool import Tool
from app.models.user import User
from app.services.admin_audit_service import record_admin_action
from app.services.ai.registry import (
    provider_constants,
    provider_meta,
    registered_provider_names,
)

_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_PRICE = 1_000_000.0
_MAX_SORT = 1_000_000
_MAX_REMARK = 500


@dataclass(frozen=True)
class ModelRuntimeMaterial:
    """缓存/解析用的模型原始材料（Key 保持密文，解密只发生在进程内）"""

    code: str
    display_name: str
    provider: str
    model_name: str
    base_url: str
    api_key_enc: str
    input_price: float
    output_price: float
    modality: str = "text"
    image_price: float = 0.0

    def as_cache_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "display_name": self.display_name,
            "provider": self.provider,
            "model_name": self.model_name,
            "base_url": self.base_url,
            "api_key_enc": self.api_key_enc,
            "input_price": self.input_price,
            "output_price": self.output_price,
            "modality": self.modality,
            "image_price": self.image_price,
        }

    @classmethod
    def from_cache_dict(cls, data: dict) -> "ModelRuntimeMaterial":
        return cls(
            code=str(data.get("code") or ""),
            display_name=str(data.get("display_name") or ""),
            provider=str(data.get("provider") or ""),
            model_name=str(data.get("model_name") or ""),
            base_url=str(data.get("base_url") or ""),
            api_key_enc=str(data.get("api_key_enc") or ""),
            input_price=float(data.get("input_price") or 0),
            output_price=float(data.get("output_price") or 0),
            # 旧缓存没有这两个键：按文本模型材料读，避免升级瞬间图像字段串味
            modality=str(data.get("modality") or "text"),
            image_price=float(data.get("image_price") or 0),
        )


# ---------------- Provider 元信息 ----------------


def provider_display_name(provider: str) -> str:
    for name, cls, _meta in provider_constants():
        if name == provider:
            return str(getattr(cls, "display_name", "") or name)
    return provider


def providers_payload() -> list[dict]:
    """供应商下拉：名称 / 可读名 / 模态 / env 前缀（不含任何凭据）"""
    return [
        {
            "name": name,
            "display_name": str(getattr(cls, "display_name", "") or name),
            "env_prefix": meta.env_prefix,
            "modality": meta.modality,
        }
        for name, cls, meta in provider_constants()
    ]


def env_values(provider: str) -> tuple[str, str, str]:
    """供应商的 (API Key, Base URL, 模型名) 环境变量兜底值"""
    meta = provider_meta(provider)
    prefix = meta.env_prefix if meta else ""
    if not prefix:
        return "", "", ""
    return (
        getattr(settings, f"{prefix}_API_KEY", "") or "",
        getattr(settings, f"{prefix}_BASE_URL", "") or "",
        getattr(settings, f"{prefix}_MODEL", "") or "",
    )


# ---------------- 运行时解析（无缓存；缓存由 ai_config_service 负责） ----------------


def _material_from_row(row: AiModel) -> ModelRuntimeMaterial:
    return ModelRuntimeMaterial(
        code=row.code,
        display_name=row.display_name,
        provider=row.provider,
        model_name=row.model_name or "",
        base_url=row.base_url or "",
        api_key_enc=row.api_key_enc or "",
        input_price=float(row.input_price or 0),
        output_price=float(row.output_price or 0),
        modality=str(row.modality or "text"),
        image_price=float(row.image_price or 0),
    )


async def _select_model_row(
    session: AsyncSession, ref: str | None, modality: str = "text"
) -> AiModel | None:
    """解析规则：code → 供应商默认模型 → 全局默认模型（均限同模态）"""
    if ref:
        row = (
            await session.execute(
                select(AiModel).where(
                    AiModel.code == ref, AiModel.modality == modality
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            if row.is_enabled:
                return row
            logger.warning(f"AI 模型 {ref!r} 已禁用，回退同供应商默认模型")
            fallback = (
                await session.execute(
                    select(AiModel)
                    .where(
                        AiModel.provider == row.provider,
                        AiModel.modality == modality,
                        AiModel.is_enabled.is_(True),
                    )
                    .order_by(
                        AiModel.is_default.desc(),
                        AiModel.sort_order,
                        AiModel.id,
                    )
                    .limit(1)
                )
            ).scalars().first()
            if fallback is not None:
                return fallback
            ref = None
        elif ref in registered_provider_names(modality):
            row = (
                await session.execute(
                    select(AiModel)
                    .where(
                        AiModel.provider == ref,
                        AiModel.modality == modality,
                        AiModel.is_enabled.is_(True),
                    )
                    .order_by(
                        AiModel.is_default.desc(),
                        AiModel.sort_order,
                        AiModel.id,
                    )
                    .limit(1)
                )
            ).scalars().first()
            if row is not None:
                return row
            return None
        else:
            logger.warning(f"未知的 AI 模型 {ref!r}，回退全局默认模型")
            ref = None

    if ref is None:
        row = (
            await session.execute(
                select(AiModel)
                .where(
                    AiModel.modality == modality,
                    AiModel.is_enabled.is_(True),
                )
                .order_by(
                    AiModel.is_default.desc(),
                    AiModel.sort_order,
                    AiModel.id,
                )
                .limit(1)
            )
        ).scalars().first()
        return row

    return None


async def load_model_runtime(
    ref: str | None, modality: str = "text"
) -> ModelRuntimeMaterial | None:
    """从库里解析运行时模型；表为空 / DB 不可用时返回 None（调用方回退 env）"""
    row = None
    with degrade_guard(
        "ai_model.load_runtime",
        detail="读取 AI 模型目录失败，回退供应商环境变量配置",
    ):
        async with async_session() as session:
            row = await _select_model_row(session, ref, modality=modality)
    return _material_from_row(row) if row is not None else None


async def load_enabled_model(
    code: str, modality: str = "text"
) -> ModelRuntimeMaterial | None:
    """**严格**解析：只认 code 精确命中、同模态且启用中的模型；缺失/禁用一律 None。

    与 :func:`load_model_runtime` 的区别是**绝不回退**：后者会把禁用/未知的 code
    替换成供应商默认或全局默认。提示词绑定的模型必须走这个入口 —— 悄悄换模型
    既改变成本也改变输出质量，而且配置者完全看不见。
    """
    if not code:
        return None
    row = None
    # 读库失败按「不可用」处理（调用方翻译成 503）：不能把读不到当成「可以回退」。
    with degrade_guard(
        "ai_model.load_enabled",
        detail="读取绑定的 AI 模型失败，按不可用处理",
    ):
        async with async_session() as session:
            row = (
                await session.execute(
                    select(AiModel).where(
                        AiModel.code == code,
                        AiModel.modality == modality,
                        AiModel.is_enabled.is_(True),
                    )
                )
            ).scalar_one_or_none()
    return _material_from_row(row) if row is not None else None


async def list_model_prices(session: AsyncSession | None = None) -> dict[str, tuple[float, float]]:
    """全部模型的单价表（含已禁用：历史用量重算仍需旧单价）"""
    stmt = select(AiModel.code, AiModel.input_price, AiModel.output_price)
    rows = []
    with degrade_guard(
        "ai_model.list_prices",
        detail="读取模型单价失败，回退全局兜底单价",
    ):
        if session is not None:
            rows = (await session.execute(stmt)).all()
        else:
            async with async_session() as own_session:
                rows = (await own_session.execute(stmt)).all()
    table: dict[str, tuple[float, float]] = {}
    for item in rows:
        try:
            code, input_price, output_price = item
            in_price = float(input_price or 0)
            out_price = float(output_price or 0)
        except (TypeError, ValueError):
            continue
        if in_price > 0 or out_price > 0:
            table[str(code)] = (in_price, out_price)
    return table


# ---------------- CRUD ----------------


def _clean_str(value: Any, *, max_length: int, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AppException(code=400, message=f"{field}必须是字符串")
    value = value.strip()
    if len(value) > max_length:
        raise AppException(code=400, message=f"{field}不能超过 {max_length} 个字符")
    return value


def _clean_price(value: Any, field: str) -> float:
    if value is None or value == "":
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AppException(code=400, message=f"{field}必须是数字")
    if not math.isfinite(number) or number < 0 or number > _MAX_PRICE:
        raise AppException(code=400, message=f"{field}必须在 0 到 {_MAX_PRICE:g} 之间")
    return number


def _clean_provider(value: Any) -> str:
    provider = _clean_str(value, max_length=30, field="供应商")
    if provider not in registered_provider_names():
        raise AppException(
            code=400,
            message=f"未知的 AI 供应商：{provider or '(空)'}，可选："
            + " / ".join(registered_provider_names()),
        )
    return provider


def _clean_base_url(value: Any) -> str:
    base_url = _clean_str(value, max_length=500, field="Base URL")
    if not base_url:
        return ""
    from app.services.setting_service import validate_ai_base_url

    try:
        return validate_ai_base_url(base_url, "Base URL")
    except ValueError as exc:
        raise AppException(code=400, message=str(exc))


#: 必须自带接入点的供应商：没有 base_url 就无从拼 `/chat/completions` 或 `/images/generations`
_CUSTOM_PROVIDERS = {"custom": "自定义（OpenAI 兼容）", "custom_image": "自定义图像（OpenAI 兼容）"}


def _require_custom_endpoint(provider: str, base_url: str, model_name: str) -> None:
    """自定义供应商必须同时给 base_url 与上游模型名。

    不校验的话会一路静默到调用期：base_url 空 → httpx 请求拼成相对路径 →
    报一个和「配置漏填」毫无关系的错，运营只能靠猜。
    """
    label = _CUSTOM_PROVIDERS.get(provider)
    if label is None:
        return
    missing = []
    if not base_url:
        missing.append("Base URL")
    if not model_name:
        missing.append("上游模型名")
    if missing:
        raise AppException(
            code=400,
            message=f"「{label}」必须填 " + " 与 ".join(missing)
            + "：Base URL 例如 https://api.openai.com/v1，上游模型名以服务商文档为准",
        )


def _assert_provider_modality(provider: str, modality: str) -> None:
    """供应商与模态必须匹配：文本 Provider 出不了图，图像 Provider 也不会聊天"""
    meta = provider_meta(provider)
    if meta is None or meta.modality != modality:
        raise AppException(code=400, message=f"供应商 {provider} 不支持 {modality} 模态")


def _clean_code(value: Any) -> str:
    code = _clean_str(value, max_length=64, field="模型标识").lower()
    if not code:
        return ""
    if not _CODE_RE.match(code):
        raise AppException(
            code=400,
            message="模型标识只能包含小写字母、数字、点、下划线和短横线，且不能以符号开头",
        )
    return code


def _clean_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise AppException(code=400, message=f"{field}必须是布尔值")


def _normalize_payload(data: dict, *, partial: bool) -> dict:
    out: dict = {}
    if not partial or "display_name" in data:
        display_name = _clean_str(data.get("display_name"), max_length=100, field="展示名")
        if not display_name:
            raise AppException(code=400, message="展示名不能为空")
        out["display_name"] = display_name
    if not partial or "provider" in data:
        out["provider"] = _clean_provider(data.get("provider"))
    if "model_name" in data or not partial:
        out["model_name"] = _clean_str(
            data.get("model_name"), max_length=100, field="上游模型名"
        )
    if "base_url" in data or not partial:
        out["base_url"] = _clean_base_url(data.get("base_url"))
    if "input_price" in data or not partial:
        out["input_price"] = _clean_price(data.get("input_price"), "输入单价")
    if "output_price" in data or not partial:
        out["output_price"] = _clean_price(data.get("output_price"), "输出单价")
    if "modality" in data or not partial:
        modality = str(data.get("modality") or "text").strip()
        if modality not in ("text", "image"):
            raise AppException(code=400, message="模型模态只能是 text 或 image")
        out["modality"] = modality
    if "image_price" in data or not partial:
        out["image_price"] = _clean_price(data.get("image_price"), "图片单价")
    if "sort_order" in data or not partial:
        raw = data.get("sort_order", 0)
        try:
            sort_order = int(raw if raw is not None else 0)
        except (TypeError, ValueError):
            raise AppException(code=400, message="排序必须是整数")
        if sort_order < 0 or sort_order > _MAX_SORT:
            raise AppException(code=400, message=f"排序必须在 0 到 {_MAX_SORT} 之间")
        out["sort_order"] = sort_order
    if "remark" in data or not partial:
        out["remark"] = _clean_str(data.get("remark"), max_length=_MAX_REMARK, field="备注") or None
    if "is_enabled" in data or not partial:
        out["is_enabled"] = _clean_bool(data.get("is_enabled", True), "启用状态")
    if "is_default" in data:
        out["is_default"] = _clean_bool(data.get("is_default"), "默认状态")
    return out


async def _generate_code(db: AsyncSession, provider: str, model_name: str) -> str:
    raw = (model_name or provider).lower()
    base = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._") or provider
    base = base[:56]
    candidate = base
    suffix = 2
    while (
        await db.execute(select(AiModel.id).where(AiModel.code == candidate))
    ).scalar_one_or_none() is not None:
        candidate = f"{base}-{suffix}"
        suffix += 1
        if suffix > 99:
            raise AppException(code=409, message="自动生成模型标识失败，请手动填写")
    return candidate


async def _ensure_default(
    db: AsyncSession, preferred_id: int | None = None, modality: str = "text"
) -> None:
    """保证「每个模态有启用模型时恰好一个默认」；禁用/删除默认后自动接替

    `FOR UPDATE` 串行化并发写：两个管理员同时保存时，各自「读全表 → 改旗标」
    会各自写出一条默认记录（表上没有部分唯一索引）。模型数量很少，锁整表成本可忽略。
    模态过滤是必须的：文本与图像各有一个默认，否则新增图像模型会抢走文本默认。
    """
    rows = (
        await db.execute(
            select(AiModel)
            .where(AiModel.modality == modality)
            .with_for_update()
        )
    ).scalars().all()
    enabled = [row for row in rows if row.is_enabled]
    choice = None
    if enabled:
        if preferred_id is not None:
            choice = next((row for row in enabled if row.id == preferred_id), None)
        if choice is None:
            choice = next((row for row in enabled if row.is_default), None)
        if choice is None:
            choice = enabled[0]
    for row in rows:
        desired = bool(choice is not None and row.id == choice.id)
        if bool(row.is_default) != desired:
            row.is_default = desired
    await db.flush()


async def _invalidate_runtime_caches() -> None:
    # 延迟 import：本模块被 ai_config_service 导入，顶层互相引用会成环
    from app.services.ai_config_service import invalidate_ai_config_cache
    from app.services.ai_usage_service import invalidate_quota_config_cache

    await invalidate_ai_config_cache()
    await invalidate_quota_config_cache()


async def _tool_reference_counts(db: AsyncSession) -> dict[str, int]:
    """按模型 code 统计被多少工具引用；查询失败只降级为空（列表不因此打不开）"""
    rows = []
    with degrade_guard(
        "ai_model.tool_ref_counts",
        detail="统计模型被工具引用次数失败，列表将不显示引用数",
    ):
        expr = func.json_unquote(func.json_extract(Tool.ai_config, "$.model"))
        rows = (
            await db.execute(
                select(expr.label("model_code"), func.count())
                .where(Tool.ai_config.is_not(None))
                .group_by(expr)
            )
        ).all()
    return {
        str(code): int(count or 0)
        for code, count in rows
        if code
    }


async def _tool_reference_count(db: AsyncSession, code: str) -> int:
    expr = func.json_unquote(func.json_extract(Tool.ai_config, "$.model"))
    return int(
        (
            await db.execute(
                select(func.count()).select_from(Tool).where(expr == code)
            )
        ).scalar()
        or 0
    )


async def _writing_prompt_reference_count(db: AsyncSession, code: str) -> int:
    """按模型 code 统计被多少 AI 写作提示词绑定。

    用「选主键再数行」而不是 COUNT：测试用的 MemorySession 不支持无实体的聚合
    查询，而这类引用计数正是最需要离线门禁覆盖的地方。提示词最多十几条，
    全量取主键的成本可以忽略。
    """
    rows = (
        await db.execute(
            select(AiWritingPrompt.id).where(AiWritingPrompt.model_code == code)
        )
    ).scalars().all()
    return len(rows)


def _serialize(row: AiModel, *, tool_refs: dict[str, int] | None = None) -> dict:
    env_key, env_base, env_model = env_values(row.provider)
    if row.api_key_enc:
        api_key_source = "database"
    elif env_key:
        api_key_source = "env"
    else:
        api_key_source = "none"
    return {
        "id": row.id,
        "code": row.code,
        "display_name": row.display_name,
        "provider": row.provider,
        "provider_label": provider_display_name(row.provider),
        "model_name": row.model_name or "",
        "effective_model_name": row.model_name or env_model,
        "base_url": row.base_url or "",
        "effective_base_url": row.base_url or env_base,
        "api_key_source": api_key_source,
        "api_key_masked": mask_secret(row.api_key_enc) if row.api_key_enc else "",
        "modality": row.modality,
        "image_price": float(row.image_price or 0),
        "input_price": float(row.input_price or 0),
        "output_price": float(row.output_price or 0),
        "is_enabled": bool(row.is_enabled),
        "is_default": bool(row.is_default),
        "sort_order": int(row.sort_order or 0),
        "remark": row.remark or "",
        "tool_refs": int((tool_refs or {}).get(row.code, 0)),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def list_models(db: AsyncSession) -> dict:
    rows = (
        await db.execute(
            select(AiModel).order_by(
                AiModel.is_default.desc(),
                AiModel.sort_order,
                AiModel.id,
            )
        )
    ).scalars().all()
    refs = await _tool_reference_counts(db)
    return {
        "items": [_serialize(row, tool_refs=refs) for row in rows],
        "providers": providers_payload(),
        "total": len(rows),
    }


async def list_model_options(
    db: AsyncSession, modality: str = "text"
) -> dict:
    """工具编辑下拉：只返回同模态模型（含禁用），由前端把禁用项标灰/禁选

    默认只给文本模型：工具调用的是聊天接口，下拉里出现图像模型只会让运营
    选中一个调用必炸的模型。
    """
    rows = (
        await db.execute(
            select(AiModel)
            .where(AiModel.modality == modality)
            .order_by(
                AiModel.is_default.desc(),
                AiModel.is_enabled.desc(),
                AiModel.sort_order,
                AiModel.id,
            )
        )
    ).scalars().all()
    return {
        "items": [
            {
                "code": row.code,
                "display_name": row.display_name,
                "provider": row.provider,
                "provider_label": provider_display_name(row.provider),
                "model_name": row.model_name or env_values(row.provider)[2],
                "modality": row.modality,
                "image_price": float(row.image_price or 0),
                "is_enabled": bool(row.is_enabled),
                "is_default": bool(row.is_default),
            }
            for row in rows
        ]
    }


async def get_default_image_model(db: AsyncSession) -> dict | None:
    """当前生效的图像模型（默认优先，其次排序）；没有则 None"""
    row = (await db.execute(
        select(AiModel).where(AiModel.modality == "image", AiModel.is_enabled.is_(True))
        .order_by(AiModel.is_default.desc(), AiModel.sort_order, AiModel.id).limit(1)
    )).scalars().first()
    if row is None:
        return None
    return {"code": row.code, "display_name": row.display_name, "image_price": float(row.image_price or 0)}


async def has_enabled_image_price(session: AsyncSession | None = None) -> bool:
    """是否存在「启用中的图像模型 + image_price > 0」——图片费用是否可计价的唯一判据。

    看板用它区分两种 0：真的免费（配了 0 单价之外的另一档价格）与「根本没配单价」。
    后者必须显示 null（未配置单价），不能显示 0 元，否则运营会以为插图不花钱。
    查询失败按「未配置」拒绝：宁可少显示金额，也不能把未知成本展示成 0。
    """
    stmt = (
        select(AiModel.id)
        .where(
            AiModel.modality == "image",
            AiModel.is_enabled.is_(True),
            AiModel.image_price > 0,
        )
        .limit(1)
    )
    found = None
    with degrade_guard(
        "ai_model.image_price_configured",
        detail="读取图像模型单价失败，按未配置单价处理",
    ):
        if session is not None:
            found = (await session.execute(stmt)).scalars().first()
        else:
            async with async_session() as own_session:
                found = (await own_session.execute(stmt)).scalars().first()
    return found is not None


async def create_model(db: AsyncSession, data: dict, admin: User) -> dict:
    payload = _normalize_payload(data, partial=False)
    provider = payload["provider"]
    _assert_provider_modality(provider, payload["modality"])
    _require_custom_endpoint(provider, payload["base_url"], payload["model_name"])
    code = _clean_code(data.get("code")) or await _generate_code(
        db, provider, payload.get("model_name") or ""
    )
    api_key = (data.get("api_key") or "").strip() if isinstance(data.get("api_key"), str) else ""
    api_key_enc = "" if looks_like_mask(api_key) else encrypt_secret(api_key)

    if payload.get("is_default") and not payload["is_enabled"]:
        raise AppException(code=400, message="默认模型必须处于启用状态")

    row = AiModel(
        code=code,
        display_name=payload["display_name"],
        provider=provider,
        modality=payload["modality"],
        image_price=payload["image_price"],
        model_name=payload["model_name"],
        base_url=payload["base_url"],
        api_key_enc=api_key_enc,
        input_price=payload["input_price"],
        output_price=payload["output_price"],
        is_enabled=payload["is_enabled"],
        is_default=False,
        sort_order=payload["sort_order"],
        remark=payload["remark"],
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise AppException(code=409, message=f"模型标识「{code}」已存在")

    # 首条启用模型自动成为该模态的默认；显式指定默认则优先它
    await _ensure_default(
        db, preferred_id=row.id if payload.get("is_default") else None,
        modality=row.modality,
    )

    record_admin_action(
        db,
        admin,
        "ai_model_create",
        target_type="ai_model",
        target_id=row.id,
        summary=f"新建 AI 模型「{row.display_name}」（{row.code}）",
        detail={
            "provider": row.provider,
            "model_name": row.model_name,
            "base_url": row.base_url,
            "has_api_key": bool(row.api_key_enc),
            "prices": [float(row.input_price or 0), float(row.output_price or 0)],
        },
    )
    await db.commit()
    await _invalidate_runtime_caches()
    return {"id": row.id, "code": row.code}


async def update_model(
    db: AsyncSession, model_id: int, data: dict, admin: User
) -> None:
    row = (
        await db.execute(select(AiModel).where(AiModel.id == model_id))
    ).scalar_one_or_none()
    if row is None:
        raise AppException(code=404, message="AI 模型不存在")

    if data.get("code") is not None:
        new_code = _clean_code(data.get("code"))
        if new_code and new_code != row.code:
            raise AppException(code=400, message="模型标识创建后不可修改（工具仍在引用它）")

    payload = _normalize_payload(data, partial=True)
    if "modality" in payload and payload["modality"] != row.modality:
        # 改模态会让历史默认与前端下拉错位（文本与图像各有默认，切换后旧默认失效）
        raise AppException(code=400, message="模型模态创建后不可修改")
    changed_fields = sorted(payload.keys())

    if payload.get("is_default") is True and not payload.get("is_enabled", row.is_enabled):
        raise AppException(code=400, message="默认模型必须处于启用状态")

    for field, value in payload.items():
        setattr(row, field, value)

    # 按**合并后**的结果校验：只清空 base_url 也要拦（否则调用期才炸）
    _require_custom_endpoint(row.provider, row.base_url or "", row.model_name or "")
    _assert_provider_modality(row.provider, row.modality)

    if "api_key" in data:
        raw_key = data.get("api_key")
        if raw_key is not None and isinstance(raw_key, str) and not looks_like_mask(raw_key):
            raw_key = raw_key.strip()
            row.api_key_enc = encrypt_secret(raw_key) if raw_key else ""
            changed_fields.append("api_key")

    if not row.is_enabled and row.is_default:
        # 禁用默认模型：先落禁用，再让 _ensure_default 接替（不在这里直接报错，
        # 否则运营必须先手动设默认再禁用，多一步且容易忘）
        row.is_default = False

    preferred_id = row.id if data.get("is_default") is True and row.is_enabled else None
    await db.flush()
    await _ensure_default(db, preferred_id=preferred_id, modality=row.modality)

    remove_key = "api_key" in changed_fields
    record_admin_action(
        db,
        admin,
        "ai_model_update",
        target_type="ai_model",
        target_id=row.id,
        summary=f"编辑 AI 模型「{row.display_name}」（{row.code}）",
        detail={
            "fields": changed_fields,
            "provider": row.provider,
            "has_api_key": bool(row.api_key_enc) or remove_key,
        },
    )
    await db.commit()
    await _invalidate_runtime_caches()


async def delete_model(db: AsyncSession, model_id: int, admin: User) -> None:
    row = (
        await db.execute(select(AiModel).where(AiModel.id == model_id))
    ).scalar_one_or_none()
    if row is None:
        raise AppException(code=404, message="AI 模型不存在")

    refs = await _tool_reference_count(db, row.code)
    if refs > 0:
        raise AppException(
            code=409,
            message=f"该模型仍被 {refs} 个在线工具引用，请先到「在线工具」改配置，或改为禁用",
        )

    if row.modality == "text":
        prompt_refs = await _writing_prompt_reference_count(db, row.code)
        if prompt_refs > 0:
            raise AppException(
                code=409,
                message=(
                    f"该模型仍被 {prompt_refs} 个 AI 写作操作绑定，请先到「AI 写作」"
                    "修改提示词配置，或改为禁用"
                ),
            )

    code, display_name = row.code, row.display_name
    modality = row.modality
    await db.delete(row)
    await db.flush()
    await _ensure_default(db, modality=modality)
    record_admin_action(
        db,
        admin,
        "ai_model_delete",
        target_type="ai_model",
        target_id=model_id,
        summary=f"删除 AI 模型「{display_name}」（{code}）",
    )
    await db.commit()
    await _invalidate_runtime_caches()
