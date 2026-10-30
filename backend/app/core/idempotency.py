"""写操作幂等（`Idempotency-Key`）。

**解决什么**：管理端的写操作大多是「点一下改一条数据」，重复执行会直接改变结果 ——
批量删除点两次、发通知点两次、网络超时后前端重试一次，都会变成两份副作用。
前端按钮的 loading 只能挡住「手快连点」，挡不住重试、代理重发、多标签页。

**怎么做**：客户端在写请求上带一个 `Idempotency-Key` 头（同一个「用户动作」用同一个 key），
服务端把这个 key 的**响应**记下来。同一个 key 再来：
- 上次还在处理 → 409（让调用方知道别急着重试）；
- 上次已完成 → 原样返回上次的响应（带 `X-Idempotent-Replay: true`），业务逻辑不再执行。

**为什么是中间件而不是给 126 个端点各加一个依赖**：
幂等是横切关注点，逐个端点加会漏、会不一致，而且新端点默认就没有。
放在中间件里，作用范围由「路径 + 方法」统一决定，新增端点自动获得。

**边界**（刻意的取舍，改之前先读）：
- 只管 `/api/v1/admin` 下的写方法：内容 API 与认证接口的语义不同，不套这一层；
- **不带 key 就不生效**，不改变任何现有行为 —— 幂等是可选增强，不是强制协议；
- 只缓存 2xx：4xx/5xx 清掉占位符，允许调用方修正后重试（缓存失败响应会让「重试」失去意义）；
- SSE 与超过 `MAX_CACHE_BYTES` 的响应不缓存（流式响应整体缓冲会毁掉体验）；
- Redis 故障 **fail-open**：幂等是安全网，不是准入门槛，缓存不可用不该让写操作失败。
"""

import hashlib
import json
import time

from loguru import logger

from app.redis import log_redis_degraded, redis_client

# 只对管理端写方法生效
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_PATH_PREFIX = "/api/v1/admin"

IDEMPOTENCY_HEADER = b"idempotency-key"
REPLAY_HEADER = (b"x-idempotent-replay", b"true")

# 占位符存活时间：一次请求最长能跑多久。超时后占位符自动消失，避免「处理中」永久卡死。
_PROCESSING_TTL = 60
# 结果缓存时长：覆盖「用户看到报错后手动重试」与「网关重发」的常见窗口
_RESULT_TTL = 24 * 3600
# 单个 key 的最大长度（防有人拿超长字符串塞爆 Redis）
_MAX_KEY_LEN = 128
# 超过这个体积的响应不缓存：大响应存进 Redis 不划算，也不值得为它增加内存风险
MAX_CACHE_BYTES = 32 * 1024

_KEY_PREFIX = "idem:"


def _header(scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _scope_token(scope) -> str:
    """按凭据派生作用域：同一个 key 在不同会话/账号下互不干扰。

    不直接用 user_id：中间件跑在鉴权之前，这里拿不到当前用户；
    而 token 本身已经唯一标识了会话，哈希后只用于分组，不落盘原文。
    """
    auth = _header(scope, b"authorization") or ""
    cookie = _header(scope, b"cookie") or ""
    return hashlib.sha256(f"{auth}|{cookie}".encode("utf-8", "ignore")).hexdigest()[:16]


def _key_for(scope, idem_key: str) -> str:
    return f"{_KEY_PREFIX}{_scope_token(scope)}:{idem_key}"


async def _redis(event: str, call, default=None):
    """Redis 调用的**单点**收口：故障时记降级日志并返回 default。

    整个模块只保留这一个宽泛捕获（`test_degrade_ratchet` 只许减不许增）。
    用 `log_redis_degraded` 而不是 `degrade_guard`：前者带 60s 窗口限流，
    Redis 抖动时不会把日志刷满。
    """
    try:
        return await call()
    except Exception as exc:  # noqa: BLE001 Redis 故障类型不固定，只能宽泛捕获
        log_redis_degraded(event, exc)
        return default


async def _send_json(send, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})


