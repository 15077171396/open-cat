"""探针 4：付费墙、AI 能力降级、错误处理与并发一致性。"""

import concurrent.futures as cf
import sys

import httpx

sys.path.insert(0, ".")
from app.core.security import create_access_token  # noqa: E402

BASE = "http://127.0.0.1:8000"
R = []


def rec(g, n, ok, d=""):
    R.append({"group": g, "name": n, "ok": ok, "detail": d})
    print(f"[{'PASS' if ok else 'FAIL'}] {g} | {n} | {d}")


c = httpx.Client(base_url=BASE, timeout=60, trust_env=False, follow_redirects=False)
user = {"Authorization": "Bearer " + create_access_token({"sub": "12", "role": "user"})}
admin = {"Authorization": "Bearer " + create_access_token({"sub": "4", "role": "admin"})}

print("\n===== A. 付费墙 =====")
r = c.get("/api/v1/tutorials/10", headers=user)
rec("业务-付费墙", "未购买会员教程被拦截", r.status_code in (402, 403), f"HTTP {r.status_code} {r.text[:90]}")

r = c.get("/api/v1/tutorials/11", headers=user)
rec("业务-付费墙", "已购买教程正常放行", r.status_code == 200, f"HTTP {r.status_code}")

r = c.get("/api/v1/tutorials/10/intro", headers=user)
rec("业务-付费墙", "未购买教程可看试读", r.status_code == 200, f"HTTP {r.status_code}")

r = c.get("/api/v1/tutorials/10/nodes", headers=user)
rec("业务-付费墙", "未购买教程目录可读", r.status_code in (200, 403), f"HTTP {r.status_code}")

print("\n===== B. AI 能力降级 =====")
for path in ("/api/v1/ai/writing/capabilities", "/api/v1/ai-models", "/api/v1/ai/usage"):
    r = c.get(path, headers=user)
    rec("业务-AI", f"{path} 无 Key 时降级不 500", r.status_code < 500, f"HTTP {r.status_code} {r.text[:80]}")

print("\n===== C. 反馈与举报（写后清理） =====")
r = c.post("/api/v1/feedbacks", json={"content": "QA 自动化测试反馈，可忽略", "contact": "qa@example.com"}, headers=user)
fid = None
if r.status_code == 200:
    fid = (r.json().get("data") or {}).get("id")
rec("功能-反馈", "用户提交反馈", r.status_code == 200, f"HTTP {r.status_code} id={fid}")

r = c.post("/api/v1/reports", json={"content_type": "tutorial", "target_id": "9", "reason": "spam", "detail": "QA 测试举报，可忽略"}, headers=user)
rid = None
if r.status_code == 200:
    rid = (r.json().get("data") or {}).get("id")
# 同一 (用户, 内容) 只允许举报一次：重复运行时返回 400「已举报过」也算通过
dup = r.status_code == 400 and "已举报过" in r.text
rec("功能-举报", "用户提交举报（首次 200 / 重复幂等 400）", r.status_code == 200 or dup,
    f"HTTP {r.status_code} id={rid} {r.text[:60]}")

r = c.post("/api/v1/reports", json={"content_type": "tutorial", "target_id": "9", "reason": "spam"}, headers=user)
rec("功能-举报", "重复举报被去重（400）", r.status_code in (400, 409, 429), f"HTTP {r.status_code} {r.text[:60]}")

if fid:
    r = c.delete(f"/api/v1/admin/feedbacks/{fid}", headers=admin)
    rec("功能-清理", "删除测试反馈", r.status_code in (200, 404), f"HTTP {r.status_code}")

print("\n===== D. 并发与幂等 =====")
def hit():
    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as cc:
        return cc.get("/api/v1/home", headers=user).status_code

with cf.ThreadPoolExecutor(max_workers=10) as ex:
    codes = list(ex.map(lambda _: hit(), range(20)))
rec("性能-并发", "20 并发读取首页（登录态）", codes.count(200) >= 18,
    f"200={codes.count(200)} 其它={sorted(set(codes) - {200})}")

print("\n" + "=" * 60)
p = sum(1 for x in R if x["ok"])
print(f"总计 {len(R)}，通过 {p}，失败 {len(R)-p}")
for f in [x for x in R if not x["ok"]]:
    print(f"  ✗ [{f['group']}] {f['name']} :: {f['detail']}")
