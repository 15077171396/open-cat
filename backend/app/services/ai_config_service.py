"""AI 模型运行时配置解析：模型管理（ai_models）优先，旧设置 / 环境变量兜底。

「取配置」与「建实例」分开：Provider 是按名缓存的单例（复用 httpx 连接池），
API Key 须不重启生效——配置变了按 fingerprint 重建实例。跨 worker 失效靠 Redis
（60s TTL 兜底）；后台未配置或解密失败均逐项回退环境变量，零行为变更。
"""

import json
import time
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select

from app.config import settings
from app.core.crypto import decrypt_secret
from app.database import async_session
from app.models.system_setting import SystemSetting
from app.redis import log_redis_degraded, redis_client
from app.services import ai_model_service
from app.services.ai.registry import provider_meta

_CACHE_PREFIX = "ai_cfg:"
_CACHE_TTL = 60
_DEFAULT_CACHE_KEY = "__default__"

# Provider 名 → (设置键前缀, 环境变量前缀) 由注册表唯一提供，避免手写映射与 factory 清单漂移


@dataclass(frozen=True)
class ProviderConfig:
    """单个模型 / Provider 的运行时配置

    `provider` / `code` / `display_name` 在「模型管理」路径下非空；
    旧设置 / 环境变量兜底路径只填 provider=code=供应商名。
    """

    api_key: str
    base_url: str
    default_model: str
    provider: str = ""
    code: str = ""
    display_name: str = ""
    modality: str = "text"
    image_price: float = 0.0

    @property
    def fingerprint(self) -> tuple:
        """用于判断实例是否需要重建（配置或协议实现变了才重建）

        `image_price` 也参与指纹：它随实例走（按张计费），只改单价时
        旧实例会一直沿用旧价，账单随之静默失真。
        """
        return (
            self.provider,
            self.api_key,
            self.base_url,
            self.default_model,
            self.image_price,
        )


# 进程内短 TTL 缓存。作用**不是加速**（正常情况下 Redis 已足够快），而是
# 「Redis 与 DB 同时不可用时，不要每次 AI 调用都去探测」——探测本身要等超时。
# 跨 worker 的失效仍由 Redis 负责，这里只是兜底，故 TTL 刻意短（30s）：
# 最坏情况是「后台改了 Key，某 worker 30 秒后才生效」。
_LOCAL_CACHE: dict[str, tuple[float, ProviderConfig]] = {}
_LOCAL_TTL = 30


def _env_config(provider: str) -> ProviderConfig:
    """环境变量兜底配置（前缀来自注册表；未注册名会先被 get_provider_config 拦下）"""
    api_key, base_url, model = ai_model_service.env_values(provider)
    return ProviderConfig(api_key=api_key, base_url=base_url, default_model=model)


async def _cache_get_key(cache_key: str) -> dict | None:
    """读取缓存的**原始配置材料**（API Key 保持密文原样）。

    Redis 是共享存储，且生产 compose 里开了 `--appendonly yes` —— 写进去的东西会随
    AOF 落到宿主卷与备份。因此这里刻意只存密文，解密只发生在 `_materialize`
    （第四轮全量审查 P1-51：原先缓存的是解密后的明文密钥，等于绕过了
    `core/crypto.py` 整层「密钥不落明文存储」的设计）。
    """
    try:
        raw = await redis_client.get(f"{_CACHE_PREFIX}{cache_key}")
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        if "api_key_enc" not in data:
            # 旧格式（缓存过明文密钥）一律视为失效 → 回源重建，顺带把明文条目冲掉
            return None
        return data
    except Exception:
        return None


async def _cache_get(provider: str) -> dict | None:
    return await _cache_get_key(provider)


