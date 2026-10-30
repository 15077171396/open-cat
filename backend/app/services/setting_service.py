"""系统设置服务

设置项以 key-value 形式存储在 system_settings 表：
- 定义集中在 SETTING_DEFINITIONS（键/类型/分组/默认值），新增设置项只需追加定义，无需改表
- 公共接口返回「DB 配置值覆盖默认值」的完整配置，前端无需自行维护默认值表
- Redis 缓存 5 分钟，管理端保存后主动失效
"""

from __future__ import annotations

import ipaddress
import json
import math
from urllib.parse import urlsplit

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret, looks_like_mask, mask_secret
from app.database import async_session
from app.models.system_setting import SystemSetting
from app.models.user import User
from app.redis import log_redis_degraded, redis_client

# 设置项声明拆到 setting_definitions（MNT-08）；这里重新导出，既有 import 路径不变
from app.services.setting_definitions import (  # noqa: E402  （放在 app 导入块之后，保持分组可读）
    SETTING_DEFINITIONS,
    SETTING_GROUPS,
    SURFACE_LEGAL,
    SURFACE_LOGIN,
    SURFACE_MAIL,
    SURFACE_MODEL,
    SURFACE_PAYMENT,
    SURFACE_SETTINGS,
    SettingDefinition,
    SettingGroup,
    definitions_payload,
    groups_payload,
    keys_for_surface,
)

# 这 6 个名字是**重新导出**（不是本模块定义），列进 __all__ 既声明了对外契约，
# 也让 lint 知道它们「被用到了」，不必逐个加 noqa。
__all__ = [
    "SETTING_DEFINITIONS",
    "SETTING_GROUPS",
    "SURFACE_LEGAL",
    "SURFACE_LOGIN",
    "SURFACE_MAIL",
    "SURFACE_MODEL",
    "SURFACE_PAYMENT",
    "SURFACE_SETTINGS",
    "SettingDefinition",
    "SettingGroup",
    "definitions_payload",
    "groups_payload",
    "keys_for_surface",
]

# 值长度上限：与 DB 列（Text）及图片 URL 实际长度需要对齐
_MAX_VALUE_LENGTH = 500
# 富文本类设置（法律文档 HTML）长度上限
_MAX_RICHTEXT_LENGTH = 200_000
# 文本类设置（平台名称）长度上限
_MAX_NAME_LENGTH = 50
# 数值类设置（AI 单价 / 全站上限）长度上限：只用来拦误粘贴的超长串
_MAX_NUMBER_LENGTH = 32
# JSON 类设置（按模型单价表）长度上限
_MAX_JSON_LENGTH = 4000
# 多行文本类设置（敏感词表、代理说明等）长度上限
_MAX_TEXTAREA_LENGTH = 20_000
# 长密钥类设置（PEM 私钥/证书全文）长度上限
_MAX_SECRET_TEXT_LENGTH = 20_000

PUBLIC_CACHE_KEY = "public_settings"
PUBLIC_CACHE_TTL = 300  # 5 分钟：允许短暂陈旧，换取 DB 零压力
LEGAL_CACHE_PREFIX = "legal_doc:"

# 会被当作 httpx base_url、且请求默认头带着 API Key 的配置项，额外要求主机白名单；
# 其余 url 项（社交链接）只由前台渲染成 <a href>，不会被服务端发起请求。
# AI 模型的 BaseURL 已迁到「模型管理」（ai_models 表），这里保留一个哨兵 key，
# 供 `validate_ai_base_url()` 复用同一套 SSRF/白名单校验，避免两套口径漂移。
_AI_MODEL_BASE_URL_KEY = "__ai_model_base_url__"
_KEY_BEARING_SETTING_KEYS = (
    _AI_MODEL_BASE_URL_KEY,
    # 旧设置键已不再出现在系统设置页，但历史库/回滚路径仍可能读到它们；
    # 保留在清单里，确保同一套主机白名单校验不会因迁移而失效。
    "ai_deepseek_base_url",
    "ai_openai_base_url",
    "ai_qwen_base_url",
)


def _allowed_ai_host_suffixes() -> list[str]:
    """解析白名单（AI_BASE_URL_ALLOWED_HOSTS）。留空 = 不限制域名，但内网仍拒绝。"""
    return [
        item.strip().lstrip(".").lower()
        for item in settings.AI_BASE_URL_ALLOWED_HOSTS.split(",")
        if item.strip()
    ]


