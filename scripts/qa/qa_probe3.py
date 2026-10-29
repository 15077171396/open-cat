"""黑盒测试探针 v3：上传、评论 XSS、支付回调、限流响应头等剩余项。"""

import json
import sys
import time

import httpx

sys.path.insert(0, ".")
from app.core.security import create_access_token  # noqa: E402

BASE = "http://127.0.0.1:8000"
R = []


def rec(g, n, ok, d=""):
    R.append({"group": g, "name": n, "ok": ok, "detail": d})
    print(f"[{'PASS' if ok else 'FAIL'}] {g} | {n} | {d}")


c = httpx.Client(base_url=BASE, timeout=60, trust_env=False, follow_redirects=False)
admin = {"Authorization": "Bearer " + create_access_token({"sub": "4", "role": "admin"})}
user = {"Authorization": "Bearer " + create_access_token({"sub": "12", "role": "user"})}

# 游客限流按 IP 计 120 次/分钟（固定窗口）：与上一个探针连续跑会把桶打满，
# 表现为整轮 429。等到下一个窗口起点再跑，整轮都落在同一个干净窗口里。
time.sleep(60 - int(time.time()) % 60 + 1)

print("\n===== A. 列表结构与分页 =====")
r = c.get("/api/v1/tutorials?page=1&page_size=3", headers=user)
body = r.json()
data = body.get("data")
rec("功能-列表", "教程列表返回分页结构", isinstance(data, dict) and "total" in data,
    f"type={type(data).__name__} keys={list(data.keys()) if isinstance(data, dict) else 'n/a'} total={data.get('total') if isinstance(data, dict) else ''}")
items = data.get("items") or data.get("list") if isinstance(data, dict) else []
rec("功能-列表", "page_size=3 生效", len(items) <= 3, f"返回 {len(items)} 条")

r = c.get("/api/v1/tutorials?page=9999&page_size=10", headers=user)
# page 有 le=200 上限：超出即 422（严格校验），关键是不能 500
rec("功能-边界", "超界页被拒且非 500", r.status_code in (200, 422), f"HTTP {r.status_code}")

r = c.get("/api/v1/tutorials?page=1&page_size=abc", headers=user)
rec("功能-边界", "非法 page_size 返回 422", r.status_code == 422, f"HTTP {r.status_code}")

r = c.get("/api/v1/tutorials?page=0", headers=user)
rec("功能-边界", "page=0 被拒（ge=1）", r.status_code == 422, f"HTTP {r.status_code}")

print("\n===== B. 上传接口（管理员通道） =====")
r = c.post("/api/v1/uploads/image", files={"file": ("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png")})
rec("安全-鉴权", "匿名图片上传被拒", r.status_code in (401, 403), f"HTTP {r.status_code} {r.text[:50]}")

r = c.post("/api/v1/uploads/image", files={"file": ("evil.svg", b"<svg onload=alert(1)>", "image/svg+xml")}, headers=admin)
rec("安全-上传", "SVG 上传被拒（XSS 载体）", r.status_code in (400, 403, 422), f"HTTP {r.status_code} {r.text[:60]}")

