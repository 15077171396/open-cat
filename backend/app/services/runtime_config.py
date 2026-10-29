"""运行期可改配置：后台「系统设置」优先，环境变量兜底。

## 为什么是「改写 settings 对象」而不是让调用点 await 取值

全项目有 100+ 处直接读 `settings.X`，其中大量位于**同步**函数里（sitemap 生成、
审核词库构建、图片代理 URL 拼接、邀请海报渲染）。逐个改成 async 会把调用链
一路传染到路由层，收益远小于风险。这里改为把 DB 值在**进程内**覆盖到 `settings`
上：既有调用点零改动即可生效，新增配置项也只需要在 `RUNTIME_KEYS` 登记一行。

## 三条必须守住的约定

1. **env 是默认值，不是备份**：模块导入时快照 env 原值（`_ENV_DEFAULTS`）。
   后台清空某项后，下一次刷新必须把 env 原值**写回去**，否则「恢复默认」在运行期
   不生效（进程重启前一直沿用被覆盖的值）。
2. **失败即保持现状**：DB/Redis 不可用时不覆盖任何键 —— 绝不能用「读不到」推导出
   「配置为空」，那会把可用配置冲掉（例如限流阈值变成 0，全站立刻 429）。
3. **热路径退避**：DB 故障后 60 秒内不再重试。MySQL 不可达时单次建连要等约 10s，
   而本模块的刷新循环在后台跑，没有退避会变成每分钟一次 10s 阻塞。

## 生效时机

- 进程启动（lifespan）先应用一次；
- 后台保存设置后**立即**应用并失效缓存（当前 worker 立刻生效）；
- 后台每 60s 轮询一次（其他 gunicorn worker 最多 60s 内跟上）；
- Redis 缓存 TTL 300s，所以正常情况下每个 worker 每 5 分钟才查一次库。
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select

from app.config import settings
from app.core.crypto import decrypt_secret
from app.database import async_session
from app.models.system_setting import SystemSetting
from app.redis import log_redis_degraded, redis_client
from app.services.content_cache import shape_key

# 形状版本位由 shape_key 统一派生（不要手写 `:v1`：两处 idiom 会漂移，
# tests/test_content_cache.py 有守卫）
_CACHE_KEY = shape_key("runtime_cfg")

_CACHE_TTL = 300
# 后台改完到其他 worker 生效的最大延迟（也是刷新循环的间隔）
SYNC_INTERVAL_SECONDS = 60
_DB_RETRY_INTERVAL = 60.0

_MB = 1024 * 1024


@dataclass(frozen=True)
class RuntimeKey:
    """一条「后台可改」配置的定义

    :param setting_key: `system_settings` 表里的键
    :param attr: 覆盖到 `settings` 上的属性名
    :param kind: int / float / bool / str
    :param scale: 单位换算系数（小时→秒、MB→字节）
    :param min_value: 下限，**按表单单位**（换算前）判断 —— 运营填「小时 / MB」，不是字节
    :param max_value: 上限，同上
    :param secret: 凭据类。库里存的是密文，**解密只发生在应用时**，缓存里仍是密文
        （与 ai_config_service 同一取舍：Redis 开了 AOF，明文密钥会随备份落到磁盘）
    """

    setting_key: str
    attr: str
    kind: str = "str"
    scale: float = 1.0
    min_value: float | None = None
    max_value: float | None = None
    secret: bool = False


# ⚠️ 只登记「运营真的会改、且改错不会让服务起不来」的项。
# 刻意排除：SECRET_KEY / JWT_* / DB_* / REDIS_* / CORS_ORIGINS / TRUSTED_PROXIES
# （引导期与安全边界，运行期改等于把自己锁在门外）、SENTRY_*（SDK 初始化一次，
# 运行期改无效）、SEARCH_NGRAM_TOKEN_SIZE（必须与 MySQL 服务端一致，需 DBA）、
# AI_TIMEOUT_SECONDS（Provider 实例按 fingerprint 缓存，改了要重建实例才生效）。
RUNTIME_KEYS: tuple[RuntimeKey, ...] = (
    # ---- 业务规则 ----
    RuntimeKey("free_time_hours", "FREE_TIME_LIMIT", "int", scale=3600, min_value=0, max_value=720),
    RuntimeKey("ai_daily_limit_per_user", "AI_DAILY_LIMIT_PER_USER", "int", min_value=0, max_value=100000),
    RuntimeKey("ai_retry_count", "AI_RETRY_COUNT", "int", min_value=0, max_value=5),
    RuntimeKey("ai_total_budget_seconds", "AI_TOTAL_BUDGET_SECONDS", "int", min_value=5, max_value=300),
    # ---- 内容审核 ----
    RuntimeKey("moderation_enabled", "MODERATION_ENABLED", "bool"),
    RuntimeKey("moderation_action", "MODERATION_ACTION", "str"),
    RuntimeKey("moderation_extra_words", "MODERATION_EXTRA_WORDS", "str"),
    # ---- 上传限制 ----
    RuntimeKey("upload_max_file_mb", "COS_MAX_FILE_SIZE", "int", scale=_MB, min_value=1, max_value=200),
    RuntimeKey("upload_max_image_mb", "COS_IMAGE_MAX_SIZE", "int", scale=_MB, min_value=1, max_value=50),
    RuntimeKey("avatar_max_size_mb", "AVATAR_MAX_SIZE", "int", scale=_MB, min_value=1, max_value=20),
    RuntimeKey("avatar_allowed_types", "AVATAR_ALLOWED_TYPES", "str"),
    RuntimeKey("image_webp_threshold_kb", "COS_IMAGE_WEBP_THRESHOLD", "int", scale=1024, min_value=0, max_value=10240),
    RuntimeKey("image_webp_quality", "COS_IMAGE_WEBP_QUALITY", "int", min_value=1, max_value=100),
    # ---- 访问限流 ----
    RuntimeKey("rate_limit_guest", "RATE_LIMIT_GUEST", "int", min_value=10, max_value=100000),
    RuntimeKey("rate_limit_user", "RATE_LIMIT_USER", "int", min_value=10, max_value=100000),
    RuntimeKey("rate_limit_window", "RATE_LIMIT_WINDOW", "int", min_value=10, max_value=3600),
    # ---- 站内搜索 ----
    RuntimeKey("search_fulltext_enabled", "SEARCH_FULLTEXT_ENABLED", "bool"),
    RuntimeKey("search_max_keyword_len", "SEARCH_MAX_KEYWORD_LEN", "int", min_value=8, max_value=256),
    # ---- 站点与 SEO ----
    RuntimeKey("site_base_url", "SITE_BASE_URL", "str"),
    RuntimeKey("baidu_push_token", "BAIDU_PUSH_TOKEN", "str", secret=True),
    RuntimeKey("indexnow_key", "INDEXNOW_KEY", "str", secret=True),
    # ---- 第三方导入 ----
    RuntimeKey("github_token", "GITHUB_TOKEN", "str", secret=True),
    RuntimeKey("github_proxy", "GITHUB_PROXY", "str"),
    RuntimeKey("github_mirror_prefix", "GITHUB_MIRROR_PREFIX", "str"),
    RuntimeKey("hf_token", "HF_TOKEN", "str", secret=True),
    RuntimeKey("article_proxy", "ARTICLE_PROXY", "str"),
    # ---- 对象存储 ----
    RuntimeKey("cos_secret_id", "COS_SECRET_ID", "str", secret=True),
    RuntimeKey("cos_secret_key", "COS_SECRET_KEY", "str", secret=True),
    RuntimeKey("cos_bucket", "COS_BUCKET", "str"),
    RuntimeKey("cos_region", "COS_REGION", "str"),
    RuntimeKey("cos_cdn_domain", "COS_CDN_DOMAIN", "str"),
    # ---- 小程序 ----
    RuntimeKey("wechat_mini_appid", "WECHAT_MINI_APPID", "str"),
    RuntimeKey("wechat_mini_secret", "WECHAT_MINI_SECRET", "str", secret=True),
    # ---- 邮件通知 ----
    RuntimeKey("smtp_host", "SMTP_HOST", "str"),
    RuntimeKey("smtp_port", "SMTP_PORT", "int", min_value=1, max_value=65535),
    RuntimeKey("smtp_username", "SMTP_USERNAME", "str"),
    RuntimeKey("smtp_password", "SMTP_PASSWORD", "str", secret=True),
    RuntimeKey("smtp_use_tls", "SMTP_USE_TLS", "bool"),
    RuntimeKey("smtp_from_email", "SMTP_FROM_EMAIL", "str"),
    RuntimeKey("smtp_sender_name", "SMTP_SENDER_NAME", "str"),
)

# 这些设置项的默认值在后台表单里要按「同一单位」展示（小时 / MB），故单独给默认值字符串
BY_SETTING_KEY: dict[str, RuntimeKey] = {k.setting_key: k for k in RUNTIME_KEYS}

# env 原值快照：后台清空某项时必须能写回来
_ENV_DEFAULTS: dict[str, object] = {
    k.setting_key: getattr(settings, k.attr) for k in RUNTIME_KEYS
}

_db_retry_after = 0.0
_db_warned = False
# 后台保存后由管理端路由置位：下一次刷新即使缓存未过期也要回源
_force_reload = False

_TRUE_WORDS = {"1", "true", "on", "yes", "y", "t"}
_FALSE_WORDS = {"0", "false", "off", "no", "n", "f"}


def parse_bool(raw: str | None) -> bool | None:
    """把后台/表单里的布尔字面量转 bool；无法识别返回 None（调用方回退默认）"""
    text = (raw or "").strip().lower()
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    return None


def _parse(raw: str, spec: RuntimeKey):
    """把字符串解析成 settings 上应有的值；不合法返回 None（调用方回退 env）"""
    text = (raw or "").strip()
    if not text:
        return None
    if spec.kind == "bool":
        return parse_bool(text)
    if spec.kind in ("int", "float"):
        try:
            number = float(text)
        except ValueError:
            return None
        if not math.isfinite(number):
            return None
        # 上下限按**表单单位**判断（小时 / MB / 次），再换算成 settings 用的单位 ——
        # 若先换算后判断，`free_time_hours` 的「最多 720 小时」会被当成 720 秒而永远拒绝
        if spec.min_value is not None and number < spec.min_value:
            return None
        if spec.max_value is not None and number > spec.max_value:
            return None
        value = number * spec.scale
        return int(round(value)) if spec.kind == "int" else value
    return text


async def _cache_get() -> dict[str, str] | None:
    try:
        raw = await redis_client.get(_CACHE_KEY)
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


async def _cache_set(stored: dict[str, str]) -> None:
    try:
        await redis_client.set(_CACHE_KEY, json.dumps(stored, ensure_ascii=False), ex=_CACHE_TTL)
    except Exception as exc:  # noqa: BLE001 缓存失效不影响功能
        log_redis_degraded("runtime_config._cache_set", exc)


async def load_overrides(force: bool = False) -> dict[str, str] | None:
    """读后台显式配置（只查登记过的键）。

    :return: 覆盖字典（可能是空 dict，表示「一项都没配」）；``None`` 表示读不到
        （DB 故障 / 退避期），调用方**不得**据此清空任何配置。
    """
    global _db_retry_after, _db_warned, _force_reload

    if not force and not _force_reload:
        cached = await _cache_get()
        if cached is not None:
            return cached
    if time.monotonic() < _db_retry_after:
        return None
    try:
        async with async_session() as session:
            rows = (
                await session.execute(
                    select(SystemSetting).where(
                        SystemSetting.setting_key.in_(BY_SETTING_KEY.keys())
                    )
                )
            ).scalars().all()
    except Exception:
        _db_retry_after = time.monotonic() + _DB_RETRY_INTERVAL
        if not _db_warned:
            _db_warned = True
            logger.warning("读取运行期配置失败，继续使用当前生效值（本进程不再重复告警）")
        return None

    _db_retry_after = 0.0
    _force_reload = False
    stored = {row.setting_key: row.value for row in rows if row.value}
    await _cache_set(stored)
    return stored


def _apply(overrides: dict[str, str]) -> list[str]:
    """把覆盖值写到 settings 上；未配置的键恢复 env 原值。返回发生变化的设置键"""
    changed: list[str] = []
    for spec in RUNTIME_KEYS:
        raw = overrides.get(spec.setting_key)
        if raw and spec.secret:
            # 库里存的是密文：解密失败（SECRET_KEY 轮换 / 数据损坏）→ 当作未配置，回退 env
            raw = decrypt_secret(raw) or ""
        value = _parse(raw, spec) if raw else None
        if value is None:
            # 没配 / 配了但解析不出合法值 → 回到 env 原值（不是「清空」）
            value = _ENV_DEFAULTS[spec.setting_key]
        if getattr(settings, spec.attr) == value:
            continue
        setattr(settings, spec.attr, value)
        changed.append(spec.setting_key)
    return changed


def _after_apply(changed: list[str]) -> None:
    """配置变了之后的副作用（目前只有敏感词库需要重建正则）"""
    if not changed:
        return
    if "moderation_extra_words" in changed:
        try:
            from app.services.moderation_service import reload_words

            reload_words()
        except Exception:  # noqa: BLE001 词库重建失败不该影响其它配置生效
            logger.exception("敏感词库重建失败，沿用旧词库")


async def apply_runtime_settings(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """应用后台配置到 settings（缺省参数时自行读取）。返回实际生效的覆盖字典。

    ⚠️ 读不到配置（``None``）时**什么都不做** —— 见模块文档第 2 条约定。
    """
    if overrides is None:
        loaded = await load_overrides()
        if loaded is None:
            return {}
        overrides = loaded
    changed = _apply(overrides)
    _after_apply(changed)
    if changed:
        logger.info(f"运行期配置已更新：{', '.join(sorted(changed))}")
    return overrides


async def invalidate_runtime_settings_cache() -> None:
    """后台保存设置后调用：失效缓存并让下一次刷新强制回源"""
    global _force_reload
    _force_reload = True
    try:
        await redis_client.delete(_CACHE_KEY)
    except Exception:
        log_redis_degraded(
            "runtime_config.invalidate",
            "Redis 不可用，运行期配置缓存未主动失效（其他 worker 最多 60s 后跟上）",
        )


async def runtime_settings_sync_loop() -> None:
    """后台轮询：让「别的 worker 保存的设置」在本进程内也能生效"""
    while True:
        try:
            await apply_runtime_settings()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 单次刷新失败不该杀死循环
            logger.exception("运行期配置刷新失败，60s 后重试")
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
