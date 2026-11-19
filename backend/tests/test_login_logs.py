"""登录日志契约测试。

钉住四条**破了不报错、只会悄悄失去意义**的契约：

1. **写日志绝不影响登录主流程**：record 内部吞掉一切异常（degrade_guard 收口）；
   若日志写入失败让登录 500，安全遥测就变成了可用性炸弹。
2. **账号先脱敏再入库/再查询**：原始账号明文落库会放大泄露面；查询按脱敏后
   精确匹配（输入先脱敏再比对，否则永远查不到）。
3. **动态值一律通过参数字典传入**：翻页、筛选、删除的动态部分不允许走其他途径；
   本测试按「调用顺序 + 参数内容」钉住行为。
4. **登录成功/失败/登出三态都留痕**：失败时取业务异常的 message（用户可见的
   失败原因），登出走 logout() 的同一条 record 通道。
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from app.core.exceptions import AppException, AuthenticationError
from app.models.user import User
from app.services import login_log_service as lls
from app.services import auth_service
from app.utils.verification import mask_account


def _run(coro):
    return asyncio.run(coro)


def _result(value=None, rowcount=0, rows=None):
    """构造 execute 返回值：scalar() / rowcount / all() 三种消费形态"""
    return SimpleNamespace(scalar=lambda: value, rowcount=rowcount, all=lambda: rows or [])


def _row(**kw):
    base = {
        "id": 1,
        "user_id": None,
        "account": "a***@example.com",
        "status": "failed",
        "message": "密码错误",
        "ip": "1.2.3.4",
        "user_agent": "UA",
        "request_id": "req-1",
        "created_at": datetime(2026, 11, 18, 1, 2, 3),
    }
    base.update(kw)
    return SimpleNamespace(_mapping=base)


def _fake_db(results):
    """按调用顺序返回预设结果的假数据库会话；记录每次调用的参数字典"""
    calls: list = []
    queue = list(results)

    async def _call(*args, **kwargs):
        calls.append(args[1] if len(args) > 1 else kwargs.get("params"))
        if queue:
            return queue.pop(0)
        return _result()

    return SimpleNamespace(execute=_call, commit=mock.AsyncMock()), calls


def _session_factory(session):
    """模拟 async_session 的异步上下文工厂"""

    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            return False

    return lambda: _Ctx()


# ---------------------------------------------------------------------------
# record：写入与降级
# ---------------------------------------------------------------------------


class RecordTests(unittest.TestCase):
    def test_record_inserts_masked_account_and_normalized_status(self):
        db, calls = _fake_db([_result()])
        with mock.patch.object(lls, "async_session", _session_factory(db)):
            _run(
                lls.record(
                    status="bogus",  # 非法状态 → 归一为 failed
                    user_id=42,
                    account="13812345678",
                    message="x" * 300,
                    ip="1.2.3.4",
                    user_agent="u" * 400,
                    request_id="req-abc",
                )
            )
        self.assertEqual(len(calls), 1)
        params = calls[0]
        self.assertEqual(params["account"], mask_account("13812345678"))
        self.assertEqual(params["status"], "failed")
        self.assertEqual(len(params["message"]), 255)
        self.assertEqual(len(params["user_agent"]), 255)
        self.assertEqual(params["request_id"], "req-abc")

    def test_record_failure_never_breaks_login_flow(self):
        """写日志失败（库挂了）绝不抛异常 —— 登录主流程不受影响"""
        db, _ = _fake_db([])
        db.execute = mock.AsyncMock(side_effect=RuntimeError("db down"))
        with mock.patch.object(lls, "async_session", _session_factory(db)):
            # 不应抛出
            _run(lls.record(status="failed", account="13812345678", message="密码错误"))

    def test_record_defaults_status_and_empty_account(self):
        db, calls = _fake_db([_result()])
        with mock.patch.object(lls, "async_session", _session_factory(db)):
            _run(lls.record(status="success", account="", user_id=7))
        self.assertEqual(calls[0]["status"], "success")
        self.assertEqual(calls[0]["account"], "")


# ---------------------------------------------------------------------------
# list_logs：可选筛选全部走绑定参数
# ---------------------------------------------------------------------------


class ListTests(unittest.TestCase):
    def test_list_filters_and_pagination_are_bound(self):
        # 调用顺序固定：先总数、后当前页
        db, calls = _fake_db([_result(value=3), _result(rows=[_row()])])
        data = _run(
            lls.list_logs(
                db,
                page=2,
                page_size=10,
                account="13812345678",
                status="failed",
                created_after=datetime(2026, 11, 1),
            )
        )
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["status"], "failed")
        count_params, list_params = calls[0], calls[1]
        # 账号按脱敏后精确匹配
        self.assertEqual(count_params["account"], mask_account("13812345678"))
        self.assertEqual(count_params["status"], "failed")
        self.assertIsNotNone(count_params["start_at"])
        # 翻页走绑定参数
        self.assertEqual(list_params["limit"], 10)
        self.assertEqual(list_params["offset"], 10)

    def test_list_rejects_unknown_status(self):
        db, _ = _fake_db([])
        with self.assertRaises(AppException):
            _run(lls.list_logs(db, status="hacked"))

    def test_list_unfiltered_returns_empty(self):
        db, calls = _fake_db([_result(value=0)])
        data = _run(lls.list_logs(db))
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["items"], [])
        # 无筛选时可选条件参数全部为 None（开关由参数决定）
        params = calls[0]
        for key in ("account", "user_id", "status", "start_at", "end_at"):
            self.assertIsNone(params[key])


# ---------------------------------------------------------------------------
# delete / cleanup / count
# ---------------------------------------------------------------------------


class MutationTests(unittest.TestCase):
    def test_delete_skips_invalid_ids_and_sums_rowcount(self):
        db, calls = _fake_db([_result(rowcount=2), _result(rowcount=2)])
        deleted = _run(lls.delete_logs(db, [1, "x", -2, 0, 3]))
        self.assertEqual(deleted, 4)
        # 逐条绑定参数；非法/非正数 ID 已被跳过
        self.assertEqual(calls[0]["log_id"], 1)
        self.assertEqual(calls[1]["log_id"], 3)

    def test_cleanup_deletes_only_expired(self):
        db, calls = _fake_db([_result(rowcount=9)])
        deleted = _run(lls.cleanup_expired(db, keep_days=90))
        self.assertEqual(deleted, 9)
        self.assertIsInstance(calls[0]["cutoff"], datetime)
        self.assertLess(
            calls[0]["cutoff"], datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=89)
        )

    def test_count_failed_since_counts_only_failed(self):
        db, calls = _fake_db([_result(value=5)])
        n = _run(lls.count_failed_since(db, datetime(2026, 11, 18)))
        self.assertEqual(n, 5)
        self.assertIsNotNone(calls[0]["since"])

    def test_delete_with_audit_commits_only_when_deleted(self):
        """删除 + 审计同事务提交：有删除才提交（审计不落空转行）"""
        db, _ = _fake_db([_result(rowcount=2)])
        admin = SimpleNamespace(id=1, account="root")
        with mock.patch("app.services.admin_audit_service.record_admin_action") as audit:
            n = _run(lls.delete_logs_with_audit(db, admin, [7]))
        self.assertEqual(n, 2)
        audit.assert_called_once()
        self.assertEqual(audit.call_args.args[2], "login_log_delete")
        db.commit.assert_awaited_once()

    def test_delete_with_audit_skips_commit_when_nothing_deleted(self):
        db, _ = _fake_db([_result(rowcount=0)])
        admin = SimpleNamespace(id=1, account="root")
        with mock.patch("app.services.admin_audit_service.record_admin_action") as audit:
            n = _run(lls.delete_logs_with_audit(db, admin, [7]))
        self.assertEqual(n, 0)
        audit.assert_not_called()
        db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# auth hooks：登录成功 / 失败 / 登出三态留痕
# ---------------------------------------------------------------------------


class _FakeUser(User):
    """绕过 ORM 构造的轻量用户（仅用作 login 流程的返回值）"""

    def __init__(self):
        self.id = 42
        self.account = "13812345678"
        self.role = "admin"


class AuthHookTests(unittest.TestCase):
    def _patch_chain(self, *, verify_raises=None, verify_returns=None):
        """把 login() 依赖的全部外呼都 mock 掉，只观察 login_log_service.record"""
        patchers = [
            mock.patch.object(auth_service.captcha_service, "enforce_login_captcha", new=mock.AsyncMock()),
            mock.patch.object(auth_service, "verify_account_password", new=mock.AsyncMock()),
            mock.patch.object(auth_service, "enforce_totp_if_enabled", new=mock.AsyncMock()),
            mock.patch.object(auth_service, "enforce_admin_2fa_policy", new=mock.AsyncMock()),
            mock.patch.object(
                auth_service,
                "issue_login_tokens",
                new=mock.AsyncMock(return_value={"access_token": "a", "refresh_token": "r"}),
            ),
        ]
        verify = patchers[1].new
        if verify_raises is not None:
            verify.side_effect = verify_raises
        else:
            verify.return_value = verify_returns

        class _Req:
            account = "13812345678"
            password = "pw"
            captcha_id = None
            captcha_code = None
            totp_code = None

        return patchers, _Req()

    def test_failed_login_records_log_and_reraises(self):
        patchers, req = self._patch_chain(verify_raises=AuthenticationError("账号或密码错误"))
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            with mock.patch.object(auth_service.login_log_service, "record", new=mock.AsyncMock()) as rec:
                with self.assertRaises(AuthenticationError):
                    _run(auth_service.login(req, None, client_ip="1.2.3.4", user_agent="UA"))
        rec.assert_awaited_once()
        kwargs = rec.await_args.kwargs
        self.assertEqual(kwargs["status"], "failed")
        self.assertEqual(kwargs["message"], "账号或密码错误")
        self.assertEqual(kwargs["ip"], "1.2.3.4")

    def test_successful_login_records_success(self):
        patchers, req = self._patch_chain(verify_returns=_FakeUser())
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            with mock.patch.object(auth_service.login_log_service, "record", new=mock.AsyncMock()) as rec:
                _run(auth_service.login(req, None, client_ip="1.2.3.4", user_agent="UA"))
        rec.assert_awaited_once()
        kwargs = rec.await_args.kwargs
        self.assertEqual(kwargs["status"], "success")
        self.assertEqual(kwargs["user_id"], 42)

    def test_logout_records_logout_event(self):
        with mock.patch.object(auth_service, "blacklist_access_token", new=mock.AsyncMock()):
            with mock.patch.object(auth_service, "revoke_session", new=mock.AsyncMock()):
                with mock.patch.object(auth_service.login_log_service, "record", new=mock.AsyncMock()) as rec:
                    _run(
                        auth_service.logout(
                            42,
                            access_token=None,
                            client_ip="1.2.3.4",
                            user_agent="UA",
                            account="root",
                        )
                    )
        rec.assert_awaited_once()
        kwargs = rec.await_args.kwargs
        self.assertEqual(kwargs["status"], "logout")
        self.assertEqual(kwargs["user_id"], 42)
        self.assertEqual(kwargs["account"], "root")


# ---------------------------------------------------------------------------
# API：路由契约（权限由 test_admin_route_guards 守卫，这里钉住响应形状）
# ---------------------------------------------------------------------------


class LoginLogsApiTests(unittest.TestCase):
    def test_list_endpoint_shape(self):
        from tests.conftest import async_client

        async def scenario():
            async with async_client(user=User(id=1, account="root", role="admin")) as client:
                with mock.patch.object(
                    lls,
                    "list_logs",
                    new=mock.AsyncMock(
                        return_value={
                            "items": [
                                {
                                    "id": 1,
                                    "user_id": None,
                                    "account": mask_account("13812345678"),
                                    "status": "failed",
                                    "message": "密码错误",
                                    "ip": "1.2.3.4",
                                    "user_agent": None,
                                    "request_id": None,
                                    "created_at": "2026-11-18T01:02:03",
                                }
                            ],
                            "total": 1,
                            "page": 1,
                            "page_size": 20,
                        }
                    ),
                ):
                    resp = await client.get("/api/v1/admin/login-logs")
            return resp

        resp = _run(scenario())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["total"], 1)
        self.assertEqual(body["data"]["items"][0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
