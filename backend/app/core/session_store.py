"""登录会话登记簿（Redis）。

**为什么需要它**

此前 refresh_token 是「每用户一个键」（`refresh_token:{uid}`），多设备登录时后者覆盖前者。
由此带来两个真实会撞上的问题：

1. 用户看不到「我现在有哪些登录」——怀疑密码泄露时，无从判断该不该全部踢掉；
2. 只能「一键全踢」（递增令牌版本号），没法只踢掉某个陌生设备。

本模块把每次登录登记成一条会话，refresh 按会话隔离：

    sess:{sid}                → JSON 字符串（uid / ua / ip / created_at / last_seen / access_jti）
    sess_idx:{uid}            → set of sid（枚举用）
    refresh_token:{uid}:{sid} → 该会话的 refresh token（读写仍由 core.security 负责）

用 JSON 字符串而不是 hash：会话记录总是整体读、整体写（刷新时改 last_seen 与 jti），
没有「只更新某一个字段」的场景，hash 只会多一层命令兼容成本。

键空间与旧版 `refresh_token:{uid}` **并存**：旧 token 没有 sid，继续走旧键，
到期后自然消失，不需要数据迁移，也不会把已登录用户踢下线。

**故障策略**沿用 security.py 的既有取舍：写（登记 / 续期）fail-open ——
登录不能因为「记不住设备」而失败；读失败返回空并记降级日志。
这不影响认证主流程：真正决定「能不能进」的是 refresh 校验与令牌版本号，
登记簿只是可观测性与操作入口。
"""

import json
import secrets
import time
from typing import Any, Awaitable, Callable

from app.config import settings
from app.redis import log_redis_degraded, redis_client

SESSION_PREFIX = "sess:"
SESSION_INDEX_PREFIX = "sess_idx:"
# refresh 键前缀与 core.security 保持一致；此处直接删，避免循环导入
_REFRESH_PREFIX = "refresh_token:"

# 会话存活期与 refresh_token 一致：refresh 能续期多久，会话就该记多久
_TTL_SECONDS = settings.JWT_REFRESH_EXPIRE_DAYS * 24 * 3600


def new_session_id() -> str:
    """生成会话 id（与 access token 的 jti 不同：jti 每次刷新都会变，sid 整个会话周期不变）"""
    return secrets.token_urlsafe(16)


def describe_user_agent(ua: str | None) -> str:
    """把 User-Agent 压成「浏览器 · 系统」的短标签。

    只做保守识别：认不出就返回「未知设备」，不猜。前端要展示的是
    「是不是我自己的设备」，猜错比不猜更糟。
    """
    if not ua:
        return "未知设备"
    u = ua.lower()

    if "edg/" in u or "edgios/" in u:
        browser = "Edge"
    elif "opr/" in u or "opera" in u:
        browser = "Opera"
    elif "firefox/" in u or "fxios/" in u:
        browser = "Firefox"
    elif "chrome/" in u or "crios/" in u:
        browser = "Chrome"
    elif "safari/" in u:
        browser = "Safari"
    elif "curl/" in u or "python" in u or "httpx" in u:
        browser = "命令行"
    else:
        browser = ""

    if "android" in u:
        system = "Android"
    elif "iphone" in u or "ipad" in u or "ios" in u:
        system = "iOS"
    elif "windows" in u:
        system = "Windows"
    elif "mac os" in u or "macintosh" in u:
        system = "macOS"
    elif "linux" in u:
        system = "Linux"
    else:
        system = ""

    if browser and system:
        return f"{browser} · {system}"
    return browser or system or "未知设备"


async def _guarded(event: str, call: Callable[[], Awaitable[Any]], default: Any = None) -> Any:
    """Redis 调用的**单点**收口：故障时记降级日志并返回 default。

    刻意只保留这一个宽泛捕获（`test_degrade_ratchet` 只许减不许增，
    每多一处就多一个「失败被吞掉、没人知道」的面），
    且仍用 `log_redis_degraded` 而不是 `degrade_guard` —— 前者带 60s 窗口限流，
    Redis 抖动时不会把日志刷满，而这里正是 Redis 抖动最密集的地方。
    """
    try:
        return await call()
    except Exception as exc:  # noqa: BLE001 见 docstring：Redis 故障类型不固定，只能宽泛捕获
        log_redis_degraded(event, exc)
        return default


async def create_session(
    user_id: int,
    sid: str,
    access_jti: str,
    user_agent: str | None = None,
    client_ip: str | None = None,
) -> None:
    """登记一条会话（登录 / 刷新后调用）。fail-open：记不住不影响登录本身。"""
    now = int(time.time())
    payload = {
        "uid": user_id,
        "ua": (user_agent or "")[:512],
        "ip": (client_ip or "")[:64],
        "created_at": now,
        "last_seen": now,
        "access_jti": access_jti,
    }
    key = f"{SESSION_PREFIX}{sid}"
    index = f"{SESSION_INDEX_PREFIX}{user_id}"

    async def _write() -> None:
        await redis_client.set(key, json.dumps(payload, ensure_ascii=False), ex=_TTL_SECONDS)
        await redis_client.sadd(index, sid)
        await redis_client.expire(index, _TTL_SECONDS)

    await _guarded("session.create", _write)


