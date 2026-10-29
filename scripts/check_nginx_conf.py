#!/usr/bin/env python3
"""nginx.conf 的零依赖静态自检（第五轮审查 OPS-08）。

为什么需要它：CI 里只做了 `docker compose config -q`（校验 compose 插值）和镜像构建，
**从不校验 nginx.conf 本身**。而 nginx 的配置错误一律表现为「容器起不来 → 全站 502」，
且只有部署那一刻才暴露。本脚本把最容易犯、又能纯静态发现的几类错误提前拦下：

1. **括号不平衡** —— 少一个 `}` 整份配置失效；
2. **location 重复** —— 同一修饰符 + 同一匹配串写两次，nginx 启动直接报
   `duplicate location`；
3. **proxy_pass 指向未定义的 upstream** —— 拼错一个字母就是 502（`nginx -t` 能查，
   但它需要 nginx 二进制与可解析的主机名，CI 里不好用）；
4. **server 块缺 listen**；
5. **`add_header` 与 `expires` 并用在同一 location** —— 会产生两条 Cache-Control
   （本项目已踩过两次，见 nginx.conf 里的注释）。

刻意不做：不做完整语法解析（那是 `nginx -t` 的活），只覆盖「改了配置但没法验证」的高频坑。
退出码 1 表示有错误；WARN 不阻塞。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONF = REPO / "nginx.conf"

_LOCATION_RE = re.compile(r"^\s*location\s+(?:(=|\^~|~|~\*)\s*)?(\S+)\s*\{")
_UPSTREAM_RE = re.compile(r"^\s*upstream\s+([\w.-]+)\s*\{")
_PROXY_PASS_RE = re.compile(r"^\s*proxy_pass\s+https?://([\w.-]+)")
_SERVER_RE = re.compile(r"^\s*server\s*\{")
_LISTEN_RE = re.compile(r"^\s*listen\s+")


def strip_comments(text: str) -> str:
    """去掉行注释（保留引号内的 #，本配置里没有，但别留坑）"""
    out = []
    for line in text.splitlines():
        in_quote = False
        quote = ""
        buf = []
        for ch in line:
            if in_quote:
                buf.append(ch)
                if ch == quote:
                    in_quote = False
                continue
            if ch in "\"'":
                in_quote = True
                quote = ch
                buf.append(ch)
                continue
            if ch == "#":
                break
            buf.append(ch)
        out.append("".join(buf))
    return "\n".join(out)


def _server_block_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """返回每个 server 块的 (起始行号, 结束行号)，行号从 1 开始、闭区间"""
    ranges: list[tuple[int, int]] = []
    depth = 0
    start = 0
    start_depth = 0
    for idx, line in enumerate(lines, 1):
        if _SERVER_RE.match(line):
            start, start_depth = idx, depth
        depth += line.count("{") - line.count("}")
        if start and depth <= start_depth:
            ranges.append((start, idx))
            start = 0
    return ranges


def _enclosing_server(line_no: int, ranges: list[tuple[int, int]]) -> int:
    """行号所属 server 块的下标；不在任何 server 块内返回 -1"""
    for i, (lo, hi) in enumerate(ranges):
        if lo <= line_no <= hi:
            return i
    return -1


def check() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not CONF.exists():
        return [f"找不到 {CONF}"], []

    raw = CONF.read_text(encoding="utf-8")
    code = strip_comments(raw)
    lines = code.splitlines()

    # 1) 括号平衡
    opens, closes = code.count("{"), code.count("}")
    if opens != closes:
        errors.append(f"花括号不平衡：{{ 有 {opens} 个，}} 有 {closes} 个")

    # 2) location 重复 —— **按 server 块分别判重**：不同 server 里出现同名 location 是合法的
    #    （本项目 www 与 tj. 两个 server 都有 `location /`）
    server_ranges = _server_block_ranges(lines)
    seen: dict[tuple[int, tuple[str, str]], int] = {}
    for idx, line in enumerate(lines, 1):
        m = _LOCATION_RE.match(line)
        if not m:
            continue
        server_no = _enclosing_server(idx, server_ranges)
        sig = (server_no, ((m.group(1) or "").strip(), m.group(2)))
        if sig in seen:
            errors.append(
                f"location 重复（nginx 启动会报 duplicate location）—— "
                f"第 {seen[sig]} 行与第 {idx} 行都在第 {server_no + 1} 个 server 块里："
                f"location {sig[1][0]} {sig[1][1]}"
            )
        else:
            seen[sig] = idx

    # 3) proxy_pass 的 upstream 必须已定义
    upstreams = {m.group(1) for line in lines if (m := _UPSTREAM_RE.match(line))}
    for idx, line in enumerate(lines, 1):
        m = _PROXY_PASS_RE.match(line)
        if not m:
            continue
        host = m.group(1)
        # 带点的视为域名（直接反代外部），其余必须是已定义的 upstream
        if "." in host or host in ("localhost",) or host.startswith("$"):
            continue
        if host not in upstreams:
            errors.append(
                f"第 {idx} 行 proxy_pass 指向未定义的 upstream「{host}」"
                f"（已定义：{sorted(upstreams) or '无'}）"
            )

    # 4) server 块必须有 listen（粗略：按行扫描，遇到 server { 后向后找 listen）
    for idx, line in enumerate(lines):
        if not _SERVER_RE.match(line):
            continue
        block = "\n".join(lines[idx: idx + 12])
        if not any(_LISTEN_RE.match(x) for x in block.splitlines()):
            warnings.append(f"第 {idx + 1} 行的 server 块前 12 行内没看到 listen")

    # 5) 同一 location 内 expires 与 add_header Cache-Control 并用
    block: list[str] = []
    depth = 0
    block_start = 0
    for idx, line in enumerate(lines, 1):
        if _LOCATION_RE.match(line):
            block, depth, block_start = [line], 1, idx
            continue
        if block:
            depth += line.count("{") - line.count("}")
            block.append(line)
            if depth <= 0:
                text = "\n".join(block)
                if re.search(r"^\s*expires\s+", text, re.M) and re.search(
                    r"add_header\s+Cache-Control", text, re.I
                ):
                    errors.append(
                        f"第 {block_start} 行的 location 同时用了 expires 与 add_header Cache-Control"
                        "（会产生两条 Cache-Control，只认首条的客户端拿不到 must-revalidate）"
                    )
                block = []

    return errors, warnings


def main() -> int:
    errors, warnings = check()
    print(f"nginx.conf 自检：{CONF.name}（{len(errors)} 个错误 / {len(warnings)} 个警告）")
    for w in warnings:
        print(f"  ⚠️  {w}")
    for e in errors:
        print(f"  ✗ {e}")
    if errors:
        print("\n❌ nginx 配置自检未通过")
        return 1
    print("\n✅ nginx 配置自检通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
