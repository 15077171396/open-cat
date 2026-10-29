"""探针 6：带容错的用户写路径全量扫描（收藏多类型 / 学习 / 通知 / 资料 / 密码）。"""

import sys

import httpx

sys.path.insert(0, ".")
from app.core.security import create_access_token  # noqa: E402

BASE = "http://127.0.0.1:8000"
R = []
c = httpx.Client(base_url=BASE, timeout=30, trust_env=False, follow_redirects=False)
user = {"Authorization": "Bearer " + create_access_token({"sub": "12", "role": "user"})}
admin = {"Authorization": "Bearer " + create_access_token({"sub": "4", "role": "admin"})}


def call(label, method, path, expect=(200,), json_body=None, headers=None):
    try:
        r = c.request(method, path, json=json_body, headers=headers or user)
        ok = r.status_code in expect
        detail = f"HTTP {r.status_code} {r.text[:100]}"
    except Exception as e:
        ok, detail = False, f"连接异常 {type(e).__name__}: {str(e)[:80]}"
        r = None
    R.append({"group": "功能-用户写路径", "name": label, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {label} | {detail}")
    return r


# 收藏：教程（有封面快照）与其他类型
r = call("收藏-教程", "POST", "/api/v1/users/favorites", json_body={"target_type": "tutorial", "target_id": 11})
for tt, tid in (("resource", 1), ("plugin", 1), ("node", 1), ("tool", 1)):
    rr = call(f"收藏-{tt}", "POST", "/api/v1/users/favorites", json_body={"target_type": tt, "target_id": tid},
              expect=(200, 400, 404))
    if rr is not None and rr.status_code == 200:
        fid = (rr.json().get("data") or {}).get("id")
        if fid:
            call(f"取消收藏-{tt}", "DELETE", f"/api/v1/users/favorites/{fid}")

call("收藏列表", "GET", "/api/v1/users/favorites?page=1&page_size=10")
call("收藏非法类型", "POST", "/api/v1/users/favorites", json_body={"target_type": "hack", "target_id": 1},
     expect=(400, 422))
call("收藏不存在内容", "POST", "/api/v1/users/favorites", json_body={"target_type": "tutorial", "target_id": 99999999},
     expect=(400, 404))

# 学习
call("上报学习时长", "POST", "/api/v1/tutorials/11/track", json_body={"duration": 10})
call("学习时长越界", "POST", "/api/v1/tutorials/11/track", json_body={"duration": 999}, expect=(422,))
call("学习记录列表", "GET", "/api/v1/users/learning?page=1&page_size=5")
call("继续学习", "GET", "/api/v1/users/learning/continue")
call("学习统计", "GET", "/api/v1/users/learning/stats")

# 通知设置
call("读取通知设置", "GET", "/api/v1/users/notification-settings")
call("更新通知设置", "PUT", "/api/v1/users/notification-settings",
     json_body={"email_notify": False, "system_notify": True})

# 资料
nick = "QA测试昵称"
r = call("更新个人资料", "PUT", "/api/v1/users/profile", json_body={"nickname": nick, "bio": "QA 自动化测试"})
r2 = call("读取资料", "GET", "/api/v1/users/profile")
if r2 is not None and r2.status_code == 200:
    ok = nick in r2.text
    R.append({"group": "功能-用户写路径", "name": "资料回显一致", "ok": ok, "detail": f"含新昵称={ok}"})
    print(f"[{'PASS' if ok else 'FAIL'}] 资料回显一致 | 含新昵称={ok}")
call("还原个人资料", "PUT", "/api/v1/users/profile", json_body={"nickname": "test", "bio": ""})
call("超长昵称", "PUT", "/api/v1/users/profile", json_body={"nickname": "x" * 500}, expect=(400, 422))

# 密码
call("改密-旧密码错误", "PUT", "/api/v1/users/password",
     json_body={"old_password": "wrong-old", "new_password": "NewPass123!"}, expect=(400, 401, 403, 429))
call("改密-新密码过弱", "PUT", "/api/v1/users/password",
     json_body={"old_password": "x", "new_password": "123"}, expect=(400, 422))

# 其他读
call("用户统计", "GET", "/api/v1/users/stats")
call("用户等级", "GET", "/api/v1/users/level")
call("我的举报", "GET", "/api/v1/reports/mine?page=1&page_size=5")
call("消息列表", "GET", "/api/v1/users/messages?page=1&page_size=5", expect=(200, 404))
call("下载记录", "GET", "/api/v1/users/downloads?page=1&page_size=5", expect=(200, 404))

print("\n===== 后台读接口 =====")
for path, label in [
    ("/api/v1/admin/reports?page=1&page_size=5", "举报管理"),
    ("/api/v1/admin/users?page=1&page_size=5", "用户管理"),
    ("/api/v1/admin/audit-logs?page=1&page_size=5", "审计日志"),
    ("/api/v1/admin/system-status", "系统状态"),
    ("/api/v1/admin/scheduled-jobs", "定时任务"),
    ("/api/v1/admin/dashboard", "数据看板"),
    ("/api/v1/admin/plugins?page=1&page_size=5", "插件管理"),
    ("/api/v1/admin/resources?page=1&page_size=5", "资源管理"),
    ("/api/v1/admin/comments?page=1&page_size=5", "评论管理"),
    ("/api/v1/admin/moderation/queue?page=1&page_size=5", "审核队列"),
    ("/api/v1/admin/messages?page=1&page_size=5", "站内信管理"),
    ("/api/v1/admin/redeem-codes?page=1&page_size=5", "兑换码"),
    ("/api/v1/admin/departments", "部门"),
]:
    try:
        r = c.get(path, headers=admin)
        ok = r.status_code in (200, 404)
        detail = f"HTTP {r.status_code} {r.text[:70]}"
    except Exception as e:
        ok, detail = False, f"异常 {type(e).__name__}"
    R.append({"group": "功能-后台读", "name": label, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {label} | {detail}")

print("\n" + "=" * 60)
p = sum(1 for x in R if x["ok"])
print(f"总计 {len(R)}，通过 {p}，失败 {len(R)-p}")
for f in [x for x in R if not x["ok"]]:
    print(f"  ✗ [{f['group']}] {f['name']} :: {f['detail']}")
