"""图库外链的 SSRF 边界：只允许 HTTPS、精确主机白名单与公网解析地址。"""

import ipaddress
import socket
from urllib.parse import urlparse


def is_public_host(host: str) -> bool:
    """域名解析结果必须全部是公网地址。

    白名单已经挡住了任意域名，这一步再防「白名单域名被解析到内网」的 DNS 事故。
    """
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def is_allowed_https_url(url: str, allowed_hosts) -> bool:
    parsed = urlparse(url or "")
    if parsed.scheme != "https":
        return False
    if parsed.port not in (None, 443):
        return False
    if parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower()
    if host not in {h.lower() for h in allowed_hosts}:
        return False
    return is_public_host(host)


def is_allowed_url(
    url: str, allowed_hosts=(), *, allow_public: bool = False, allow_http: bool = False,
) -> bool:
    """网页图源可指向任意公网主机；固定图源仍必须命中精确域名。"""
    parsed = urlparse(url or "")
    schemes = {"http", "https"} if allow_http else {"https"}
    if parsed.scheme not in schemes:
        return False
    default_port = 443 if parsed.scheme == "https" else 80
    if parsed.port not in (None, default_port):
        return False
    if parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if not allow_public and host not in {h.lower() for h in allowed_hosts}:
        return False
    return is_public_host(host)
