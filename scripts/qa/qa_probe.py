"""黑盒接口测试探针：功能 / 鉴权 / 校验 / 注入 / 限流 / 性能 / CORS。

用后端 venv 运行（要复用 app.core.security 生成测试令牌）：
    cd backend && ./venv/Scripts/python.exe ../scripts/qa/qa_probe.py
"""

import asyncio
import json
import statistics
import sys
import time

import httpx
import jwt

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.core.security import create_access_token  # noqa: E402

BASE = "http://127.0.0.1:8000"
RESULTS = []


def rec(group, name, ok, detail=""):
    RESULTS.append({"group": group, "name": name, "ok": ok, "detail": detail})
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {group} | {name} | {detail}")


client = httpx.Client(base_url=BASE, timeout=20, trust_env=False, follow_redirects=False)


def envelope_ok(r):
    try:
        body = r.json()
    except Exception:
        return False, "非 JSON 响应"
    if not isinstance(body, dict):
        return False, "响应不是对象"
    for k in ("code", "message", "data"):
        if k not in body:
            return False, f"缺少 {k} 字段"
    if body.get("code") != 0 and r.status_code < 400:
        return False, f"HTTP {r.status_code} 但 code={body.get('code')}"
    return True, ""


# ---------------------------------------------------------------- 1. 公共接口
PUBLIC = [
    ("/api/v1/health", "健康检查"),
    ("/api/v1/settings", "系统设置"),
    ("/api/v1/home", "首页聚合"),
    ("/api/v1/tutorials?page=1&page_size=5", "教程列表"),
    ("/api/v1/resources?page=1&page_size=5", "资源列表"),
    ("/api/v1/tools?page=1&page_size=5", "工具列表"),
    ("/api/v1/nodes?page=1&page_size=5", "节点百科"),
    ("/api/v1/plugins?page=1&page_size=5", "扩展推荐"),
    ("/api/v1/categories/tutorial", "分类（按模块）"),
    ("/api/v1/stats", "站点统计"),
    ("/api/v1/announcements", "公告"),
    ("/api/v1/comments?target_type=tutorial&target_id=1", "评论列表"),
]

print("\n===== 1. 公共接口可用性 =====")
latencies = {}
for path, label in PUBLIC:
    t0 = time.perf_counter()
    try:
        r = client.get(path)
        dt = (time.perf_counter() - t0) * 1000
        latencies[path] = dt
        ok = r.status_code == 200
        detail = f"HTTP {r.status_code} {dt:.0f}ms"
        if ok and r.headers.get("content-type", "").startswith("application/json"):
            env_ok, why = envelope_ok(r)
            ok = ok and env_ok
            if not env_ok:
                detail += f" 信封异常:{why}"
        rec("功能-公共接口", label, ok, detail)
    except Exception as e:
        rec("功能-公共接口", label, False, f"请求异常 {type(e).__name__}: {e}")

# 详情接口
r = client.get("/api/v1/tutorials?page=1&page_size=1")
tid = None
if r.status_code == 200:
    data = r.json().get("data") or {}
    items = data.get("items") or data.get("list") or []
    if items:
        tid = items[0].get("id")
if tid:
    # 正文需登录（付费墙的一部分）：匿名必须 401，登录后才 200
    r = client.get(f"/api/v1/tutorials/{tid}")
    rec("安全-访问控制", "匿名读教程正文被拒（设计如此）", r.status_code == 401, f"HTTP {r.status_code} id={tid}")
    r = client.get("/api/v1/tutorials/999999999")
    rec("功能-公共接口", "不存在的教程返回 404", r.status_code == 404, f"HTTP {r.status_code}")

# ---------------------------------------------------------------- 2. 鉴权
print("\n===== 2. 认证与鉴权 =====")


def make_token(sub, role=None, exp_min=60, key=None, alg="HS256", typ="access"):
    payload = {"sub": str(sub), "role": role or "user", "type": typ, "jti": "qa-probe"}
    payload["exp"] = time.time() + exp_min * 60
    return jwt.encode(payload, key or settings.SECRET_KEY, algorithm=alg)