async def _cache_set_key(cache_key: str, stored: dict) -> None:
    try:
        await redis_client.set(
            f"{_CACHE_PREFIX}{cache_key}", json.dumps(stored), ex=_CACHE_TTL
        )
    except Exception as exc:  # noqa: BLE001 降级不阻断主流程
        # Redis 故障不影响功能，只是退化为每次查库
        log_redis_degraded("ai_config_service._cache_set", exc)


async def _cache_set(provider: str, stored: dict) -> None:
    await _cache_set_key(provider, stored)


def _materialize(stored: dict, env_cfg: ProviderConfig) -> ProviderConfig:
    """原始材料 → 可用配置。API Key 在**这里**解密，因此明文只存在于进程内。"""
    return ProviderConfig(
        # decrypt_secret 解密失败返回 None（SECRET_KEY 轮换）→ 回退环境变量
        api_key=decrypt_secret(stored.get("api_key_enc")) or env_cfg.api_key,
        base_url=stored.get("base_url") or env_cfg.base_url,
        default_model=stored.get("default_model") or env_cfg.default_model,
    )


async def invalidate_ai_config_cache() -> None:
    """后台保存设置后调用：清掉所有 Provider 的配置缓存

    清空整个前缀而不是逐个删：调用方不必知道改的是哪个 Provider
    （保存的是整个表单），逐个删反而容易漏。
    """
    _LOCAL_CACHE.clear()
    try:
        # ⚠️ 必须用 SCAN 分批，**绝不能用 KEYS**（第三轮增量审查 2026-10-11 P1-43③）：
        # KEYS 是 O(N) 且会**阻塞整个 Redis 实例** —— 而本实例同时承载限流计数、
        # 令牌吊销黑名单、各类缓存，一次 KEYS 可能让全站请求一起卡住。
        # 同仓库 content_cache._delete_by_prefix 早已用这个范式，且该模块有注释
        # 明令禁止 KEYS；此处是漏网的一处。
        async for key in redis_client.scan_iter(match=f"{_CACHE_PREFIX}*", count=100):
            await redis_client.delete(key)
    except Exception:
        log_redis_degraded("ai_config.invalidate_cache", "Redis 不可用，AI 配置缓存未主动失效（将随 60s TTL 过期）")


async def get_provider_config(provider: str) -> ProviderConfig:
    """取 Provider 的生效配置：DB（后台配置）优先，逐项回退环境变量

    ⚠️ 未注册的 Provider 名**直接抛错**（第三轮增量审查 P1-40，no silent
    fallback）：原实现在名字缺失时静默回退到 `DEFAULT_AI_MODEL` / `"deepseek"`，
    于是「只登记了一半的新 Provider」会用 DeepSeek 的 key / base_url / model
    构造出真实实例 —— 不报错、不打日志，只在账单与回答质量上体现。
    未知名字属**调用方 bug**，应当立刻可见；用户面的回退由
    ``factory.get_provider`` 在调用本函数**之前**用明确告警完成。
    """
    meta = provider_meta(provider)
    if meta is None:
        raise ValueError(
            f"未知的 AI Provider: {provider!r}（未通过 @register_provider 注册）"
        )

    env_cfg = _env_config(provider)

    local = _LOCAL_CACHE.get(provider)
    if local is not None and (time.monotonic() - local[0]) < _LOCAL_TTL:
        return local[1]

    cached = await _cache_get(provider)
    if cached is not None:
        cfg = _materialize(cached, env_cfg)
        _LOCAL_CACHE[provider] = (time.monotonic(), cfg)
        return cfg

    prefix = meta.settings_prefix
    keys = [f"{prefix}_api_key", f"{prefix}_base_url", f"{prefix}_model"]

    try:
        async with async_session() as session:
            rows = {
                row.setting_key: row.value
                for row in (
                    await session.execute(
                        select(SystemSetting).where(SystemSetting.setting_key.in_(keys))
                    )
                ).scalars().all()
                if row.value
            }
    except Exception:
        # 读库失败不能让 AI 功能整体不可用 —— 回退环境变量
        logger.exception("读取 AI 配置失败，回退环境变量")
        _LOCAL_CACHE[provider] = (time.monotonic(), env_cfg)
        return env_cfg

    # 缓存里放**密文原样**（api_key_enc），解密只在 _materialize 里发生 —— 明文密钥
    # 从此不再进入 Redis / AOF / 备份（第四轮审查 P1-51）
    stored = {
        "api_key_enc": rows.get(f"{prefix}_api_key", ""),
        "base_url": rows.get(f"{prefix}_base_url", ""),
        "default_model": rows.get(f"{prefix}_model", ""),
    }
    await _cache_set(provider, stored)
    cfg = _materialize(stored, env_cfg)
    _LOCAL_CACHE[provider] = (time.monotonic(), cfg)
    return cfg