def _is_non_public_host(host: str) -> bool:
    """字面量的内网/环回/链路本地/保留地址判定。刻意不做 DNS 解析（解析引入时延与探测面）；
    域名指向内网的情况由主机白名单兜住。"""
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        # 非字面量 IP：只挡本地解析必然命中的名字
        return host == "localhost" or host.endswith(".localhost")
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.unspecified
    )


def _validate_url_value(defn: SettingDefinition, value: str) -> str:
    """校验 url 类设置值，返回规范化后的值（不合法抛 ValueError）

    用 urlsplit 而不是 startswith 判断协议：`startswith("http://")` 会放过
    `http://deepseek.com@evil.com/`（真实主机是 evil.com）这类凭据前缀形态。
    """
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"「{defn.label}」仅支持 http(s) 链接")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"「{defn.label}」缺少主机名")
    if _is_non_public_host(host):
        raise ValueError(f"「{defn.label}」不允许指向内网、环回或链路本地地址")
    if defn.key in _KEY_BEARING_SETTING_KEYS:
        allowed = _allowed_ai_host_suffixes()
        if allowed and not any(
            host == suffix or host.endswith("." + suffix) for suffix in allowed
        ):
            raise ValueError(
                f"「{defn.label}」只能是白名单内的 AI 服务域名（当前允许：{', '.join(allowed)}）。"
                f"使用自建 OpenAI 兼容代理时，在 .env 的 AI_BASE_URL_ALLOWED_HOSTS 追加域名即可，无需改代码。"
            )
    return value


def validate_ai_base_url(value: str, label: str = "Base URL") -> str:
    """AI 模型 BaseURL 的统一校验入口（模型管理复用设置页的 SSRF/白名单规则）。

    必须与设置页的 url 校验同一口径：这条 URL 会被 httpx 带上 API Key 请求，
    放任它指向内网或任意主机等于把密钥送出去。空串表示「回退供应商 .env」。
    """
    if not value:
        return ""
    defn = SettingDefinition(
        key=_AI_MODEL_BASE_URL_KEY,
        label=label,
        type="url",
        group="ai",
        group_label="AI 成本护栏",
    )
    return _validate_url_value(defn, value)


# 前台法律文档类型 → 设置项映射（doc_type 用于 URL 路径）
LEGAL_DOC_TYPES: dict[str, dict[str, str]] = {
    "privacy": {"key": "legal_privacy", "title": "隐私政策"},
    "agreement": {"key": "legal_agreement", "title": "用户协议"},
    "disclaimer": {"key": "legal_disclaimer", "title": "免责声明"},
}

_DEFINITIONS_BY_KEY = {d.key: d for d in SETTING_DEFINITIONS}


async def get_stored_settings(
    db: AsyncSession,
    *,
    mask_secrets: bool = True,
    surface: str | None = None,
) -> dict[str, str]:
    """获取已显式配置的设置值（未配置/空值项不返回）

    :param surface: 只返回该管理界面负责的键（最小暴露面）；None = 全部

    ⚠️ secret 项默认**掩码**返回：管理端只需要知道「配没配、配的是哪把」，
    不应拿到明文。需要真实值的场景（AI Provider 运行时）走 get_secret_setting。
    """
    allowed = keys_for_surface(surface) if surface is not None else None
    rows = await db.execute(select(SystemSetting))
    result: dict[str, str] = {}
    for row in rows.scalars().all():
        if not row.value:
            continue
        if allowed is not None and row.setting_key not in allowed:
            continue
        defn = _DEFINITIONS_BY_KEY.get(row.setting_key)
        if defn is not None and defn.secret:
            result[row.setting_key] = mask_secret(row.value) if mask_secrets else row.value
        else:
            result[row.setting_key] = row.value
    return result


async def get_bool_setting(db: AsyncSession, key: str, default: bool = False) -> bool:
    """读取布尔设置（未配置/脏值回退 default）；只查一行，供登录等热路径使用。"""
    row = (
        await db.execute(select(SystemSetting).where(SystemSetting.setting_key == key))
    ).scalar_one_or_none()
    if row is None or not row.value:
        return default
    return str(row.value).strip().lower() in ("1", "true", "on", "yes", "y")


async def get_secret_setting(db: AsyncSession, key: str) -> str | None:
    """取密钥类设置项的**明文**（仅限服务端内部使用，绝不经接口外发）

    解密失败（SECRET_KEY 轮换 / 数据损坏）返回 None，调用方回退环境变量。
    """
    defn = _DEFINITIONS_BY_KEY.get(key)
    if defn is None or not defn.secret:
        raise ValueError(f"{key} 不是密钥类设置项")
    row = (
        await db.execute(select(SystemSetting).where(SystemSetting.setting_key == key))
    ).scalar_one_or_none()
    if row is None or not row.value:
        return None
    return decrypt_secret(row.value)