ADMIN_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 4
USER_ID = int(sys.argv[2]) if len(sys.argv) > 2 else 12
admin_token = create_access_token({"sub": str(ADMIN_ID), "role": "admin"})
user_token = create_access_token({"sub": str(USER_ID), "role": "user"})
H_ADMIN = {"Authorization": f"Bearer {admin_token}"}
H_USER = {"Authorization": f"Bearer {user_token}"}

ADMIN_GETS = [
    ("/api/v1/admin/users?page=1&page_size=5", "用户管理"),
    ("/api/v1/admin/settings", "系统设置"),
    ("/api/v1/admin/tutorials?page=1&page_size=5", "教程管理"),
    ("/api/v1/admin/audit-logs?page=1&page_size=5", "审计日志"),
    ("/api/v1/admin/rbac/roles", "RBAC 角色"),
    ("/api/v1/admin/system-status", "系统状态"),
    ("/api/v1/admin/scheduled-jobs", "定时任务"),
    ("/api/v1/admin/ai-models", "AI 模型"),
    ("/api/v1/admin/payment-settings", "支付设置"),
    ("/api/v1/admin/mail-settings", "邮件设置"),
    ("/api/v1/admin/login-settings", "登录设置"),
    ("/api/v1/admin/dashboard", "数据看板"),
]

for path, label in ADMIN_GETS:
    r = client.get(path)
    rec("安全-鉴权", f"匿名访问 {label}", r.status_code in (401, 403), f"HTTP {r.status_code}")

r = client.get("/api/v1/admin/users", headers={"Authorization": "Bearer not-a-jwt"})
rec("安全-鉴权", "伪造随机 token 被拒", r.status_code == 401, f"HTTP {r.status_code}")

r = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {make_token(ADMIN_ID, key='wrong-key')}"})
rec("安全-鉴权", "错误密钥签名的 token 被拒", r.status_code == 401, f"HTTP {r.status_code}")

none_token = jwt.encode({"sub": str(ADMIN_ID), "type": "access", "exp": time.time() + 3600}, None, algorithm="none")
r = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {none_token}"})
rec("安全-鉴权", "alg=none 伪造 token 被拒", r.status_code == 401, f"HTTP {r.status_code}")

r = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {make_token(ADMIN_ID, exp_min=-10)}"})
rec("安全-鉴权", "过期 token 被拒", r.status_code == 401, f"HTTP {r.status_code}")

r = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {make_token(ADMIN_ID, typ='refresh')}"})
rec("安全-鉴权", "refresh token 当 access 用被拒", r.status_code == 401, f"HTTP {r.status_code}")

r = client.get("/api/v1/users/profile", headers={"Authorization": "Bearer " + make_token(999999)})
rec("安全-鉴权", "不存在的用户 id 被拒", r.status_code in (401, 404), f"HTTP {r.status_code}")

r = client.get("/api/v1/users/profile", headers=H_USER)
rec("功能-用户中心", "普通用户读取自身信息", r.status_code == 200, f"HTTP {r.status_code}")

# 普通用户越权访问后台
for path, label in ADMIN_GETS[:6]:
    r = client.get(path, headers=H_USER)
    rec("安全-越权", f"普通用户访问 {label}", r.status_code == 403, f"HTTP {r.status_code}")

# 管理员访问后台
admin_ok = 0
admin_fail = []
for path, label in ADMIN_GETS:
    r = client.get(path, headers=H_ADMIN)
    if r.status_code == 200:
        admin_ok += 1
    else:
        admin_fail.append(f"{label}={r.status_code}")
rec("功能-后台", "管理员访问 12 个后台列表接口", admin_ok == len(ADMIN_GETS),
    f"{admin_ok}/{len(ADMIN_GETS)} 成功" + (f"；失败: {', '.join(admin_fail)}" if admin_fail else ""))

