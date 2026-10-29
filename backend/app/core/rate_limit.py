"""API 限流中间件 - Redis 固定窗口计数器"""

import ipaddress
import time
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.redis import log_redis_degraded, redis_client
from app.core.exceptions import AppException, error_response
from app.config import settings


# 免限流的精确路径（探活给 Compose/nginx 探针用，频率低）；放精确匹配是为了让 /health/deep 不被前缀名单连带放过
WHITELIST_EXACT = ("/api/v1/health",)
# 免限流的前缀路径（处理尾部斜杠变体）。⚠️ 绝不能把 /api/v1/health 放进来：前缀匹配会连带免掉
# /health/deep 的限流与鉴权（该端点缓存未命中时要在事件循环上 AST 解析全部迁移文件，匿名循环探测等于给全站加压）
WHITELIST_PREFIXES = ("/docs", "/redoc", "/openapi.json", "/")

# 敏感端点：Redis 异常时 fail-closed，避免限流被绕过后遭到爆破
SENSITIVE_PATHS = {
    "/api/v1/auth/send-code",
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
    # 安装向导：创建首个管理员，未初始化期间对全网可达，Redis 挂了更不能放行
    "/api/v1/setup/init",
    # 小程序登录/绑定：同样接受账号密码，且静默登录会在每次冷启动触发
    "/api/v1/auth/miniapp/login",
    "/api/v1/auth/miniapp/bind",
    # 手机号验证码（短信成本 + 账号枚举面）与手机号登录（TOTP/验证码爆破面）
    "/api/v1/auth/phone/send-code",
    "/api/v1/auth/phone/login",
    "/api/v1/auth/phone/bind",
    # 微信 state 存 Redis；Redis 异常时应 fail-closed，而不是退化成无 state 登录
    "/api/v1/auth/wechat/web/start",
    "/api/v1/auth/wechat/web/callback",
}

# 默认可信代理网段：回环 + RFC1918 私网（容器部署 nginx 位于 docker 私网）
_DEFAULT_TRUSTED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
]


def _load_trusted_networks() -> list:
    """解析可信代理网段列表（TRUSTED_PROXIES 环境变量覆盖默认私网）"""
    if settings.TRUSTED_PROXIES:
        networks = []
        for item in settings.TRUSTED_PROXIES.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                networks.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                logger.warning(f"TRUSTED_PROXIES 配置项无效，已忽略: {item}")
        return networks or _DEFAULT_TRUSTED_NETWORKS
    return _DEFAULT_TRUSTED_NETWORKS


_TRUSTED_NETWORKS = _load_trusted_networks()


def _is_trusted_proxy(host: str) -> bool:
    """判断直连对端是否可信代理"""
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in _TRUSTED_NETWORKS)


def get_client_ip(request: Request) -> str:
    """获取真实客户端 IP（仅信任可信代理转发头）。

    X-Forwarded-For 从右向左跳过仍属可信代理的条目，取第一个非可信地址；其次 X-Real-IP。
    不能只取最右值：生产链路是「客户端 → CDN → nginx → app」，最右永远是 CDN 出口 IP，
    会让同一 POP 下所有匿名访客共用一个限流桶（脚本可零成本 DoS），审计日志也无法归因。
    """
    peer = request.client.host if request.client else None
    if peer and _is_trusted_proxy(peer):
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
            for candidate in reversed(parts):
                if not _is_trusted_proxy(candidate):
                    return candidate
            # 整条链都落在可信网段（纯内网调用等）：取最右值，
            # 绝不回退到最左值 —— 那是唯一可被客户端任意伪造的一段
            if parts:
                return parts[-1]
        x_real_ip = request.headers.get("x-real-ip")
        if x_real_ip and x_real_ip.strip():
            return x_real_ip.strip()
    return peer or "unknown"


