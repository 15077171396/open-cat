"""测试公共夹具与辅助工具：零新增依赖（标准库 + httpx + 本应用代码）；模块内不顶层
import pytest，既可被 unittest 导入也可作 conftest。提供 FakeRedis（redis.asyncio 子集，支持 TTL）、
make_test_app（关闭限流、覆盖 get_db/get_current_user 的测试 app + AsyncClient）、make_user。"""

import asyncio
import fnmatch
import time
from contextlib import asynccontextmanager
from unittest import mock

import httpx
from fastapi import FastAPI

from app.dependencies import get_current_user, get_current_user_optional
from app.database import get_db
from app.models.user import User
from app.services import content_cache


class FakeRedis:
    """内存异步 Redis, 覆盖本仓库用到的命令子集 (含 TTL 语义)。

    TTL 以单调时钟 (time.monotonic) 计算, 过期键在访问时惰性删除。
    """

    def __init__(self):
        # key -> (value, expire_at_or_None)
        self._store: dict[str, tuple[object, float | None]] = {}
        # 列表单独存（定时任务命令队列用；不参与 TTL，够用）
        self._lists: dict[str, list] = {}

    def _now(self) -> float:
        return time.monotonic()

    def _expired(self, key: str) -> bool:
        entry = self._store.get(key)
        if entry is None:
            return True
        _, expire_at = entry
        if expire_at is not None and self._now() >= expire_at:
            del self._store[key]
            return True
        return False

    async def get(self, key: str):
        if self._expired(key):
            return None
        entry = self._store.get(key)
        return entry[0] if entry else None

    async def getdel(self, key: str):
        # GETDEL (Redis >= 6.2)：原子取值并删除
        value = await self.get(key)
        if value is not None:
            self._store.pop(key, None)
        return value

    async def mget(self, keys):
        """MGET：批量取值。缺失/已过期的键返回 None（与真实 Redis 一致），
        且返回列表长度与入参一致 —— 调用方依赖这一点按下标取值。"""
        return [await self.get(key) for key in keys]

    async def setex(self, key: str, ttl: int, value):
        expire_at = self._now() + ttl if ttl and ttl > 0 else None
        self._store[key] = (value, expire_at)

    # ---- List（定时任务命令队列：LPUSH 入队 / LPOP 消费 / LTRIM 截断）----

    async def lpush(self, key: str, *values):
        lst = self._lists.setdefault(key, [])
        for value in values:
            lst.insert(0, value)
        return len(lst)

    async def rpush(self, key: str, *values):
        lst = self._lists.setdefault(key, [])
        lst.extend(values)
        return len(lst)

    async def lpop(self, key: str):
        lst = self._lists.get(key)
        if not lst:
            return None
        return lst.pop(0)

    async def ltrim(self, key: str, start: int, stop: int):
        lst = self._lists.get(key)
        if lst is not None:
            # 与 Redis 一致的闭区间语义；stop 为负表示「从末尾倒数」
            end = stop + 1 if stop >= 0 else len(lst) + stop + 1
            self._lists[key] = lst[start:end]
        return True

    async def llen(self, key: str) -> int:
        return len(self._lists.get(key) or [])

    async def set(self, key: str, value, ex: int | None = None, nx: bool = False):
        # nx=True 时仅当键不存在才写入 (对齐 redis-py SET NX 语义)
        if nx and not self._expired(key) and key in self._store:
            return False
        expire_at = self._now() + ex if ex else None
        self._store[key] = (value, expire_at)
        return True

    async def delete(self, *keys: str) -> int:
        # 对齐 redis-py：支持一次删除多个 key
        deleted = 0
        for key in keys:
            if not self._expired(key) and key in self._store:
                deleted += 1
            self._store.pop(key, None)
        return deleted

    async def exists(self, key: str) -> bool:
        return not self._expired(key) and key in self._store

    async def ttl(self, key: str) -> int:
        if self._expired(key):
            return -2
        entry = self._store.get(key)
        if entry is None:
            return -2
        _, expire_at = entry
        if expire_at is None:
            return -1
        remaining = int(expire_at - self._now())
        return max(0, remaining)

    async def incr(self, key: str) -> int:
        if self._expired(key):
            self._store.pop(key, None)
        entry = self._store.get(key)
        current = int(entry[0]) if entry else 0
        current += 1
        # 保留原 TTL
        expire_at = entry[1] if entry else None
        self._store[key] = (current, expire_at)
        return current

    async def decr(self, key: str) -> int:
        if self._expired(key):
            self._store.pop(key, None)
        entry = self._store.get(key)
        current = int(entry[0]) if entry else 0
        current -= 1
        expire_at = entry[1] if entry else None
        self._store[key] = (current, expire_at)
        return current

    async def incrby(self, key: str, amount: int) -> int:
        """INCRBY：AI 用量统计累加 token 数时使用（台账 P1-2）。"""
        if self._expired(key):
            self._store.pop(key, None)
        entry = self._store.get(key)
        current = int(entry[0]) if entry else 0
        current += int(amount)
        expire_at = entry[1] if entry else None
        self._store[key] = (current, expire_at)
        return current

    async def decrby(self, key: str, amount: int) -> int:
        if self._expired(key):
            self._store.pop(key, None)
        entry = self._store.get(key)
        current = int(entry[0]) if entry else 0
        current -= int(amount)
        expire_at = entry[1] if entry else None
        self._store[key] = (current, expire_at)
        return current

    async def incrbyfloat(self, key: str, amount: float) -> float:
        if self._expired(key):
            self._store.pop(key, None)
        entry = self._store.get(key)
        current = float(entry[0]) if entry else 0.0
        current += float(amount)
        expire_at = entry[1] if entry else None
        self._store[key] = (current, expire_at)
        return current

    async def sadd(self, key: str, *members) -> int:
        """SADD：AI 用量按模型分桶时记录「当天/当月出现过哪些模型」（台账 P2-4）。

        返回新增成员数（与真实 Redis 一致）——调用方据此判断是否需要补 TTL。
        """
        if self._expired(key):
            self._store.pop(key, None)
        entry = self._store.get(key)
        current: set = set(entry[0]) if entry and isinstance(entry[0], set) else set()
        before = len(current)
        current.update(str(m) for m in members)
        expire_at = entry[1] if entry else None
        self._store[key] = (current, expire_at)
        return len(current) - before

    async def smembers(self, key: str) -> set:
        """SMEMBERS：返回集合副本（真实 Redis 返回 bytes，这里返回 str，
        调用方 `_safe_smembers` 两种都处理）。"""
        if self._expired(key):
            return set()
        entry = self._store.get(key)
        if entry is None or not isinstance(entry[0], set):
            return set()
        return set(entry[0])

    async def expire(self, key: str, ttl: int) -> bool:
        if self._expired(key):
            return False
        entry = self._store.get(key)
        if entry is None:
            return False
        expire_at = self._now() + ttl if ttl and ttl > 0 else None
        self._store[key] = (entry[0], expire_at)
        return True

    def pipeline(self):
        """简易 pipeline: 缓存命令, execute 时顺序执行。覆盖限流中间件用法。"""
        return _FakePipeline(self)

    async def scan_iter(self, match: str | None = None, count: int | None = None):
        """SCAN 游标迭代（按 match 过滤）。

        覆盖 ``content_cache._delete_by_prefix`` 的按前缀失效用法。
        ⚠️ 真实 SCAN 不保证顺序、可能重复返回；测试只关心「能否遍历到匹配键」，
        故此处直接对当前键集做 glob 匹配（先惰性清理已过期键）。
        """
        for key in list(self._store.keys()):
            if self._expired(key):
                continue
            if match is None or fnmatch.fnmatchcase(key, match):
                yield key

    def clear(self):
        self._store.clear()