async def get_public_settings() -> dict[str, str]:
    """公共设置（默认值 + DB 覆盖），Redis 缓存，故障降级直查 DB

    richtext 类型（法律文档）不进入公共设置：避免每个访客的首屏请求
    都携带几十 KB 的富文本，法律文档走独立的 get_legal_document。
    """
    cached = await _cache_get(PUBLIC_CACHE_KEY)
    if cached is not None:
        return cached

    # 默认值打底，DB 显式配置覆盖（仅认白名单内的键）
    # ⚠️ 必须同时排除 secret 项：本函数的结果会经 /api/v1/settings 直接发给
    # **所有访客**。此前过滤条件是「非 richtext 全放行」，新增密钥项后会原样外泄。
    merged = {
        d.key: d.default
        for d in SETTING_DEFINITIONS
        if d.type != "richtext" and not d.secret and d.public
    }
    try:
        async with async_session() as session:
            stored = await get_stored_settings(session)
    except Exception:
        logger.exception("读取系统设置失败，回退默认值")
        return merged
    for key, value in stored.items():
        if key in merged:
            merged[key] = value

    await _cache_set(PUBLIC_CACHE_KEY, merged)
    return merged


def _normalize_number(defn: SettingDefinition, value: str) -> str:
    """数值类设置：必须是非负有限数；返回去掉多余 0 的规范形态

    刻意允许小数（单价常见 0.5 / 0.0001 元/百万 tokens），
    但拒绝负数与 inf/nan —— 负数上限会让护栏「永久超限」，把 AI 功能整体打死。
    """
    if len(value) > _MAX_NUMBER_LENGTH:
        raise ValueError(f"「{defn.label}」不是合法数字（长度超限）")
    try:
        number = float(value)
    except ValueError:
        raise ValueError(f"「{defn.label}」必须是数字，当前填的是「{value}」")
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"「{defn.label}」必须是不小于 0 的数字")
    # 整数去掉小数点（"5000.0" → "5000"），小数保留原精度（"0.0001" 不能被压成 0）
    return str(int(number)) if number == int(number) else f"{number}"


def _normalize_json_object(defn: SettingDefinition, value: str) -> str:
    """JSON 对象类设置（当前只有按模型单价表）：结构必须与运行时解析口径一致

    在这里**严格拒绝**而不是「存进去让运行时忽略」：运行时解析失败只会打日志，
    表现为「后台填了单价、看板金额不变」，是最难排查的一类问题。
    """
    if len(value) > _MAX_JSON_LENGTH:
        raise ValueError(f"「{defn.label}」不能超过 {_MAX_JSON_LENGTH} 字符")
    try:
        data = json.loads(value)
    except ValueError as exc:
        raise ValueError(f"「{defn.label}」不是合法 JSON：{exc}")
    if not isinstance(data, dict):
        raise ValueError(
            f"「{defn.label}」必须是对象，形如 {{\"deepseek\": {{\"input\": 1, \"output\": 2}}}}"
        )
    for model, price in data.items():
        if not isinstance(price, dict):
            raise ValueError(f"「{defn.label}」里「{model}」的值必须是对象（含 input / output）")
        for field_name in ("input", "output"):
            if field_name not in price:
                continue
            try:
                number = float(price[field_name])
            except (TypeError, ValueError):
                raise ValueError(f"「{defn.label}」里「{model}.{field_name}」必须是数字")
            if not math.isfinite(number) or number < 0:
                raise ValueError(f"「{defn.label}」里「{model}.{field_name}」必须是不小于 0 的数字")
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _normalize_bool(defn: SettingDefinition, value: str) -> str:
    """布尔类设置统一存 "1" / "0"（前端 Switch 提交的也是这两个值）

    兼容 true/false/on/off 等写法：.env 里常见 `true`，运营手填时也常这么写，
    直接拒绝只会让人反复试错。
    """
    text = value.strip().lower()
    if text in ("1", "true", "on", "yes", "y"):
        return "1"
    if text in ("0", "false", "off", "no", "n"):
        return "0"
    raise ValueError(f"「{defn.label}」只能是 1 / 0（开启 / 关闭）")