def _materialize_model(
    material: ai_model_service.ModelRuntimeMaterial,
) -> ProviderConfig:
    """模型目录材料 → 可用配置（Key 在这里解密；空字段逐项回退供应商 env）"""
    env_cfg = _env_config(material.provider)
    return ProviderConfig(
        api_key=decrypt_secret(material.api_key_enc) or env_cfg.api_key,
        base_url=material.base_url or env_cfg.base_url,
        default_model=material.model_name or env_cfg.default_model,
        provider=material.provider,
        code=material.code,
        display_name=material.display_name,
        modality=material.modality,
        image_price=material.image_price,
    )


async def get_runtime_config(
    ref: str | None = None, modality: str = "text"
) -> ProviderConfig:
    """按模型 code / 供应商名 / 默认模型解析运行时配置。

    优先级：
    1. `ai_models` 中 code 精确命中的启用模型（同模态）；
    2. 供应商名 → 该供应商的默认/首条启用模型（同模态）；
    3. 空引用 → 该模态的全局默认模型；
    4. 目录为空 / DB 不可用 / 模型被禁用 → 回退旧 system_settings / 环境变量。

    缓存只存**密文材料**（同旧路径），解密在进程内完成；模型增删改后由
    `invalidate_ai_config_cache()` 统一失效。缓存 key 带模态：同一个 code / 空引用
    在文本与图像下解析出的模型不同，共用 key 会把文本配置喂给图像调用。
    """
    cache_key = f"{modality}:model:{ref or _DEFAULT_CACHE_KEY}"
    local = _LOCAL_CACHE.get(cache_key)
    if local is not None and (time.monotonic() - local[0]) < _LOCAL_TTL:
        return local[1]

    cached = await _cache_get_key(cache_key)
    if cached is not None:
        cfg = _materialize_model(
            ai_model_service.ModelRuntimeMaterial.from_cache_dict(cached)
        )
        _LOCAL_CACHE[cache_key] = (time.monotonic(), cfg)
        return cfg

    material = await ai_model_service.load_model_runtime(ref, modality=modality)
    if material is not None:
        await _cache_set_key(cache_key, material.as_cache_dict())
        cfg = _materialize_model(material)
        _LOCAL_CACHE[cache_key] = (time.monotonic(), cfg)
        return cfg

    # 旧配置兜底：未注册的名字回退同模态默认供应商（与 factory 的历史行为一致）
    from app.services.ai.registry import registered_provider_names

    requested = ref if ref in registered_provider_names(modality) else (
        settings.DEFAULT_AI_MODEL if modality == "text" else "dashscope_image"
    )
    if modality == "text" and requested not in registered_provider_names(modality):
        requested = "deepseek"
    legacy = await get_provider_config(requested)
    cfg = ProviderConfig(
        api_key=legacy.api_key,
        base_url=legacy.base_url,
        default_model=legacy.default_model,
        provider=requested,
        code=requested,
        display_name=ai_model_service.provider_display_name(requested),
        modality=modality,
    )
    _LOCAL_CACHE[cache_key] = (time.monotonic(), cfg)
    return cfg