async def check_ip_daily_quota(request: Request, scope: str, limit: int, message: str) -> None:
    """按客户端 IP 的自然日配额。先 SET NX EX 落 TTL 再 INCR（反序会产生无 TTL 的永久计数 key），
    TTL == -1 时自愈；Redis 故障记降级并放行（账号级频控由调用方保证）。"""
    ip = get_client_ip(request)
    daily_key = f"{scope}:ip_daily:{ip}"
    try:
        pipe = redis_client.pipeline()
        pipe.set(daily_key, "0", nx=True, ex=86400)
        pipe.incr(daily_key)
        pipe.ttl(daily_key)
        _, count, ttl = await pipe.execute()
        if ttl == -1:
            await redis_client.expire(daily_key, 86400)
        if count > limit:
            raise AppException(code=429, message=message)
    except AppException:
        raise
    except Exception as exc:
        # 带上异常类型名：这一支是 fail-open，若把「管线命令不支持」这类**编程错误**
        # 也记成「Redis 不可用」，配额就会长期静默失效而日志看起来完全正常
        log_redis_degraded(
            f"rate_limit.{scope}_ip_quota",
            f"IP 日配额降级跳过（{type(exc).__name__}: {exc}）",
        )


async def check_user_daily_quota(user_id: int, scope: str, limit: int, message: str) -> None:
    """按登录用户 ID 的自然日配额，供「没有业务行可数」的写入口（如评论配图上传）使用；
    语义与 check_ip_daily_quota 对齐，Redis 故障记降级并放行（不该因缓存抖动让正常用户传不上图）。"""
    daily_key = f"{scope}:user_daily:{user_id}"
    try:
        pipe = redis_client.pipeline()
        pipe.set(daily_key, "0", nx=True, ex=86400)
        pipe.incr(daily_key)
        pipe.ttl(daily_key)
        _, count, ttl = await pipe.execute()
        if ttl == -1:
            await redis_client.expire(daily_key, 86400)
        if count > limit:
            raise AppException(code=429, message=message)
    except AppException:
        raise
    except Exception as exc:
        log_redis_degraded(
            f"rate_limit.{scope}_user_quota",
            f"用户日配额降级跳过（{type(exc).__name__}: {exc}）",
        )


def _is_pool_exhaustion(exc: BaseException) -> bool:
    """区分「本地连接池打满」与「Redis 真的不可用」：池打满时 Redis 是活的，拒绝只会放大故障（放行 + 高声告警）。
    用名称/文案兜底而非 isinstance：MaxConnectionsError 在较老的 redis-py 里不存在。"""
    cls = type(exc)
    if cls.__name__ == "MaxConnectionsError":
        return True
    return "too many connections" in str(exc).lower()


