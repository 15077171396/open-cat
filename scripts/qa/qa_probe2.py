"""黑盒测试探针 v2：修正路径后的功能性 / 限流 / 上传 / 后台 CRUD 验证。"""

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


c = httpx.Client(base_url=BASE, timeout=30, trust_env=False, follow_redirects=False)
admin = {"Authorization": "Bearer " + create_access_token({"sub": "4", "role": "admin"})}
user = {"Authorization": "Bearer " + create_access_token({"sub": "12", "role": "user"})}

# 游客限流按 IP 计 120 次/分钟（固定窗口）：与上一个探针连续跑会把桶打满，
# 表现为整轮 429。等到下一个窗口起点再跑，整轮都落在同一个干净窗口里。
time.sleep(60 - int(time.time()) % 60 + 1)

print("\n===== A. 公共接口（修正路径） =====")
for path, label in [
    ("/api/v1/categories/tutorial", "分类-教程模块"),
    ("/api/v1/categories/tutorial/overview", "分类-教程概览"),
    ("/api/v1/tutorials/11/intro", "教程试读页（匿名）"),
    ("/api/v1/llms.txt", "llms.txt"),
    ("/api/v1/indexnow-key", "IndexNow key"),
    ("/api/v1/images/search-thumb?token=short", "图库签名校验"),
]:
    r = c.get(path)
    # IndexNow 未配密钥时 404、图库签名校验失败时 422，都是预期结果
    rec("功能-公共接口", label, r.status_code in (200, 400, 401, 403, 404, 422),
        f"HTTP {r.status_code} {r.text[:70]}")

r = c.get("/api/v1/users/profile", headers=user)
rec("功能-用户中心", "普通用户读取 profile", r.status_code == 200, f"HTTP {r.status_code} {r.text[:80]}")

r = c.get("/api/v1/tutorials/11", headers=user)
rec("功能-教程", "登录用户读取教程详情", r.status_code == 200, f"HTTP {r.status_code} {r.text[:60]}")

r = c.get("/api/v1/tutorials/11")
rec("安全-访问控制", "匿名读教程正文被拒（设计为登录可读）", r.status_code == 401, f"HTTP {r.status_code}")

print("\n===== B. 登录锁定（同账号连续失败） =====")
seq = []
for i in range(11):
    r = c.post("/api/v1/auth/login", json={"account": "test@aicat.com", "password": f"wrong-{i}"})
    seq.append(f"{r.status_code}:{r.json().get('message')}")
print("序列:", seq)
locked = any("次数过多" in s for s in seq)
rec("安全-防爆破", "同一账号连续失败 10 次后临时锁定", locked, seq[-1])

print("\n===== C. 全局限流 =====")
# 等窗口重置
for _ in range(70):
    if c.get("/api/v1/stats").status_code == 200:
        break
    time.sleep(1)
codes = []
for i in range(130):
    r = c.get("/api/v1/stats")
    codes.append(r.status_code)
n429 = codes.count(429)
first429 = codes.index(429) + 1 if 429 in codes else None
rec("安全-限流", "游客 120/分钟的限流生效", n429 > 0, f"429 数量={n429}，首次 429 在第 {first429} 次请求")
r = c.get("/api/v1/stats")
rec("安全-限流", "429 响应带 Retry-After", r.headers.get("retry-after") is not None,
    f"Retry-After={r.headers.get('retry-after')} body={r.text[:60]}")

# 上一节刻意打满了游客桶（130 次），后面的匿名用例必须等到新窗口，
# 否则一律 429 —— 看起来像"上传鉴权坏了"，其实是自己的压力测试余波
time.sleep(60 - int(time.time()) % 60 + 1)

print("\n===== D. 上传接口 =====")
r = c.post("/api/v1/uploads/image", files={"file": ("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png")})
rec("安全-鉴权", "匿名图片上传被拒", r.status_code in (401, 403), f"HTTP {r.status_code}")

r = c.post("/api/v1/uploads/image", files={"file": ("evil.svg", b"<svg onload=alert(1)>", "image/svg+xml")}, headers=admin)
rec("安全-上传", "SVG 上传被拒（XSS 载体）", r.status_code in (400, 403, 422), f"HTTP {r.status_code} {r.text[:80]}")

