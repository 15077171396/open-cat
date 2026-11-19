"""IP 黑名单：解析、匹配与请求拦截（2026-11-19）。

## 为什么在应用层而不是 nginx

黑名单是**运营动作**（发现刷子/扫描器时当场拉黑），要「保存即生效、无需登服务器
改配置」。应用层中间件直接读运行期配置，后台保存后本进程立即生效、其余 worker
60 秒内跟上；nginx 层做不到（reload 是全局动作且需要登机）。

## 取舍

- **匹配语义**：每行一个 IPv4/IPv6 地址或 CIDR 网段（如 `203.0.113.7` / `10.0.0.0/8`），
  用标准库 `ipaddress` 解析；非法行**跳过不报错**（一行写坏不能让全站 500），
  管理页会把无效行标出来。
- **成本控制**：每次请求都重新 parse 字符串在大名单下不可接受 —— 按**配置值指纹**
  缓存解析结果，值不变零解析开销（与 module_registry 的指纹缓存同一手法）。
- **放行健康检查**：`/api/v1/health*` 不受黑名单影响 —— 探活是「系统是否活着」的
  事实陈述，被黑名单挡住会让运维误判为宕机（watchdog 用的出口 IP 也可能被误拉黑）。
- **失败方向**：解析/读取失败一律**放行**（fail-open）—— 黑名单是加固项，
  它自身故障不能把全站变成 403。
"""

from __future__ import annotations

import ipaddress
import json

from app.config import settings
from app.core.exceptions import error_response
from app.core.rate_limit import get_client_ip

# 健康检查前缀：探活请求不进黑名单判断
_HEALTH_PREFIXES = ("/api/v1/health",)

# 解析缓存：值指纹 → (网络对象列表, 无效行列表)
_cache_fingerprint: str | None = None
_cache_networks: tuple[ipaddress._BaseNetwork, ...] = ()  # noqa: SLF001 私有类型仅作标注
_cache_invalid: tuple[str, ...] = ()


def parse_blacklist(raw: str | None) -> tuple[list[str], list[str]]:
    """把黑名单文本解析成 (有效条目, 无效条目)。

    条目按行/逗号分隔；单 IP 与 CIDR 均可。返回的是**归一化后的字符串**
    （CIDR 原样保留，单 IP 展开为其网络对象的原文），无效行只报告不抛错。
    """
    valid: list[str] = []
    invalid: list[str] = []
    if not raw:
        return valid, invalid
    for part in raw.replace("\r", "\n").replace("\n", ",").split(","):
        entry = part.strip()
        if not entry:
            continue
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            invalid.append(entry)
            continue
        if "/" in entry:
            valid.append(str(network))
        else:
            valid.append(str(network.network_address))
    return valid, invalid


def _networks() -> tuple[list[ipaddress.IPv4Network | ipaddress.IPv6Network], list[str]]:
    """带指纹缓存的解析（黑名单通常不变，避免每请求 parse）"""
    global _cache_fingerprint, _cache_networks, _cache_invalid
    fingerprint = settings.IP_BLACKLIST or ""
    if fingerprint != _cache_fingerprint:
        valid, invalid = parse_blacklist(fingerprint)
        _cache_networks = tuple(
            ipaddress.ip_network(entry, strict=False) for entry in valid
        )
        _cache_invalid = tuple(invalid)
        _cache_fingerprint = fingerprint
    return list(_cache_networks), list(_cache_invalid)


def is_blocked(ip: str) -> bool:
    """判断 IP 是否命中黑名单（解析失败放行）。"""
    if not ip:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    networks, _ = _networks()
    return any(address in network for network in networks)


def invalid_entries() -> list[str]:
    """当前配置里的无效行（管理页展示「这些行不会生效」）。"""
    _, invalid = _networks()
    return list(invalid)


class IPBlacklistMiddleware:
    """纯 ASGI 黑名单拦截（与 RateLimitMiddleware 同款实现形态）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in _HEALTH_PREFIXES):
            await self.app(scope, receive, send)
            return

        # 构造一个轻量 request 供 get_client_ip 使用（信任代理配置与限流同源）
        from starlette.requests import Request

        request = Request(scope, receive=receive)
        client_ip = get_client_ip(request)
        if client_ip and is_blocked(client_ip):
            response = error_response("您的网络地址已被列入访问黑名单", 403)
            payload = json.dumps(response, ensure_ascii=False).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(payload)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": payload})
            return

        await self.app(scope, receive, send)