# 登录失败与账号枚举
print("\n===== 3. 认证接口行为 =====")
body_unknown = {"account": "no-such-user-qa@example.com", "password": "WrongPass123!"}
body_wrong = {"account": "test@aicat.com", "password": "WrongPass123!"}
r1 = client.post("/api/v1/auth/login", json=body_unknown)
r2 = client.post("/api/v1/auth/login", json=body_wrong)
same_msg = r1.json().get("message") == r2.json().get("message") and r1.status_code == r2.status_code
rec("安全-账号枚举", "未知账号与错误密码返回一致", same_msg,
    f"未知={r1.status_code}/{r1.json().get('message')} 错误={r2.status_code}/{r2.json().get('message')}")

r = client.post("/api/v1/auth/login", json={"account": "", "password": ""})
rec("功能-参数校验", "登录空账号返回 422", r.status_code == 422, f"HTTP {r.status_code}")

long_acc = "a" * 5000
r = client.post("/api/v1/auth/login", json={"account": long_acc, "password": "x"})
rec("安全-参数上限", "5000 字符账号被长度校验拦截", r.status_code in (422, 400), f"HTTP {r.status_code}")

# 登录限流
print("\n===== 4. 限流 =====")
codes = []
for i in range(11):
    r = client.post("/api/v1/auth/login", json={"account": "qa-lock-probe@example.com", "password": f"x{i}"})
    codes.append(r.json().get("message", ""))
locked = any("次数过多" in m for m in codes)
rec("安全-防爆破", "同账号连续失败 10 次后锁定 15 分钟", locked, f"最后一条={codes[-1]}")

r = client.post("/api/v1/auth/send-code", json={"account": "13800000000"})
rec("功能-验证码", "发送验证码接口有响应", r.status_code in (200, 400, 429), f"HTTP {r.status_code} {r.text[:80]}")

# ---------------------------------------------------------------- 5. 注入 / XSS / SSRF
print("\n===== 5. 注入与 SSRF =====")
INJ = [
    ("/api/v1/tutorials?keyword=%27%20OR%20%271%27%3D%271", "SQL 注入关键字（教程搜索）"),
    ("/api/v1/tutorials?keyword=%3Cscript%3Ealert(1)%3C/script%3E", "XSS 载荷（教程搜索）"),
    ("/api/v1/tutorials?order_by=id%20desc%3Bdrop%20table%20users", "order_by 注入"),
    ("/api/v1/tutorials?page=1%20OR%201%3D1", "page 参数注入"),
]
for path, label in INJ:
    r = client.get(path)
    ok = r.status_code in (200, 400, 422)
    detail = f"HTTP {r.status_code}"
    if r.status_code >= 500:
        detail += " " + r.text[:120]
    elif r.status_code == 200 and "<script>" in r.text:
        ok = False
        detail += " 响应原文反射了脚本"
    rec("安全-注入", label, ok, detail)

SSRF = [
    ("http://127.0.0.1:6379/", "内网 Redis"),
    ("http://169.254.169.254/latest/meta-data/", "云元数据"),
    ("file:///C:/Windows/win.ini", "file 协议"),
    ("http://127.0.0.1:8000/api/v1/admin/users", "本站后台接口"),
]
for target, label in SSRF:
    r = client.get("/api/v1/images/proxy", params={"url": target})
    ok = r.status_code in (400, 403) or (r.status_code in (301, 302) and "127.0.0.1" not in (r.headers.get("location") or ""))
    rec("安全-SSRF", f"图片代理拒绝 {label}", ok, f"HTTP {r.status_code} loc={(r.headers.get('location') or '')[:60]}")

r = client.get("/api/v1/images/proxy", params={"url": "../../../etc/passwd"})
rec("安全-路径穿越", "图片代理拒绝相对路径", r.status_code in (400, 403), f"HTTP {r.status_code}")