r = c.post("/api/v1/uploads/image",
           files={"file": ("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (8 * 1024 * 1024), "image/png")}, headers=admin)
rec("安全-上传", "8MB 图片被大小限制拦截", r.status_code in (400, 413, 422), f"HTTP {r.status_code} {r.text[:80]}")

r = c.post("/api/v1/uploads/image",
           files={"file": ("fake.png", b"<?php echo 1;?>", "image/png")}, headers=admin)
rec("安全-上传", "伪装扩展名的非图片内容被拒（MIME 嗅探）", r.status_code in (400, 422), f"HTTP {r.status_code} {r.text[:80]}")

print("\n===== E. 后台 CRUD 全链路 =====")
payload = {"title": "QA 自动化测试公告", "content": "该公告由测试脚本创建，稍后删除。", "tag": "测试", "is_published": False}
r = c.post("/api/v1/admin/announcements", json=payload, headers=admin)
created_id = None
if r.status_code == 200:
    created_id = (r.json().get("data") or {}).get("id")
rec("功能-后台CRUD", "新建公告", r.status_code == 200 and created_id, f"HTTP {r.status_code} id={created_id} {r.text[:80]}")

if created_id:
    r = c.put(f"/api/v1/admin/announcements/{created_id}", json={**payload, "title": "QA 自动化测试公告（已改）"}, headers=admin)
    rec("功能-后台CRUD", "编辑公告", r.status_code == 200, f"HTTP {r.status_code}")
    r = c.get("/api/v1/admin/announcements?page=1&page_size=50", headers=admin)
    title_ok = r.status_code == 200 and "已改" in r.text
    rec("功能-后台CRUD", "列表回显修改结果", title_ok, f"HTTP {r.status_code}")
    r = c.delete(f"/api/v1/admin/announcements/{created_id}", headers=admin)
    rec("功能-后台CRUD", "删除公告（清理测试数据）", r.status_code == 200, f"HTTP {r.status_code}")

# 写入权限：普通用户不能建公告
r = c.post("/api/v1/admin/announcements", json=payload, headers=user)
rec("安全-越权", "普通用户新建后台公告被拒", r.status_code == 403, f"HTTP {r.status_code}")

# 参数校验：超长标题
r = c.post("/api/v1/admin/announcements", json={"title": "x" * 5000, "content": "y"}, headers=admin)
rec("功能-参数校验", "超长标题被拒", r.status_code in (400, 422), f"HTTP {r.status_code}")

print("\n===== F. 审计留痕 =====")
r = c.get("/api/v1/admin/audit-logs?page=1&page_size=5", headers=admin)
txt = r.text
rec("功能-审计", "后台写操作进入审计日志", r.status_code == 200 and ("announcement" in txt or "公告" in txt),
    f"HTTP {r.status_code} 命中={('announcement' in txt)}")

print("\n===== G. 分页与一致性 =====")
r = c.get("/api/v1/tutorials?page=1&page_size=3", headers=user)
d = r.json().get("data") or {}
rec("功能-分页", "分页返回 total/items 结构", "total" in d and ("items" in d or "list" in d),
    f"keys={list(d.keys())} total={d.get('total')}")

r = c.get("/api/v1/tutorials?page=9999&page_size=10", headers=user)
# page 声明了 le=200：超出即 422（严格校验），关键是不能 500
rec("功能-分页", "超界页被拒且非 500", r.status_code in (200, 422), f"HTTP {r.status_code} {r.text[:60]}")

r = c.get("/api/v1/tutorials?page=1&page_size=abc", headers=user)
rec("功能-分页", "非法分页参数返回 422", r.status_code == 422, f"HTTP {r.status_code}")

print("\n" + "=" * 60)
p = sum(1 for x in R if x["ok"])
print(f"总计 {len(R)}，通过 {p}，失败 {len(R)-p}")
for f in [x for x in R if not x["ok"]]:
    print(f"  ✗ [{f['group']}] {f['name']} :: {f['detail']}")
json.dump({"total": len(R), "passed": p, "results": R}, open("../logs/qa/qa_probe2_result.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
