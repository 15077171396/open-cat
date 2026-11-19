"""性能基线脚本：对**运行中的服务**压测关键端点，输出 P50/P95/P99，超预算退出码 1。

2026-10-29 对标文档定下的验收线（本地 MySQL、10 万行量级）：
- 列表 < 200ms、详情 < 150ms、写入 < 300ms（--budgets 可覆盖）
- 用法（后台先跑起来，再执行）：

    backend/venv/Scripts/python scripts/bench.py \
        --base http://127.0.0.1:8001 \
        --account admin@example.com --password <管理员密码> \
        --samples 50 --concurrency 4

    # 或直接给 token（浏览器登录后从请求头复制 access token）：
    backend/venv/Scripts/python scripts/bench.py --base http://127.0.0.1:8001 --token <access_token>

- 登录路径说明：管理员后台若开启了图形验证码，脚本登录会被拒 —— 此时用 --token。
- 输出为表；--json 时输出机器可读结果（CI/脚本消费）。任何端点超预算 → 退出码 1。
- 端点集刻意精简（2 个列表 + 1 个详情 + 1 个看板）：它们分别代表「筛选 + 分页 +
  关联回填」「单表大结果」「聚合」三类成本，扩列请保持每类至少一个代表。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

import httpx

# (名称, 方法, 路径, 预算档位)。路径不含 base。
DEFAULT_ENDPOINTS = [
    ("users_list", "GET", "/api/v1/admin/users?page=1&page_size=20", "list"),
    ("tutorials_list", "GET", "/api/v1/admin/tutorials?page=1&page_size=20", "list"),
    ("tutorial_detail", "GET", "/api/v1/admin/tutorials/1", "detail"),
    ("dashboard", "GET", "/api/v1/admin/dashboard", "list"),
]

BUDGET_MS = {"list": 200.0, "detail": 150.0, "write": 300.0}


def _percentiles(samples: list[float]) -> dict:
    """P50/P95/P99（线性插值近似）+ 最大值，样本不足 2 条时 P 档退化。"""
    data = sorted(samples)
    n = len(data)

    def _pct(p: float) -> float:
        if n == 0:
            return 0.0
        if n == 1:
            return data[0]
        idx = min(n - 1, max(0, round(p / 100 * (n - 1))))
        return data[idx]

    return {
        "count": n,
        "p50": round(_pct(50), 1),
        "p95": round(_pct(95), 1),
        "p99": round(_pct(99), 1),
        "max": round(data[-1] if data else 0.0, 1),
        "errors": 0,
    }


async def _login(base: str, account: str, password: str) -> str:
    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        resp = await client.post("/api/v1/auth/login", json={"account": account, "password": password})
        resp.raise_for_status()
        envelope = resp.json()
        data = envelope.get("data") or {}
        token = data.get("access_token") or ""
        if not token:
            raise SystemExit("登录成功但未返回 access_token —— 检查账号是否为管理员")
        return token


async def _run_endpoint(client: httpx.AsyncClient, name: str, method: str, path: str, samples: int, concurrency: int) -> dict:
    sem = asyncio.Semaphore(concurrency)
    timings: list[float] = []
    errors: list[str] = []

    async def _one():
        async with sem:
            t0 = time.perf_counter()
            try:
                resp = await client.request(method, path)
                if resp.status_code >= 400:
                    errors.append(f"{resp.status_code} {path}")
            except Exception as exc:  # noqa: BLE001 网络类异常统一记账
                errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                timings.append((time.perf_counter() - t0) * 1000)

    await asyncio.gather(*(_one() for _ in range(samples)))
    result = _percentiles(timings)
    result["errors"] = len(errors)
    if errors:
        result["first_error"] = errors[0]
    result["name"] = name
    result["path"] = path
    return result


async def run(args) -> int:
    token = args.token
    if not token:
        if not (args.account and args.password):
            raise SystemExit("需要 --token 或 --account + --password")
        token = await _login(args.base, args.account, args.password)

    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=args.base, timeout=30, headers=headers) as client:
        rows = []
        for name, method, path, budget_key in DEFAULT_ENDPOINTS:
            result = await _run_endpoint(client, name, method, path, args.samples, args.concurrency)
            budget = BUDGET_MS.get(budget_key, 200.0)
            result["budget"] = budget
            result["ok"] = result["errors"] == 0 and result["p95"] <= budget
            rows.append(result)

    failed = [r for r in rows if not r["ok"]]
    if args.json:
        import json

        print(json.dumps({"rows": rows, "passed": not failed}, ensure_ascii=False, indent=2))
    else:
        print(f"{'端点':<18}{'count':>6}{'P50':>9}{'P95':>9}{'P99':>9}{'max':>9}{'err':>5}  {'预算':>7}  结论")
        for r in rows:
            verdict = "✅" if r["ok"] else "❌"
            print(
                f"{r['name']:<18}{r['count']:>6}{r['p50']:>9}{r['p95']:>9}{r['p99']:>9}{r['max']:>9}{r['errors']:>5}  {r['budget']:>6.0f}ms  {verdict}"
            )
        if any("first_error" in r for r in rows):
            for r in rows:
                if "first_error" in r:
                    print(f"首个错误（{r['name']}）: {r['first_error']}")

    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="open-cat 性能基线（对运行中的服务压测关键端点）")
    parser.add_argument("--base", default="http://127.0.0.1:8001", help="服务基址，默认本机 8001")
    parser.add_argument("--token", default="", help="管理员 access token（开启验证码时用这个）")
    parser.add_argument("--account", default="", help="管理员账号（与 --password 搭配登录）")
    parser.add_argument("--password", default="", help="管理员密码")
    parser.add_argument("--samples", type=int, default=30, help="每端点请求数，默认 30")
    parser.add_argument("--concurrency", type=int, default=4, help="并发度，默认 4")
    parser.add_argument("--json", action="store_true", help="输出 JSON（CI/脚本消费）")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