class RateLimitMiddleware:
    """基于 Redis 的固定窗口计数器限流（纯 ASGI 实现，避免 BaseHTTPMiddleware 每请求的额外 task 与响应流包装开销）"""

    def __init__(
        self,
        app,
        guest_limit: Optional[int] = None,
        user_limit: Optional[int] = None,
        window: Optional[int] = None,
    ):
        self.app = app
        # 显式传参优先（测试 / 特殊部署）；未传的项在**每次请求**重新读 settings，
        # 这样后台「系统设置 → 访问限流」改完立即生效，不必等进程重启
        self._explicit_guest = guest_limit
        self._explicit_user = user_limit
        self._explicit_window = window
        # 显式传 0（全部拒绝）不被 `or` 短路成默认值
        self.guest_limit = guest_limit if guest_limit is not None else settings.RATE_LIMIT_GUEST
        self.user_limit = user_limit if user_limit is not None else settings.RATE_LIMIT_USER
        self.window = window if window is not None else settings.RATE_LIMIT_WINDOW

    def _refresh_limits(self) -> None:
        """把后台可改的阈值同步到实例属性（显式传参的维度不覆盖）"""
        if self._explicit_guest is None:
            self.guest_limit = settings.RATE_LIMIT_GUEST
        if self._explicit_user is None:
            self.user_limit = settings.RATE_LIMIT_USER
        if self._explicit_window is None:
            self.window = settings.RATE_LIMIT_WINDOW

    async def __call__(self, scope, receive, send):
        # 非 HTTP 流量（如 lifespan）直接放行
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 阈值可能在后台被改过：每请求刷新一次（读两个 settings 属性的成本可忽略）
        self._refresh_limits()

        # 轻量包装：仅读取 scope 中的方法/路径/头，不消费 body
        request = Request(scope)

        path = request.url.path
        # 健康检查和文档不限流（精确名单 + 前缀名单，处理尾部斜杠变体）
        if path in WHITELIST_EXACT or any(
            path == p or path.startswith(p + "/") for p in WHITELIST_PREFIXES
        ):
            await self.app(scope, receive, send)
            return

        # CORS 预检请求直接放行（由 CORSMiddleware 消化，不消耗限流配额）
        if request.method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        # 获取客户端真实 IP（仅信任可信代理转发头）
        client_ip = get_client_ip(request)

        # 尝试从 JWT 获取用户 ID（Authorization 头优先，回退 httpOnly cookie，
        # 与 dependencies.pick_token 口径一致，避免主前端登录用户被按 IP 误杀）
        user_id = self._extract_user_id(request)

        limit_key = f"rate_limit:{f'user:{user_id}' if user_id else f'ip:{client_ip}'}"
        limit = self.user_limit if user_id else self.guest_limit

        # 固定窗口计数器（仅 2 个 Redis 命令：incr + expire）
        try:
            now = time.time()
            window_key = f"{limit_key}:{int(now // self.window)}"
            pipe = redis_client.pipeline()
            pipe.incr(window_key)
            pipe.expire(window_key, self.window)
            results = await pipe.execute()

            request_count = results[0]
            if request_count > limit:
                response = JSONResponse(
                    status_code=429,
                    content=error_response("请求过于频繁，请稍后再试", 429),
                    headers={"Retry-After": str(self.window)},
                )
                await response(scope, receive, send)
                return
        except Exception as e:
            # 连接池打满 ≠ Redis 不可用：此时拒绝的是正常用户，只会放大故障
            if _is_pool_exhaustion(e):
                logger.error(
                    f"Redis 连接池打满（限流本轮放行，请查 max_connections 与并发来源）: {e}"
                )
                await self.app(scope, receive, send)
                return
            # Redis 异常：敏感端点 fail-closed，其他端点 fail-open
            if path in SENSITIVE_PATHS:
                log_redis_degraded("rate_limit.sensitive_fail_closed", f"限流中间件 Redis 异常（敏感端点 {path}），拒绝请求: {e}")
                response = JSONResponse(
                    status_code=429,
                    content=error_response("服务繁忙，请稍后再试", 429),
                )
                await response(scope, receive, send)
                return
            log_redis_degraded("rate_limit.skip", f"限流中间件 Redis 异常，跳过限流: {e}")

        await self.app(scope, receive, send)

    @staticmethod
    def _extract_user_id(request: Request) -> Optional[str]:
        """从 Authorization 头或 cookie 中的 access token 提取用户 ID（解析失败按游客处理）"""
        from app.core.security import ACCESS_TOKEN_COOKIE, decode_token

        token = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]
        else:
            token = request.cookies.get(ACCESS_TOKEN_COOKIE)
        if not token:
            return None
        try:
            payload = decode_token(token)
            if payload and payload.get("type") == "access":
                return payload.get("sub")
        except Exception as exc:  # noqa: BLE001 无效/伪造 token 属常态，只 debug 记录
            # 刻意不用 warning：token 由客户端提供，伪造请求可任意触发，打 warning 等于给攻击者一个日志放大器
            logger.debug(f"访问令牌解析失败，按未认证处理：{exc}")
        return None
