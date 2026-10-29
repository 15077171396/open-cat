"""公开内容缓存的键定义与失效集中地：公开侧写入与后台侧删除共用同一组常量，避免两边漂移。

⚠️ 按前缀删而非逐个删：limit 由前端参数决定、取值集合不封闭，逐个枚举必漏。
⚠️ 不用 KEYS（阻塞整个 Redis，本实例还承载限流与验证码），统一 SCAN 分批。
⚠️ 失效必须在 db.commit() 之后调用：提交前失效会被并发读用旧数据回填、失效被吃掉。
"""

from __future__ import annotations

import hashlib
import json
import random

from app.redis import log_redis_degraded, redis_client

# 键定义（公开侧写入、后台侧删除，两边必须一致）

# 形状版本位：缓存键存的是整份序列化结果，增删响应字段后旧结构会被原样返回且无任何报错
# （窗口=TTL）。改动任何公开响应的字段集合，就把下面的数字 +1（发版即刻 miss，旧键由 TTL 回收）
CACHE_SHAPE_VERSION = 3

def shape_key(name: str) -> str:
    """把形状版本拼进键名。所有承载整份响应体的缓存键都必须经它派生。"""
    return f"{name}:v{CACHE_SHAPE_VERSION}"

def home_aggregate_key() -> str:
    return shape_key("home_aggregate")

def public_stats_key() -> str:
    return shape_key("public_stats")

# 首页统计卡的 TTL（纳入本模块失效体系，防止改了不生效）
PUBLIC_STATS_TTL = 300

# 单次 SCAN 的游标批大小：够大以摊薄往返，够小以不长时间占用 Redis
_SCAN_BATCH = 200

# llms.txt 的进程内缓存（api/llms_txt.py）是**每 worker 一份**，删 Redis 键删不掉它。
# 跨 worker 失效靠这个代数键：内容变更时 INCR，各进程发现代数变化即重建。
# Redis 不可用时读侧返回 -1（与缓存里记的值一致 → 退化为纯 TTL 过期，不会每次重建）。
LLMS_GEN_KEY = "llms_txt:gen"

async def llms_generation() -> int:
    """读取 llms.txt 缓存代数（Redis 不可用返回 -1）"""
    try:
        raw = await redis_client.get(LLMS_GEN_KEY)
        return int(raw) if raw else 0
    except Exception as exc:  # noqa: BLE001 读不到就退化为 TTL 策略
        log_redis_degraded("content_cache.llms_generation", exc)
        return -1

async def _bump_llms_generation() -> None:
    """内容变更时递增代数，让各 worker 的 llms.txt 进程内缓存立即作废"""
    try:
        await redis_client.incr(LLMS_GEN_KEY)
    except Exception as exc:  # noqa: BLE001 失效失败不应让写操作报错
        log_redis_degraded("content_cache._bump_llms_generation", exc)

# 公开列表缓存（PERF-03）
#
# 列表接口原先零缓存：每个 (module, category, sort, page, page_size) 组合都直查 DB，
# 爬虫把 ?page=1..N 扫一遍就是 N×3 次查询，而内容 45 秒内几乎不变。
# 只缓存**无关键词**的浏览路径：搜索结果属长尾，命中率低而 key 基数无上限
# （一个随机词就是一个新 key），反而会把 Redis 当垃圾桶。
LIST_CACHE_TTL = 45
LIST_CACHE_JITTER = 15

def list_cache_key(module: str, params: dict) -> str:
    """列表缓存键：参数按固定顺序序列化后取摘要，长度有界（关键词/分类可能很长）"""
    raw = "|".join(f"{k}={params[k]}" for k in sorted(params) if params[k] not in (None, ""))
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]
    return f"{shape_key('list:' + module)}:{digest}"

async def get_cached_list(key: str) -> dict | None:
    """读列表缓存；未命中/损坏/Redis 不可用一律返回 None（回落到查库）"""
    try:
        raw = await redis_client.get(key)
    except Exception as exc:  # noqa: BLE001 缓存读失败不该影响列表
        log_redis_degraded("content_cache.get_cached_list", exc)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        # 形状版本升级后可能残留旧结构：删掉，下次重建
        try:
            await redis_client.delete(key)
        except Exception as exc:  # noqa: BLE001
            log_redis_degraded("content_cache.get_cached_list_delete", exc)
        return None
    return data if isinstance(data, dict) else None

async def set_cached_list(key: str, payload: dict) -> None:
    """写列表缓存（TTL 带抖动，避免同批 key 同时过期）"""
    ttl = LIST_CACHE_TTL + random.randint(0, LIST_CACHE_JITTER)
    try:
        await redis_client.set(key, json.dumps(payload, ensure_ascii=False), ex=ttl)
    except Exception as exc:  # noqa: BLE001
        log_redis_degraded("content_cache.set_cached_list", exc)

# 失效动作

async def _delete(*keys: str) -> None:
    """删除指定键。Redis 不可用时仅记日志（TTL 到期后自然刷新，不阻断写操作）。"""
    try:
        await redis_client.delete(*keys)
    except Exception as exc:  # noqa: BLE001 缓存失效失败不应让管理端写操作报错
        log_redis_degraded("content_cache._delete", f"缓存失效失败（keys={keys}），依赖 TTL 自然过期：{exc}")

async def _delete_by_prefix(prefix: str) -> int:
    """按前缀删除（SCAN 分批，**不用 KEYS**）。返回删除的键数；Redis 不可用时返回 0。"""
    deleted = 0
    try:
        async for key in redis_client.scan_iter(match=f"{prefix}*", count=_SCAN_BATCH):
            deleted += await redis_client.delete(key)
    except Exception as exc:  # noqa: BLE001 同上
        log_redis_degraded("content_cache._delete_by_prefix", f"按前缀失效缓存失败（prefix={prefix}），依赖 TTL 自然过期：{exc}")
    return deleted

async def invalidate_home() -> None:
    """首页聚合缓存（教程 / 分类 / 公告参与聚合）。"""
    await _delete(home_aggregate_key(), public_stats_key())

async def invalidate_tutorials() -> None:
    """教程增删改后调用：仅首页聚合（教程列表本身不缓存）。

    注：首页的「热门学习方向」卡片虽含各分类教程数，但该数据由
    `list_categories_with_tutorial_count` 直查 DB、不单独缓存，
    随首页聚合一起失效即可，无需额外处理 `categories:tutorial`。
    """
    await _delete(home_aggregate_key(), public_stats_key())
    await _bump_llms_generation()
    await _delete_by_prefix(f"{shape_key('list:tutorials')}:")