def _validate_setting_value(defn: SettingDefinition, value) -> str:
    """校验并规范化单个设置值；返回空串表示「恢复默认」，不合法抛 ValueError"""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"设置项「{defn.label}」的值必须是字符串")
    value = value.strip()
    if not value:
        return ""
    # richtext 类型（法律文档 HTML）：仅限长度，内容安全由前台渲染端 DOMPurify 白名单清洗
    if defn.type == "richtext":
        if len(value) > _MAX_RICHTEXT_LENGTH:
            raise ValueError(f"「{defn.label}」的内容不能超过 {_MAX_RICHTEXT_LENGTH} 字符")
        return value
    # 数值 / JSON / 多行文本三类有各自的长度上限，须在通用 500 字符检查**之前**处理
    if defn.type == "number":
        return _normalize_number(defn, value)
    if defn.type == "json":
        return _normalize_json_object(defn, value)
    if defn.type == "textarea":
        if len(value) > _MAX_TEXTAREA_LENGTH:
            raise ValueError(f"「{defn.label}」的内容不能超过 {_MAX_TEXTAREA_LENGTH} 字符")
        return value
    if defn.type == "secret_text":
        # PEM 私钥/证书会超过普通字段的 500 字符上限；仍走 secret 加密落库
        if len(value) > _MAX_SECRET_TEXT_LENGTH:
            raise ValueError(f"「{defn.label}」的内容不能超过 {_MAX_SECRET_TEXT_LENGTH} 字符")
        return value
    if defn.type == "bool":
        return _normalize_bool(defn, value)
    if defn.type == "select":
        allowed = [str(o.get("value")) for o in defn.options]
        if value not in allowed:
            raise ValueError(f"「{defn.label}」只能是：{' / '.join(allowed)}")
        return value
    if len(value) > _MAX_VALUE_LENGTH:
        raise ValueError(f"「{defn.label}」的值不能超过 {_MAX_VALUE_LENGTH} 字符")
    if defn.type == "password":
        # 密钥类：只做长度上限（通用检查已限 _MAX_VALUE_LENGTH），不约束格式
        # ——各家 API Key 形态不一，加格式校验只会误伤
        return value
    if defn.type == "text":
        if len(value) > _MAX_NAME_LENGTH:
            raise ValueError(f"「{defn.label}」不能超过 {_MAX_NAME_LENGTH} 字")
        return value
    # url 类型（社交链接、AI Provider base_url 等）：协议 + 主机校验，
    # 阻断 javascript:/data: 注入，也阻断「把带密钥的请求发往内网/任意主机」（P1-51）
    if defn.type == "url":
        return _validate_url_value(defn, value)
    # image 类型：仅允许 http(s) URL 或以 / 开头的站内路径，
    # 阻断 javascript:/data: 等协议与外链跳转类注入
    if not value.startswith(("http://", "https://", "/")):
        raise ValueError(f"「{defn.label}」仅支持 http(s) 链接或以 / 开头的站内路径")
    return value


async def update_settings(db: AsyncSession, items: dict, *, surface: str | None = None) -> int:
    """批量更新设置（空值=清除恢复默认）。返回实际变更条数。

    :param surface: 限定本次可写的键范围（如 `SURFACE_LOGIN` 只允许登录配置）。
        None = 不限（内部调用 / 测试）。越界 key 直接报 ValueError，**整批放弃**。

        这层校验不是重复的权限检查，而是「收窄的权限码不能写宽范围」的兜底：
        `login_setting:update` 不该因为复用了设置写入通道就能顺手改 COS 密钥。

    不在此处 commit（由 get_db 依赖统一提交），调用方须在 commit 成功后
    调用 invalidate_public_settings_cache()，避免「清缓存先于 commit」的竞态。
    """
    if not items:
        raise ValueError("settings 不能为空")

    allowed = keys_for_surface(surface) if surface is not None else None

    # 先整体校验再落库，避免部分写入
    normalized: dict[str, str] = {}
    for key, value in items.items():
        defn = _DEFINITIONS_BY_KEY.get(key)
        if defn is None:
            raise ValueError(f"未知的设置项: {key}")
        if allowed is not None and key not in allowed:
            raise ValueError(f"设置项 {key} 不属于当前管理范围，请到对应模块修改")
        # 掩码 = 前端回显的占位值（用户没改这个字段）→ 跳过。
        # 前端本不该提交它，这里是纵深防御：即便提交了也不会把掩码写进库。
        if defn.secret and looks_like_mask(value if isinstance(value, str) else None):
            continue
        normalized[key] = _validate_setting_value(defn, value)

    # 强制 2FA 是「锁定后无法自救」的开关：开启前必须确认没有未绑定 TOTP 的管理员，
    # 否则下一次登录就会把人挡在门外。
    if normalized.get("admin_require_2fa") == "1":
        unbound = (
            await db.execute(
                select(func.count(User.id)).where(
                    User.role == "admin",
                    User.deleted_at.is_(None),
                    User.totp_enabled.is_(False),
                )
            )
        ).scalar() or 0
        if unbound:
            raise ValueError(
                f"仍有 {unbound} 个管理员未开启二次验证；"
                "请先让其在「个人中心 → 安全设置」绑定后再开启强制"
            )

    rows = {
        row.setting_key: row
        for row in (
            await db.execute(select(SystemSetting).where(SystemSetting.setting_key.in_(normalized)))
        ).scalars().all()
    }

    changed = 0
    for key, value in normalized.items():
        defn = _DEFINITIONS_BY_KEY[key]
        row = rows.get(key)
        if not value:  # 空值：清除恢复默认
            if row is not None:
                await db.delete(row)
                changed += 1
            continue
        store_value = encrypt_secret(value) if defn.secret else value
        if row is None:
            db.add(SystemSetting(setting_key=key, value=store_value))
            changed += 1
        elif defn.secret:
            # ⚠️ 密文必须与**明文**比较：Fernet 每次加密结果都不同（随机 IV），
            # 直接比密文会把「内容其实没变」误判成变更，每次都白写一遍
            if decrypt_secret(row.value) != value:
                row.value = store_value
                changed += 1
        elif row.value != store_value:
            row.value = store_value
            changed += 1
    return changed


