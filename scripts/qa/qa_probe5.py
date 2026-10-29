"""探针 5：用户端写路径（收藏/学习/通知/资料）与后台写路径的 500 扫描。"""

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


def chk(label, resp, expect=(200,)):
    ok = resp.status_code in expect
    rec("功能-写路径", label, ok, f"HTTP {resp.status_code} {resp.text[:110]}")


chk("添加收藏", c.post("/api/v1/users/favorites", json={"target_type": "tutorial", "target_id": 11}, headers=user))
r = c.get("/api/v1/users/favorites?page=1&page_size=10", headers=user)
fid = None
if r.status_code == 200:
    items = (r.json().get("data") or {}).get("items") or []
    if items:
        fid = items[0].get("id")
rec("功能-写路径", "收藏列表可读", r.status_code == 200, f"HTTP {r.status_code} 条数={len((r.json().get('data') or {}).get('items') or []) if r.status_code==200 else '-'}")
if fid:
    chk("取消收藏", c.delete(f"/api/v1/users/favorites/{fid}", headers=user))

chk("重复添加收藏（幂等）", c.post("/api/v1/users/favorites", json={"target_type": "tutorial", "target_id": 11}, headers=user))
r = c.get("/api/v1/users/favorites?page=1&page_size=10", headers=user)
if r.status_code == 200:
    items = (r.json().get("data") or {}).get("items") or []
    if items:
        c.delete(f"/api/v1/users/favorites/{items[0].get('id')}", headers=user)

chk("收藏不存在的教程", c.post("/api/v1/users/favorites", json={"target_type": "tutorial", "target_id": 99999999}, headers=user),
    expect=(400, 404, 422))
chk("非法收藏类型", c.post("/api/v1/users/favorites", json={"target_type": "hack", "target_id": 11}, headers=user),
    expect=(400, 422))

chk("上报学习时长", c.post("/api/v1/tutorials/11/track", json={"duration": 10}, headers=user))
chk("超范围学习时长", c.post("/api/v1/tutorials/11/track", json={"duration": 999}, headers=user), expect=(422,))
chk("学习记录可读", c.get("/api/v1/users/learning?page=1&page_size=5", headers=user))

chk("读取通知设置", c.get("/api/v1/users/notification-settings", headers=user))
chk("更新通知设置", c.put("/api/v1/users/notification-settings",
                       json={"email_notify": False, "system_notify": True}, headers=user))

nick = "QA测试昵称"
chk("更新个人资料", c.put("/api/v1/users/profile", json={"nickname": nick, "bio": "QA 自动化测试"}, headers=user))
r = c.get("/api/v1/users/profile", headers=user)
rec("功能-写路径", "资料更新后回显一致", r.status_code == 200 and nick in r.text, f"HTTP {r.status_code}")
chk("还原个人资料", c.put("/api/v1/users/profile", json={"nickname": "test", "bio": ""}, headers=user))

chk("修改密码-旧密码错误", c.put("/api/v1/users/password",
                             json={"old_password": "wrong-old", "new_password": "NewPass123!"}, headers=user),
    expect=(400, 401, 403))
chk("修改密码-新密码过弱", c.put("/api/v1/users/password",
                            json={"old_password": "x", "new_password": "123"}, headers=user),
    expect=(400, 422))

chk("用户统计", c.get("/api/v1/users/stats", headers=user))
chk("用户等级", c.get("/api/v1/users/level", headers=user))
chk("我的举报列表", c.get("/api/v1/reports/mine?page=1&page_size=5", headers=user))
chk("消息列表", c.get("/api/v1/users/messages?page=1&page_size=5", headers=user), expect=(200, 404))

print("\n===== 后台写路径 =====")
chk("后台举报列表", c.get("/api/v1/admin/reports?page=1&page_size=5", headers=admin))
chk("后台用户列表", c.get("/api/v1/admin/users?page=1&page_size=5", headers=admin))
chk("后台审计日志", c.get("/api/v1/admin/audit-logs?page=1&page_size=5", headers=admin))
chk("后台系统状态", c.get("/api/v1/admin/system-status", headers=admin))
chk("后台定时任务", c.get("/api/v1/admin/scheduled-jobs", headers=admin))
chk("后台数据看板", c.get("/api/v1/admin/dashboard", headers=admin))
chk("后台插件列表", c.get("/api/v1/admin/plugins?page=1&page_size=5", headers=admin))
chk("后台兑换码列表", c.get("/api/v1/admin/redeem-codes?page=1&page_size=5", headers=admin), expect=(200, 404))
chk("后台部门树", c.get("/api/v1/admin/departments", headers=admin), expect=(200, 404))

print("\n" + "=" * 60)
p = sum(1 for x in R if x["ok"])
print(f"总计 {len(R)}，通过 {p}，失败 {len(R)-p}")
for f in [x for x in R if not x["ok"]]:
    print(f"  ✗ [{f['group']}] {f['name']} :: {f['detail']}")
