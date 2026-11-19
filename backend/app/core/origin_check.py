"""写请求的 Origin 校验（CSRF 显式化，2026-11-20，对标文档 S7）。

现状与缺口：refresh cookie 是 `SameSite=Lax`，跨站 POST 天然不带 cookie ——
但「依赖浏览器默认行为」与「服务端显式拒绝」是两回事：开源部署里总有人改
cookie 属性、挂反向代理抹 SameSite，或从旧浏览器流量里漏进来。S7 的结论是
**显式化**：写方法带 Origin 头时，服务端必须亲口回答「这个来源我认不认」。

放行规则（三条，逐条可解释）：
- **无 Origin 头** → 放行：curl / 服务器间调用 / PAT 机器客户端本来就没有 Origin，
  这是开放 API 的一等公民，不能被 CSRF 防线误伤；
- **Origin 与 Host 头同源** → 放行：同源请求（含反代后 Host 透传正确的自部署）；
- **Origin ∈ CORS 白名单** → 放行：管理前端 5173 这类已声明的来源；
- 其余 → **403**（跨站页面带 cookie 发起的写请求 —— CSRF 的本来攻击面）。

纯 ASGI 实现：不进 BaseHTTPMiddleware（不消费请求体、零开销透传读请求）。
"""

from __future__ import annotations

from urllib.parse import urlsplit

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _origin_allowed(origin: str, host_header: str, allowlist: list[str]) -> bool:
    """纯函数判定（可离线单测）：origin 与 host 同源 / 在白名单 → True。"""
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if not parsed.scheme or not parsed.netloc:
        return False
    if origin.rstrip("/") in allowlist:
        return True
    # Host 头同源：Origin 的 netloc 与 Host 完全一致（含端口）即同源。
    # 反代场景 Host 由 nginx 透传给后端，正是「自部署站点的自己人」。
    return parsed.netloc.lower() == (host_header or "").lower()


class OriginCheckMiddleware:
    def __init__(self, app, allowlist: list[str] | None = None):
        self.app = app
        self.allowlist = [o.rstrip("/") for o in (allowlist or [])]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in _UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        origin = headers.get("origin", "")
        if origin and not _origin_allowed(origin, headers.get("host", ""), self.allowlist):
            body = '{"code":403,"message":"跨站写请求被拒绝（Origin 校验失败）","data":null}'.encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