class _FakePipeline:
    """FakeRedis.pipeline() 返回的伪 pipeline, 顺序执行缓存的命令。

    覆盖限流中间件（incr/expire）与令牌撤销校验（exists/get）的用法。
    新增命令时在此登记，未登记的命令会在 execute 时显式报错，
    避免"pipeline 静默丢命令"造成假绿。
    """

    def __init__(self, redis: "FakeRedis"):
        self._redis = redis
        self._commands: list = []

    def incr(self, key: str):
        self._commands.append(("incr", key))
        return self

    def expire(self, key: str, ttl: int):
        self._commands.append(("expire", key, ttl))
        return self

    def exists(self, key: str):
        self._commands.append(("exists", key))
        return self

    def get(self, key: str):
        self._commands.append(("get", key))
        return self

    def incrby(self, key: str, amount: int):
        # AI token 记账把 6~10 次累加合并进一条 pipeline（PERF-11）
        self._commands.append(("incrby", key, amount))
        return self

    def decrby(self, key: str, amount: int):
        self._commands.append(("decrby", key, amount))
        return self

    def incrbyfloat(self, key: str, amount: float):
        self._commands.append(("incrbyfloat", key, amount))
        return self

    def sadd(self, key: str, *members: str):
        # 同上：按模型分桶还要把模型名记进集合
        self._commands.append(("sadd", key, members))
        return self

    def setex(self, key: str, ttl: int, value):
        self._commands.append(("setex", key, ttl, value))
        return self

    def set(self, key: str, value, ex: int | None = None, nx: bool = False):
        # IP 日配额用「SET NX EX + INCR + TTL」三段（先落 TTL 再计数，
        # 反序会留下无 TTL 的计数 key），这里必须原样支持否则整条管线报错
        self._commands.append(("set", key, value, ex, nx))
        return self

    def ttl(self, key: str):
        self._commands.append(("ttl", key))
        return self

    async def execute(self):
        results = []
        for cmd in self._commands:
            if cmd[0] == "incr":
                results.append(await self._redis.incr(cmd[1]))
            elif cmd[0] == "expire":
                results.append(await self._redis.expire(cmd[1], cmd[2]))
            elif cmd[0] == "exists":
                results.append(await self._redis.exists(cmd[1]))
            elif cmd[0] == "get":
                results.append(await self._redis.get(cmd[1]))
            elif cmd[0] == "incrby":
                results.append(await self._redis.incrby(cmd[1], cmd[2]))
            elif cmd[0] == "decrby":
                results.append(await self._redis.decrby(cmd[1], cmd[2]))
            elif cmd[0] == "incrbyfloat":
                results.append(await self._redis.incrbyfloat(cmd[1], cmd[2]))
            elif cmd[0] == "sadd":
                results.append(await self._redis.sadd(cmd[1], *cmd[2]))
            elif cmd[0] == "setex":
                results.append(await self._redis.set(cmd[1], cmd[3], ex=cmd[2]))
            elif cmd[0] == "set":
                results.append(await self._redis.set(cmd[1], cmd[2], ex=cmd[3], nx=cmd[4]))
            elif cmd[0] == "ttl":
                results.append(await self._redis.ttl(cmd[1]))
            else:  # 防御：新增命令未在上方登记
                raise AssertionError(f"FakeRedis pipeline 未实现命令: {cmd[0]}")
        return results