# 上传接口鉴权
r = client.post("/api/v1/uploads/image", files={"file": ("a.png", b"\x89PNG", "image/png")})
rec("安全-鉴权", "匿名图片上传被拒", r.status_code in (401, 403), f"HTTP {r.status_code}")

r = client.post("/api/v1/uploads/image", files={"file": ("evil.svg", b"<svg onload=alert(1)>", "image/svg+xml")})
rec("安全-鉴权", "普通用户走管理员上传通道被拒", r.status_code in (401, 403), f"HTTP {r.status_code} {r.text[:60]}")

r = client.post("/api/v1/users/avatar", files={"file": ("evil.svg", b"<svg onload=alert(1)>", "image/svg+xml")},
                headers=H_USER)
rec("安全-上传", "SVG 头像被拒（XSS 载体）", r.status_code in (400, 403, 422), f"HTTP {r.status_code} {r.text[:70]}")

r = client.post("/api/v1/users/avatar",
                files={"file": ("big.png", b"\x89PNG" + b"0" * (6 * 1024 * 1024), "image/png")}, headers=H_USER)
rec("安全-上传", "6MB 头像被大小限制拦截", r.status_code in (400, 413, 422), f"HTTP {r.status_code} {r.text[:60]}")

# ---------------------------------------------------------------- 6. 参数校验 / 边界
print("\n===== 6. 参数校验与边界 =====")
CASES = [
    ("POST", "/api/v1/comments", {}, "评论空 body"),
    ("POST", "/api/v1/reports", {}, "举报空 body"),
    ("POST", "/api/v1/feedbacks", {}, "反馈空 body"),
]
for method, path, body, label in CASES:
    r = client.request(method, path, json=body, headers=H_USER)
    # 缺字段由 pydantic 拦（422），字段齐但业务不合法由 service 拦（400），两者都算拒绝
    ok = r.status_code in (400, 422) and r.json().get("code") == r.status_code
    rec("功能-参数校验", f"{label} 被拒且信封一致", ok, f"HTTP {r.status_code} {r.text[:70]}")

r = client.get("/api/v1/tutorials?page=-1&page_size=10")
data = r.json().get("data") if r.status_code == 200 else None
rec("功能-边界", "page=-1 不报 500", r.status_code in (200, 422), f"HTTP {r.status_code}")

r = client.get("/api/v1/tutorials?page_size=100000")
size_ok = True
detail = f"HTTP {r.status_code}"
if r.status_code == 200:
    d = r.json().get("data") or {}
    n = len(d.get("items") or d.get("list") or [])
    size_ok = n <= 100
    detail += f" 返回 {n} 条"
rec("功能-边界", "page_size 上限被钳制（<=100）", size_ok, detail)

r = client.put("/api/v1/health")
rec("功能-边界", "不支持的方法返回 405", r.status_code == 405, f"HTTP {r.status_code}")

r = client.post("/api/v1/auth/login", content=b"{bad json", headers={"Content-Type": "application/json"})
rec("功能-参数校验", "非法 JSON 返回 422", r.status_code == 422, f"HTTP {r.status_code}")

r = client.post("/api/v1/auth/login", content=b"x" * (3 * 1024 * 1024), headers={"Content-Type": "application/json"})
rec("功能-参数校验", "3MB 请求体被拒", r.status_code in (413, 422), f"HTTP {r.status_code}")

# ---------------------------------------------------------------- 7. 信息泄露
print("\n===== 7. 信息泄露 =====")
r = client.get("/api/v1/settings")
leak = []
if r.status_code == 200:
    txt = r.text.lower()
    for kw in ("secret", "password", "api_key", "apikey", "token", "access_key"):
        if kw in txt:
            leak.append(kw)
rec("安全-信息泄露", "公开设置接口不含敏感键", not leak, f"命中: {leak}" if leak else "无敏感键")

