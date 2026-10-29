"""AI 模型工厂：把 tools.ai_config.model 里的 Provider 名（deepseek/openai/qwen）路由到具体实现。
注册表见 app.services.ai.registry（唯一事实来源）；本模块只负责按名取实例、配置变更时重建与连接池生命周期。"""

import asyncio

from loguru import logger

from app.config import settings
from app.core.exceptions import AppException
from app.services.ai.base import AIProvider
from app.services.ai.base_image import ImageProvider
from app.services.ai.registry import (
    ensure_providers_loaded,
    provider_factory,
    registered_provider_names,  # noqa: F401  re-export：调用方（ai_usage_service/测试）沿用此入口
)
from app.services.ai_config_service import ProviderConfig, get_provider_config, get_runtime_config

_providers: dict[str, AIProvider | ImageProvider] = {}
# 重建竞态锁：「比对指纹 → close 旧实例 → 建新 → 写回」中间有 await，配置轮换时两个并发请求
# 会各自 close + 各自新建，被覆盖实例的 httpx.AsyncClient 再没人 aclose（连接泄漏随轮换累积）
_providers_lock = asyncio.Lock()


async def get_provider(model: str | None = None) -> AIProvider:
    """获取 AI 文本模型 Provider（单例，**配置变更时自动重建**）"""
    return await _get_provider(model, modality="text")


async def get_image_provider(model: str | None = None) -> ImageProvider:
    """获取图像模型 Provider（单例，配置变更时自动重建）"""
    return await _get_provider(model, modality="image")


async def _get_provider(
    model: str | None, *, modality: str
) -> AIProvider | ImageProvider:
    """按模态解析并缓存 Provider 实例

    未知 Provider 名在文本模态回退到 `DEFAULT_AI_MODEL`（并打 warning）；
    图像模态不回退 —— 拿错模型出的是错图，必须让管理员先补配置。
    ⚠️ 回退后**按回退名缓存**：原实现会把实例缓存到「请求的那个未知名字」下，
    于是每个拼错的名字都会新建一个 httpx 客户端（连接池泄漏），
    且下次同名请求仍会重新走一遍回退分支。

    ⚠️ 为什么要比对配置指纹：后台现在可以改 API Key（见 ai_config_service），
    而实例持有的是**创建时**的 Key。指纹一致就复用（保住 httpx 连接池），
    不一致才重建 —— 否则会出现「后台改了 Key、也提示保存成功，但仍在用旧 Key」。
    """
    ensure_providers_loaded()
    config = await get_runtime_config(model, modality=modality)
    provider_name = config.provider or model
    if modality == "text":
        provider_name = provider_name or settings.DEFAULT_AI_MODEL
    else:
        provider_name = provider_name or "dashscope_image"
    cache_key = config.code or provider_name

    if provider_factory(provider_name, modality=modality) is None:
        if modality == "image":
            raise AppException(
                code=503, message="图像模型未配置，请到「模型管理」添加图像模型"
            )
        # 用户面路径（工具配置里的模型名）刻意保留回退 + 明确告警：
        # 拼错的名字不该让工具整体不可用。但「回退目标」本身也必须是
        # 已注册的文本 Provider，否则给出明确错误而不是继续往下拼错。
        fallback = settings.DEFAULT_AI_MODEL
        if provider_factory(fallback, modality="text") is None:
            fallback = "deepseek"
        if provider_factory(fallback, modality="text") is None:
            raise ValueError(
                f"未知的 AI 模型 {provider_name!r}，且默认模型 {settings.DEFAULT_AI_MODEL!r} "
                f"也未注册（已注册: {registered_provider_names('text')}）"
            )
        logger.warning(f"未知的 AI 模型: {provider_name}, 回退到 {fallback}")
        legacy = await get_provider_config(fallback)
        config = ProviderConfig(
            api_key=legacy.api_key,
            base_url=legacy.base_url,
            default_model=legacy.default_model,
            provider=fallback,
            code=fallback,
            display_name=fallback,
            modality="text",
        )
        provider_name = fallback
        cache_key = fallback

    instance = _providers.get(cache_key)
    if instance is not None and instance.config_fingerprint == config.fingerprint:
        return instance

    async with _providers_lock:
        # 锁内复检：排队等锁期间可能已有并发请求按同一指纹重建完成，直接复用；
        # 少了这一步，锁只是把泄漏改成「串行地多建几个 client」
        instance = _providers.get(cache_key)
        if instance is not None and instance.config_fingerprint == config.fingerprint:
            return instance
        rebuilt = instance is not None
        if rebuilt:
            await instance.close()
        instance = provider_factory(provider_name, modality=modality)(config)
        instance.config_fingerprint = config.fingerprint
        # 用量维度按「模型 code」记账（旧供应商名即种子模型的 code，历史数据不断档）
        instance.model_key = cache_key
        # 图片按张计费，单价随实例走；文本 Provider 读取该属性时恒为 0
        instance.image_price = config.image_price
        _providers[cache_key] = instance
        logger.info(
            f"AI 模型 {cache_key}（{provider_name}）"
            f"{'配置已变更，实例已重建' if rebuilt else '首次加载'}"
        )
        return instance


async def close_all_providers():
    """关闭所有 Provider 连接"""
    async with _providers_lock:
        for provider in _providers.values():
            await provider.close()
        _providers.clear()