r = c.post("/api/v1/uploads/image",
           files={"file": ("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (8 * 1024 * 1024), "image/png")}, headers=admin)
rec("安全-上传", "8MB 图片被大小限制拦截", r.status_code in (400, 413, 422), f"HTTP {r.status_code} {r.text[:60]}")

r = c.post("/api/v1/uploads/image", files={"file": ("fake.png", b"<?php system($_GET[1]); ?>", "image/png")}, headers=admin)
rec("安全-上传", "伪装 PNG 的脚本内容被嗅探拒绝", r.status_code in (400, 422), f"HTTP {r.status_code} {r.text[:60]}")

r = c.post("/api/v1/uploads/file", files={"file": ("x.exe", b"MZ\x90\x00", "application/octet-stream")}, headers=admin)
rec("安全-上传", "可执行文件上传被拒", r.status_code in (400, 403, 422), f"HTTP {r.status_code} {r.text[:60]}")

print("\n===== C. 评论 XSS 与内容安全 =====")
xss = '<script>alert("qa")</script><img src=x onerror=alert(1)> 测试评论'
r = c.post("/api/v1/comments", json={"target_type": "tutorial", "target_id": 11, "content": xss}, headers=user)
cid = None
if r.status_code == 200:
    cid = (r.json().get("data") or {}).get("id")
rec("安全-内容", "发布含脚本的评论", r.status_code in (200, 400),
    f"HTTP {r.status_code} {r.text[:90]}")

r = c.get("/api/v1/comments?target_type=tutorial&target_id=11&page=1&page_size=20")
txt = r.text
rec("安全-XSS", "评论列表不返回未经转义的脚本标签", "<script>alert" not in txt, f"HTTP {r.status_code}")
if "<script>alert" in txt:
    print("     实际返回片段:", txt[:400])

if cid:
    r = c.delete(f"/api/v1/comments/{cid}", headers=user)
    rec("功能-清理", "作者删除自己的评论", r.status_code == 200, f"HTTP {r.status_code}")

r = c.post("/api/v1/comments", json={"target_type": "tutorial", "target_id": 11, "content": "a" * 5000}, headers=user)
rec("功能-参数校验", "超长评论被长度上限拦截", r.status_code in (400, 422), f"HTTP {r.status_code} {r.text[:80]}")

r = c.post("/api/v1/comments", json={"target_type": "not-exist", "target_id": 1, "content": "hi"}, headers=user)
rec("功能-参数校验", "非法 target_type 被拒", r.status_code in (400, 422), f"HTTP {r.status_code} {r.text[:80]}")

print("\n===== D. 支付回调与订单 =====")
r = c.post("/api/v1/payments/notify/wechat", content=b"<xml><out_trade_no>fake</out_trade_no></xml>",
           headers={"Content-Type": "text/xml"})
rec("安全-支付", "微信回调无签名被拒", r.status_code in (400, 401, 403, 422) or "FAIL" in r.text.upper(),
    f"HTTP {r.status_code} {r.text[:80]}")

r = c.post("/api/v1/payments/notify/alipay", content=b"out_trade_no=fake&trade_status=TRADE_SUCCESS",
           headers={"Content-Type": "application/x-www-form-urlencoded"})
rec("安全-支付", "支付宝回调无签名被拒", r.status_code in (400, 401, 403, 422) or "fail" in r.text.lower(),
    f"HTTP {r.status_code} {r.text[:80]}")

r = c.get("/api/v1/payments/orders", headers=user)
rec("功能-支付", "用户订单列表可读", r.status_code == 200, f"HTTP {r.status_code}")

r = c.post("/api/v1/payments/orders/QA-FAKE-ORDER/mock-pay", headers=user)
rec("安全-支付", "不存在的订单 mock-pay 被拒", r.status_code in (400, 403, 404), f"HTTP {r.status_code} {r.text[:60]}")

print("\n===== E. 后台越权细节 =====")
for path, label in [
    ("/api/v1/admin/users/4", "读管理员详情"),
    ("/api/v1/admin/users/4/restore", "恢复用户"),
    ("/api/v1/admin/rbac/roles", "角色列表"),
]:
    r = c.get(path, headers=user)
    rec("安全-越权", f"普通用户 {label}", r.status_code in (403, 405), f"HTTP {r.status_code}")

r = c.get("/api/v1/admin/users?keyword=%27%20OR%201%3D1--", headers=admin)
rec("安全-注入", "后台用户搜索 SQL 注入无异常", r.status_code in (200, 422), f"HTTP {r.status_code}")

r = c.put("/api/v1/users/profile", json={"nickname": "x" * 500}, headers=user)
rec("功能-参数校验", "超长昵称被拒", r.status_code in (400, 422), f"HTTP {r.status_code}")

print("\n===== F. 响应头与错误约定 =====")
r = c.get("/api/v1/nope")
rec("功能-错误约定", "404 带 X-Request-Id", r.headers.get("x-request-id") is not None,
    f"x-request-id={r.headers.get('x-request-id')}")
rec("安全-响应头", "404 带 nosniff（后端直连兜底）", r.headers.get("x-content-type-options") == "nosniff",
    f"X-Content-Type-Options={r.headers.get('x-content-type-options')}")

r = c.post("/api/v1/auth/login", json={"account": "x", "password": "y"})
rec("功能-错误约定", "4xx 响应的 code 与 HTTP 状态一致", r.json().get("code") == r.status_code,
    f"HTTP {r.status_code} code={r.json().get('code')}")

r = c.get("/api/v1/users/profile")
rec("功能-错误约定", "缺少认证返回 401 且信息中性", r.status_code == 401 and "缺少认证信息" in r.text,
    f"HTTP {r.status_code} {r.text[:60]}")

print("\n" + "=" * 60)
p = sum(1 for x in R if x["ok"])
print(f"总计 {len(R)}，通过 {p}，失败 {len(R)-p}")
for f in [x for x in R if not x["ok"]]:
    print(f"  ✗ [{f['group']}] {f['name']} :: {f['detail']}")
json.dump({"total": len(R), "passed": p, "results": R},
          open("../logs/qa/qa_probe3_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