async def touch_session(user_id: int, sid: str, access_jti: str | None = None) -> None:
    """刷新时续期：更新 last_seen（有新的 access_jti 时一并更新，供单会话吊销用）"""
    key = f"{SESSION_PREFIX}{sid}"
    index = f"{SESSION_INDEX_PREFIX}{user_id}"

    async def _touch() -> None:
        raw = await redis_client.get(key)
        if not raw:
            return
        payload = json.loads(raw)
        payload["last_seen"] = int(time.time())
        if access_jti:
            payload["access_jti"] = access_jti
        await redis_client.set(key, json.dumps(payload, ensure_ascii=False), ex=_TTL_SECONDS)
        await redis_client.expire(index, _TTL_SECONDS)

    await _guarded("session.touch", _touch)


async def get_session(user_id: int, sid: str) -> dict | None:
    """读取单条会话；uid 不匹配返回 None（防止拿别人的 sid 操作）"""
    raw = await _guarded("session.get", lambda: redis_client.get(f"{SESSION_PREFIX}{sid}"))
    if not raw:
        return None
    payload = json.loads(raw)
    if str(payload.get("uid")) != str(user_id):
        return None
    return _normalize(payload, sid)


async def list_sessions(user_id: int) -> list[dict]:
    """列出该用户全部会话，按最近活跃倒序（Redis 不可用时返回空列表并记降级）"""
    index = f"{SESSION_INDEX_PREFIX}{user_id}"

    async def _read() -> tuple[list[str], list[str | None]]:
        sids = await redis_client.smembers(index)
        if not sids:
            return [], []
        sid_list = list(sids)
        raws = await redis_client.mget([f"{SESSION_PREFIX}{sid}" for sid in sid_list])
        return sid_list, raws

    result = await _guarded("session.list", _read, default=([], []))
    sids, raws = result if result else ([], [])

    sessions: list[dict] = []
    stale: list[str] = []
    for sid, raw in zip(sids, raws):
        if not raw:
            # 会话已过期但索引里还留着 → 顺手清掉，避免列表越滚越长
            stale.append(sid)
            continue
        sessions.append(_normalize(json.loads(raw), sid))
    if stale:
        await _guarded("session.prune", lambda: redis_client.srem(index, *stale))
    sessions.sort(key=lambda item: item["last_seen"], reverse=True)
    return sessions


async def revoke_session(user_id: int, sid: str) -> dict | None:
    """吊销单条会话：删会话记录 + 从索引移除 + 删该会话的 refresh_token。

    返回被删会话的记录（调用方据此把它的 access_jti 加入黑名单，让当前 access_token 立即失效）。
    不存在或不属于该用户时返回 None（幂等：重复吊销不报错）。
    """
    record = await get_session(user_id, sid)
    if record is None:
        return None

    async def _drop() -> None:
        await redis_client.delete(
            f"{SESSION_PREFIX}{sid}", f"{_REFRESH_PREFIX}{user_id}:{sid}"
        )
        await redis_client.srem(f"{SESSION_INDEX_PREFIX}{user_id}", sid)

    ok = await _guarded("session.revoke", _drop, default=False)
    # 删除失败（Redis 不可用）时返回 None：调用方会拿到 404，
    # 比「界面上显示已下线、实际还能用」诚实
    return None if ok is False else record


async def revoke_other_sessions(user_id: int, keep_sid: str | None) -> list[dict]:
    """吊销除 `keep_sid` 之外的全部会话（「退出其他设备」）。返回被吊销的记录列表。"""
    revoked: list[dict] = []
    for item in await list_sessions(user_id):
        if keep_sid and item["sid"] == keep_sid:
            continue
        record = await revoke_session(user_id, item["sid"])
        if record:
            revoked.append(record)
    return revoked


async def revoke_all_sessions(user_id: int) -> list[dict]:
    """吊销该用户全部会话（注销 / 删号 / 强制下线时调用）"""
    return await revoke_other_sessions(user_id, keep_sid=None)


def _normalize(payload: dict, sid: str) -> dict:
    """会话记录 → 统一结构（时间戳转 int，便于前端直接格式化）"""

    def _int(name: str) -> int:
        try:
            return int(payload.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    ua = payload.get("ua") or ""
    return {
        "sid": sid,
        "user_agent": ua,
        "device": describe_user_agent(ua),
        "ip": payload.get("ip") or "",
        "created_at": _int("created_at"),
        "last_seen": _int("last_seen"),
        "access_jti": payload.get("access_jti") or "",
    }