class IdempotencyMiddleware:
    """纯 ASGI 中间件：需要看到完整响应体才能缓存，所以不能用 BaseHTTPMiddleware。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        idem_key = _header(scope, IDEMPOTENCY_HEADER)
        eligible = (
            scope.get("method") in _WRITE_METHODS
            and str(scope.get("path", "")).startswith(_PATH_PREFIX)
            and bool(idem_key)
            and len(idem_key) <= _MAX_KEY_LEN
        )
        if not eligible:
            await self.app(scope, receive, send)
            return

        key = _key_for(scope, idem_key)
        replayed = await self._replay_if_known(key, send)
        if replayed:
            return

        acquired = await self._acquire(key)
        if acquired is False:
            # 抢占失败：可能是并发重复（占位符还在），也可能是刚完成（结果已写入）
            if await self._replay_if_known(key, send):
                return
            await _send_json(
                send,
                409,
                {
                    "code": 409,
                    "message": "相同请求正在处理中，请稍后再试",
                    "data": None,
                },
            )
            return

        await self._run_and_cache(key, scope, receive, send, cacheable=acquired is True)

    # ---------- Redis 交互（全部 fail-open） ----------

    async def _replay_if_known(self, key: str, send) -> bool:
        raw = await _redis("idempotency.read", lambda: redis_client.get(key))
        if not raw:
            return False
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            return False
        if record.get("state") == "processing":
            return False
        body = record.get("body", "")
        await send(
            {
                "type": "http.response.start",
                "status": int(record.get("status", 200)),
                "headers": [
                    (b"content-type", str(record.get("content_type", "application/json")).encode()),
                    REPLAY_HEADER,
                ],
            }
        )
        await send({"type": "http.response.body", "body": body.encode("utf-8")})
        return True

    async def _acquire(self, key: str) -> bool | None:
        """抢占占位符。True=抢到且可缓存；False=没抢到；None=Redis 不可用（放行且不缓存）"""
        ok = await _redis(
            "idempotency.acquire",
            lambda: redis_client.set(
                key, json.dumps({"state": "processing"}), nx=True, ex=_PROCESSING_TTL
            ),
            default=None,
        )
        if ok is None:
            return None
        return bool(ok)

    async def _store(self, key: str, record: dict | None) -> None:
        if record is None:
            await _redis("idempotency.store", lambda: redis_client.delete(key))
            return
        await _redis(
            "idempotency.store",
            lambda: redis_client.set(
                key, json.dumps(record, ensure_ascii=False), ex=_RESULT_TTL
            ),
        )

    # ---------- 执行并缓存 ----------

    async def _run_and_cache(self, key, scope, receive, send, *, cacheable: bool) -> None:
        status: int = 500
        headers: list[tuple[bytes, bytes]] = []
        body = bytearray()
        passthrough = False  # SSE 等流式响应：立即转发，不缓冲

        async def send_wrapper(message):
            nonlocal status, headers, passthrough
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers") or [])
                content_type = ""
                for name, value in headers:
                    if name.lower() == b"content-type":
                        content_type = value.decode("latin-1").lower()
                if "text/event-stream" in content_type:
                    # 流式响应一旦缓冲就等于「等生成完再一次性返回」，直接透传
                    passthrough = True
                    await send(message)
                return
            if message["type"] == "http.response.body":
                if passthrough:
                    await send(message)
                    return
                body.extend(message.get("body", b""))
                return
            await send(message)

        started = time.time()
        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException:
            # 下游抛异常（含取消）：清掉占位符让调用方能重试，再把异常交给外层
            # （Sentry / 全局 handler）。这里刻意用 BaseException —— 连 CancelledError
            # 也要清，否则客户端断开会让这个 key 卡到占位符过期。
            if cacheable:
                await self._store(key, None)
            raise

        if passthrough:
            # 流式响应无法缓存，但**必须清掉占位符**：否则这个 key 在 _PROCESSING_TTL 内
            # 一直被当成「处理中」，重试会拿到 409，用户只能等 60 秒或换个 key。
            if cacheable:
                await self._store(key, None)
            return

        # 统一转发（缓冲模式：拿到完整响应后再发）
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": bytes(body)})

        if not cacheable:
            return

        if 200 <= status < 300 and len(body) <= MAX_CACHE_BYTES:
            content_type = "application/json"
            for name, value in headers:
                if name.lower() == b"content-type":
                    content_type = value.decode("latin-1")
            await self._store(
                key,
                {
                    "state": "done",
                    "status": status,
                    "content_type": content_type,
                    "body": body.decode("utf-8", "ignore"),
                },
            )
            logger.bind(event="idempotency.cache", status=status).debug(
                f"幂等缓存写入 {key}（{len(body)}B, {int((time.time() - started) * 1000)}ms）"
            )
        else:
            # 非 2xx 或超大响应：清掉占位符，允许重试
            await self._store(key, None)