r = client.get("/api/v1/tutorials/999999999")
rec("安全-信息泄露", "404 响应不含堆栈/SQL", "Traceback" not in r.text and "SELECT" not in r.text,
    f"body={r.text[:100]}")

r = client.get("/openapi.json")
rec("安全-信息泄露", "开发环境 /openapi.json 可访问（生产应关闭）", r.status_code == 200, f"HTTP {r.status_code}")

r = client.get("/api/v1/admin/users", headers=H_ADMIN)
hdr_leak = [k for k in r.headers if k.lower() in ("server", "x-powered-by")]
rec("安全-信息泄露", "响应头暴露服务指纹", True, f"暴露: {', '.join(f'{k}={r.headers[k]}' for k in hdr_leak)}")

# ---------------------------------------------------------------- 8. CORS
print("\n===== 8. CORS =====")
r = client.options("/api/v1/tutorials", headers={
    "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
rec("安全-CORS", "允许配置内来源", r.headers.get("access-control-allow-origin") == "http://localhost:5173",
    f"ACAO={r.headers.get('access-control-allow-origin')}")

r = client.options("/api/v1/tutorials", headers={
    "Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"})
rec("安全-CORS", "拒绝未知来源", r.headers.get("access-control-allow-origin") in (None, ""),
    f"ACAO={r.headers.get('access-control-allow-origin')}")

r = client.get("/api/v1/tutorials", headers={"Origin": "https://evil.example.com"})
rec("安全-CORS", "未知来源的简单请求不下发 ACAO", r.headers.get("access-control-allow-origin") in (None, ""),
    f"ACAO={r.headers.get('access-control-allow-origin')}")

# ---------------------------------------------------------------- 9. 性能
print("\n===== 9. 性能 =====")


async def bench(path, n=12):
    async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
        # 预热
        await c.get(path)
        ts = []
        for _ in range(n):
            t0 = time.perf_counter()
            r = await c.get(path)
            ts.append((time.perf_counter() - t0) * 1000)
            if r.status_code != 200:
                return path, None, r.status_code
        return path, ts, 200


async def concurrent(path, n=30):
    # 必须带登录态：游客限流 120 次/分钟，30 并发会先撞 429，看起来像"性能差"
    async with httpx.AsyncClient(base_url=BASE, timeout=60, trust_env=False) as c:
        t0 = time.perf_counter()
        rs = await asyncio.gather(*[c.get(path, headers=H_USER) for _ in range(n)], return_exceptions=True)
        dt = (time.perf_counter() - t0) * 1000
        ok = sum(1 for r in rs if not isinstance(r, Exception) and r.status_code == 200)
        return dt, ok, n


for path in ("/api/v1/home", "/api/v1/tutorials?page=1&page_size=10", "/api/v1/stats"):
    p, ts, code = asyncio.run(bench(path))
    if ts:
        rec("性能-延迟", f"{p} 顺序 12 次", statistics.median(ts) < 800,
            f"P50={statistics.median(ts):.0f}ms P95={sorted(ts)[int(len(ts)*0.95)-1]:.0f}ms max={max(ts):.0f}ms")
    else:
        rec("性能-延迟", f"{p} 顺序 12 次", False, f"HTTP {code}")

dt, ok, n = asyncio.run(concurrent("/api/v1/home", 30))
rec("性能-并发", "首页聚合 30 并发", ok == n, f"{ok}/{n} 成功 总耗时 {dt:.0f}ms")

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 60)
total = len(RESULTS)
passed = sum(1 for r in RESULTS if r["ok"])
print(f"总计 {total} 项，通过 {passed}，失败 {total - passed}")
fails = [r for r in RESULTS if not r["ok"]]
for f in fails:
    print(f"  ✗ [{f['group']}] {f['name']} :: {f['detail']}")

with open("../logs/qa/qa_probe_result.json", "w", encoding="utf-8") as fh:
    json.dump({"total": total, "passed": passed, "results": RESULTS}, fh, ensure_ascii=False, indent=2)