def make_user(
    *,
    id: int = 1,
    account: str = "tester@example.com",
    role: str = "user",
    pending_auth_code: str | None = None,
    free_used_seconds: int = 0,
) -> User:
    """构造内存 User 对象 (不持久化)。"""
    return User(
        id=id,
        account=account,
        password_hash="$2b$12$placeholderhashplaceholderhashplaceholderhashplaceholde",
        role=role,
        pending_auth_code=pending_auth_code,
        free_used_seconds=free_used_seconds,
    )


class _NullSession:
    """占位 AsyncSession, 用于覆盖 get_db 依赖 (不实际访问数据库)。"""

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def close(self):
        pass


@asynccontextmanager
async def async_client(
    *,
    user: User | None = None,
    include_auth: bool = True,
):
    """构建 httpx AsyncClient, 指向覆盖依赖后的 FastAPI app。

    - ``user`` 不为 None 时, get_current_user 返回该 user。
    - ``include_auth=False`` 时不覆盖 get_current_user (用于测试未鉴权场景)。
    - 同时用 FakeRedis 替换限流中间件与 verification 的 redis_client, 避免测试
      访问真实 Redis (否则敏感端点 /register 在 Redis 不可用时会 fail-closed
      返回 429, 或与真实计数器串扰)。
    退出时清理 dependency_overrides 与 redis 补丁。
    """
    # 延迟导入, 避免在模块加载阶段触发 app.logging 文件 sink 创建
    from app.main import app
    from app.core import rate_limit as _rate_limit
    from app.core import rbac as _rbac
    from app.services import health_service as _health_service
    from app.utils import verification as _verification

    async def _override_db():
        yield _NullSession()

    async def _override_user():
        return user

    async def _override_rbac_check():
        # RBAC 细粒度权限校验依赖数据库角色查询，单测（覆盖 get_db 为空会话）
        # 跳过该层；RBAC 行为由 tests/test_rbac.py 专项覆盖
        pass

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[_rbac.check_route_permission] = _override_rbac_check
    if include_auth:
        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_current_user_optional] = _override_user

    fake_redis = FakeRedis()
    _orig_rate_redis = _rate_limit.redis_client
    _orig_verify_redis = _verification.redis_client
    _orig_health_redis = _health_service.redis_client
    _rate_limit.redis_client = fake_redis
    _verification.redis_client = fake_redis
    # 深度健康检查 (/health/deep) 的探测与缓存都在 health_service 内（P2-21 移入）
    _health_service.redis_client = fake_redis

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        _rate_limit.redis_client = _orig_rate_redis
        _verification.redis_client = _orig_verify_redis
        _health_service.redis_client = _orig_health_redis


# ========== pytest 兼容夹具 (pytest 安装后自动可用) ==========

def _pytest_fixture(func=None, **fixture_kwargs):
    """惰性装饰器: 无 pytest 时退化为原函数, 不影响 unittest 导入。

    支持两种用法: ``@_pytest_fixture`` 与 ``@_pytest_fixture(autouse=True)``。
    """

    def _wrap(f):
        try:
            import pytest  # type: ignore
        except Exception:
            return f
        return pytest.fixture(f, **fixture_kwargs)

    return _wrap(func) if func is not None else _wrap


@_pytest_fixture(autouse=True)
def _patch_content_cache_redis():
    """自动把 content_cache 的 redis_client 换成 FakeRedis（autouse）。

    为什么需要：管理端写操作在 commit 之后会失效公开缓存（见
    ``app/services/content_cache.py``）。测试若不替换 redis_client，这些调用会去连
    **真实 Redis** —— 本机未启动时每次要等 ``socket_connect_timeout``（2s），
    几十个用例累积会把整个套件从 20s 拖到 90s（实测）。

    用 autouse 而非逐个 setUp：避免将来新增测试时再次踩到同一个坑 ——
    这类「连了真实外部服务」的测试**不会失败，只会静默变慢**。
    """
    with mock.patch.object(content_cache, "redis_client", FakeRedis()):
        yield


@_pytest_fixture
def fake_redis():
    return FakeRedis()


@_pytest_fixture
def make_test_user():
    return make_user