async def invalidate_public_settings_cache() -> None:
    """失效公共设置缓存（保存成功后调用，下次请求回源重建）"""
    try:
        await redis_client.delete(PUBLIC_CACHE_KEY)
    except Exception:
        # Redis 故障不影响保存结果，缓存随 TTL 自然过期
        log_redis_degraded("setting.invalidate_settings", "Redis 不可用，系统设置缓存未主动失效（将随 TTL 过期）")


async def get_legal_document(doc_type: str) -> dict[str, str]:
    """获取单个法律文档（隐私政策/用户协议/免责声明）

    默认值 + DB 覆盖，独立 Redis 缓存（与公共设置缓存隔离，
    避免大体积富文本影响高频的公共设置接口）。未知类型抛 ValueError。
    """
    meta = LEGAL_DOC_TYPES.get(doc_type)
    if meta is None:
        raise ValueError(f"未知的文档类型: {doc_type}")

    cache_key = f"{LEGAL_CACHE_PREFIX}{doc_type}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached

    defn = _DEFINITIONS_BY_KEY[meta["key"]]
    content = defn.default
    try:
        async with async_session() as session:
            row = (
                await session.execute(
                    select(SystemSetting).where(SystemSetting.setting_key == defn.key)
                )
            ).scalar_one_or_none()
            if row and row.value:
                content = row.value
    except Exception:
        logger.exception("读取法律文档失败，回退默认值")

    doc = {"type": doc_type, "title": meta["title"], "content": content}
    await _cache_set(cache_key, doc)
    return doc


async def invalidate_legal_documents_cache() -> None:
    """失效全部法律文档缓存（设置保存成功后调用）"""
    try:
        await redis_client.delete(*(f"{LEGAL_CACHE_PREFIX}{t}" for t in LEGAL_DOC_TYPES))
    except Exception:
        log_redis_degraded("setting.invalidate_legal", "Redis 不可用，法律文档缓存未主动失效（将随 TTL 过期）")


async def _cache_get(key: str) -> dict[str, str] | None:
    """缓存读取（Redis 故障降级返回 None 直查 DB；缓存值损坏时删除重建）"""
    try:
        cached = await redis_client.get(key)
    except Exception:
        log_redis_degraded("setting.get_public_settings", "Redis 不可用，系统设置降级直查 DB")
        return None
    if not cached:
        return None
    try:
        data = json.loads(cached)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        logger.warning("系统设置缓存损坏，删除后重建")
        try:
            await redis_client.delete(key)
        except Exception as exc:  # noqa: BLE001 降级不阻断主流程
            log_redis_degraded("setting_service._cache_get", exc)
        return None


async def _cache_set(key: str, value: dict[str, str]) -> None:
    """缓存写入（Redis 故障静默跳过）"""
    try:
        await redis_client.set(key, json.dumps(value, ensure_ascii=False), ex=PUBLIC_CACHE_TTL)
    except Exception as exc:  # noqa: BLE001 降级不阻断主流程
        log_redis_degraded("setting_service._cache_set", exc)
