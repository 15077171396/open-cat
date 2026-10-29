"""P0-1 可观测性单测：错误监控脱敏 + 深度健康检查。

全部离线运行：不访问真实 MySQL / Redis / Sentry。
探测函数的「成功」「失败」两条分支都用替换依赖的方式确定性覆盖，
不依赖本机是否恰好跑着 MySQL/Redis。
"""

import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from tests.conftest import async_client


@asynccontextmanager
async def _failing_session(exc: Exception):
    """模拟 async_session() 建连即失败。"""
    raise exc
    yield  # pragma: no cover


class _ScalarResult:
    """只实现 .scalar() 的查询结果桩。"""

    def __init__(self, value=None, raise_on_scalar: bool = False):
        self._value = value
        self._raise = raise_on_scalar

    def scalar(self):
        if self._raise:
            raise RuntimeError("boom")
        return self._value


class _RowResult:
    """只实现 ``.first()`` 的查询结果桩（alembic 版本号查询用）。"""

    def __init__(self, row=None, raise_on_first: bool = False):
        self._row = row
        self._raise = raise_on_first

    def first(self):
        if self._raise:
            raise RuntimeError("boom")
        return self._row


class _SessionStub:
    """会话桩：按调用顺序返回 —— 1) ``SELECT 1``  2) 会话时区  3) alembic 版本。

    顺序敏感是刻意的——若被测代码调整了这三条查询的先后，桩会返回错位的结果，
    用例会以「utc 为 None」或「schema_revision 为 None」暴露出来。
    """

    def __init__(self, tz_result: _ScalarResult | None, rev_result: _RowResult | None = None):
        self._tz_result = tz_result
        self._rev_result = rev_result
        self.calls = 0

    async def execute(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return None  # SELECT 1 的结果不被使用
        if self.calls == 2:
            return self._tz_result
        return self._rev_result


def _session_factory(tz_result, rev_result=None):
    @asynccontextmanager
    async def factory():
        yield _SessionStub(tz_result, rev_result)

    return factory


class ShallowHealthTests(unittest.IsolatedAsyncioTestCase):
    """/health 必须与外部依赖解耦。"""

    async def test_health_ok(self):
        async with async_client(include_auth=False) as client:
            resp = await client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], {"status": "ok"})

    async def test_shallow_health_ignores_broken_dependencies(self):
        """MySQL/Redis 全挂时浅检查仍返回 200（否则编排器会无谓重启容器）。"""
        with patch("app.services.health_service.probe_mysql", side_effect=AssertionError("不应被调用")), \
             patch("app.services.health_service.probe_redis", side_effect=AssertionError("不应被调用")):
            async with async_client(include_auth=False) as client:
                resp = await client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 200)


class ProbeUnitTests(unittest.IsolatedAsyncioTestCase):
    """直接测真实探测函数（不经 HTTP 层），确保失败分支不泄露连接信息。"""

    async def test_probe_mysql_ok(self):
        from app.services import health_service

        class _Session:
            async def execute(self, *a, **kw):
                return None

        @asynccontextmanager
        async def _session_factory():
            yield _Session()

        with patch("app.database.async_session", _session_factory):
            result = await health_service.probe_mysql()
        self.assertTrue(result["ok"])
        self.assertIn("ms", result)

    async def test_probe_mysql_failure_hides_message(self):
        """驱动异常消息常含主机/账号/口令，只允许回传异常类名。"""
        from app.services import health_service

        class OperationalError(Exception):
            pass

        leaky = OperationalError(
            "Can't connect to MySQL server on '127.0.0.1:3306' (user=root, password=hunter2)"
        )
        with patch("app.database.async_session", lambda: _failing_session(leaky)):
            result = await health_service.probe_mysql()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "OperationalError")
        self.assertNotIn("hunter2", str(result))
        self.assertNotIn("3306", str(result))

    # ---------- 会话时区校验（台账 P2-7 / 代码审查 D5） ----------
    # 全库 naive 时间戳按 UTC 写入是代码级约定（统计日界 / VIP 到期 / 免费时长倒计时
    # 都直接拿 naive 值当 UTC 比较）。会话时区非 UTC 时 func.now() 会写本地时间，
    # 与应用写的 UTC 混用 → **不报错但算错** 小时级时间差，必须让它可见。

    async def test_probe_mysql_ok_when_session_is_utc(self):
        from app.services import health_service

        with patch("app.database.async_session", _session_factory(_ScalarResult(0))):
            result = await health_service.probe_mysql()
        self.assertTrue(result["ok"])
        self.assertTrue(result["utc"])
        self.assertEqual(result["tz_offset_seconds"], 0)

    async def test_probe_mysql_flags_non_utc_session(self):
        """非 UTC 会话必须判失败并给出可读原因（否则时间会静默算错）"""
        from app.services import health_service

        with patch("app.database.async_session", _session_factory(_ScalarResult(28800))):
            result = await health_service.probe_mysql()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "MySQLSessionNotUTC")
        self.assertFalse(result["utc"])
        self.assertEqual(result["tz_offset_seconds"], 28800)

    async def test_probe_mysql_unknown_timezone_does_not_fail(self):
        """读不到时区（驱动/权限差异）只标「未知」，不能反过来把连通性判成失败"""
        from app.services import health_service

        with patch(
            "app.database.async_session",
            _session_factory(_ScalarResult(raise_on_scalar=True)),
        ):
            result = await health_service.probe_mysql()
        self.assertTrue(result["ok"])
        self.assertIsNone(result["utc"])
        self.assertNotIn("error", result)

    async def test_probe_redis_ok_and_failure(self):
        from app.services import health_service

        class _OkRedis:
            async def ping(self):
                return True

        class _BadRedis:
            async def ping(self):
                raise ConnectionError("redis://:secretpw@127.0.0.1:6379/0 refused")

        with patch.object(health_service, "redis_client", _OkRedis()):
            ok = await health_service.probe_redis()
        self.assertTrue(ok["ok"])

        with patch.object(health_service, "redis_client", _BadRedis()):
            bad = await health_service.probe_redis()
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"], "ConnectionError")
        self.assertNotIn("secretpw", str(bad))


class DeepHealthTests(unittest.IsolatedAsyncioTestCase):
    """/health/deep 状态码语义与缓存。"""

    async def test_deep_ok_when_dependencies_up(self):
        ok = {"ok": True, "ms": 1.0}
        with patch("app.services.health_service.probe_mysql", return_value=ok), \
             patch("app.services.health_service.probe_redis", return_value=ok):
            async with async_client(include_auth=False) as client:
                resp = await client.get("/api/v1/health/deep")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["checks"]["mysql"]["ok"])
        self.assertTrue(data["checks"]["redis"]["ok"])

    async def test_deep_degraded_when_dependencies_down(self):
        down = {"ok": False, "error": "OperationalError", "ms": 1.0}
        with patch("app.services.health_service.probe_mysql", return_value=down), \
             patch("app.services.health_service.probe_redis", return_value=down):
            async with async_client(include_auth=False) as client:
                resp = await client.get("/api/v1/health/deep")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["data"]["status"], "degraded")

    async def test_deep_degraded_when_only_one_dependency_down(self):
        """任一依赖故障即 degraded（不能因为 Redis 挂了就认为服务健康）。"""
        with patch("app.services.health_service.probe_mysql", return_value={"ok": True, "ms": 1.0}), \
             patch("app.services.health_service.probe_redis",
                   return_value={"ok": False, "error": "ConnectionError", "ms": 1.0}):
            async with async_client(include_auth=False) as client:
                resp = await client.get("/api/v1/health/deep")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["data"]["status"], "degraded")

    async def test_deep_result_is_cached(self):
        """TTL 内二次请求命中缓存，不再实际探测（吸收探活高频请求）。"""
        calls = {"mysql": 0, "redis": 0}

        async def counting_mysql():
            calls["mysql"] += 1
            return {"ok": True, "ms": 1.0}

        async def counting_redis():
            calls["redis"] += 1
            return {"ok": True, "ms": 1.0}

        with patch("app.services.health_service.probe_mysql", side_effect=counting_mysql), \
             patch("app.services.health_service.probe_redis", side_effect=counting_redis):
            async with async_client(include_auth=False) as client:
                first = await client.get("/api/v1/health/deep")
                second = await client.get("/api/v1/health/deep")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(calls, {"mysql": 1, "redis": 1})

    async def test_deep_survives_redis_cache_outage(self):
        """缓存读写失败不得影响探测结果：Redis 挂掉时仍要能正确报 degraded。"""
        from fastapi import Response

        from app.api import router as api_router
        from app.services import health_service

        class _BrokenRedis:
            async def get(self, key):
                raise ConnectionError("down")

            async def set(self, *a, **kw):
                raise ConnectionError("down")

            async def ping(self):
                raise ConnectionError("down")

        down = {"ok": False, "error": "ConnectionError", "ms": 1.0}
        with patch.object(health_service, "redis_client", _BrokenRedis()), \
             patch("app.services.health_service.probe_mysql", return_value=down), \
             patch("app.services.health_service.probe_redis", return_value=down):
            response = Response()
            payload = await api_router.health_deep_check(response)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(payload["data"]["status"], "degraded")

    async def test_deep_response_shape(self):
        ok = {"ok": True, "ms": 1.0}
        with patch("app.services.health_service.probe_mysql", return_value=ok), \
             patch("app.services.health_service.probe_redis", return_value=ok):
            async with async_client(include_auth=False) as client:
                resp = await client.get("/api/v1/health/deep")
        body = resp.json()
        self.assertEqual(body["code"], 0)
        self.assertIsInstance(body["data"]["ts"], int)


class ScrubTests(unittest.TestCase):
    """上报前脱敏：本站接口大量传递口令/验证码/令牌，必须擦除。"""

    def setUp(self):
        from app.core import monitoring

        self.monitoring = monitoring

    def test_scrub_redacts_sensitive_keys_recursively(self):
        payload = {
            "account": "tester@example.com",
            "password": "plain-text-password",
            "nested": {"refresh_token": "abc.def.ghi", "code": "123456"},
            "items": [{"api_key": "sk-xxx"}, {"name": "keep-me"}],
        }
        scrubbed = self.monitoring._scrub(payload)
        self.assertEqual(scrubbed["account"], "tester@example.com")
        self.assertEqual(scrubbed["password"], "***")
        self.assertEqual(scrubbed["nested"]["refresh_token"], "***")
        self.assertEqual(scrubbed["nested"]["code"], "***")
        self.assertEqual(scrubbed["items"][0]["api_key"], "***")
        self.assertEqual(scrubbed["items"][1]["name"], "keep-me")

    def test_scrub_stops_at_depth_limit(self):
        """超深结构截断而非递归爆栈。"""
        node = {"password": "deep-secret"}
        for _ in range(20):
            node = {"child": node}
        result = self.monitoring._scrub(node)
        self.assertIsInstance(result, dict)

    def test_before_send_drops_query_string_and_cookies(self):
        event = {
            "request": {
                "query_string": "?email=a@b.com&password=hunter2",
                "cookies": {"access_token": "jwt-here"},
                "data": {"password": "hunter2", "account": "a@b.com"},
                "headers": {"Authorization": "Bearer jwt-here", "X-Real-IP": "1.2.3.4"},
            },
            "user": {"id": 123, "email": "a@b.com", "ip_address": "1.2.3.4"},
            "extra": {"user_password": "hunter2"},
        }
        result = self.monitoring._before_send(event, {})
        self.assertNotIn("query_string", result["request"])
        self.assertNotIn("cookies", result["request"])
        self.assertEqual(result["request"]["data"]["password"], "***")
        self.assertEqual(result["request"]["data"]["account"], "a@b.com")
        self.assertEqual(result["request"]["headers"]["Authorization"], "***")
        # 只保留用户 ID，去掉邮箱与 IP
        self.assertEqual(result["user"], {"id": 123})
        self.assertEqual(result["extra"]["user_password"], "***")

    def test_scrub_text_redacts_credentials_in_free_text(self):
        """日志正文里的凭据必须被正则兜住（结构化脱敏只认字段名）。"""
        cases = [
            ("登录失败 password=hunter2", "hunter2"),
            ("token: eyJhbGciOi.JWT.SECRET", "eyJhbGciOi.JWT.SECRET"),
            ('{"account":"a@b.com","password":"hunter2"}', "hunter2"),
            ("refresh_token=abc.def.ghi, next", "abc.def.ghi"),
            ("api_key=sk-live-12345", "sk-live-12345"),
        ]
        for text, secret in cases:
            scrubbed = self.monitoring._scrub_text(text)
            self.assertNotIn(secret, scrubbed, f"未擦除: {text}")

    def test_scrub_text_keeps_ordinary_text(self):
        """普通文本不应被破坏（否则事件失去排查价值）。"""
        text = "用户 12345 在 2026-10-08 扣减积分 30 失败"
        self.assertEqual(self.monitoring._scrub_text(text), text)

    def test_before_send_scrubs_logentry_message(self):
        """loguru logger.error("...password=x") 走的是 logentry 路径。"""
        event = {
            "message": "登录失败 password=hunter2",
            "logentry": {"message": "重置失败 token=abc.def.ghi", "formatted": "重置失败 token=abc.def.ghi"},
        }
        result = self.monitoring._before_send(event, {})
        self.assertNotIn("hunter2", result["message"])
        self.assertNotIn("abc.def.ghi", result["logentry"]["message"])
        self.assertNotIn("abc.def.ghi", result["logentry"]["formatted"])

    def test_before_send_scrubs_exception_and_breadcrumbs(self):
        event = {
            "exception": {"values": [{"type": "ValueError", "value": "登录失败 password=hunter2"}]},
            "breadcrumbs": {"values": [{"message": "调用 AI 失败 token=abc.def.ghi"}]},
        }
        result = self.monitoring._before_send(event, {})
        self.assertNotIn("hunter2", result["exception"]["values"][0]["value"])
        self.assertNotIn("abc.def.ghi", result["breadcrumbs"]["values"][0]["message"])

    def test_before_send_scrubs_frame_local_variables(self):
        """堆栈帧的局部变量会带出函数参数里的明文口令，必须擦除。"""
        event = {
            "exception": {
                "values": [
                    {
                        "type": "ValueError",
                        "value": "登录失败",
                        "stacktrace": {
                            "frames": [
                                {
                                    "vars": {
                                        "account": "a@b.com",
                                        "password": "hunter2",
                                        "self": "<UserService>",
                                    },
                                    "context_line": "async def login(account, password):",
                                }
                            ]
                        },
                    }
                ]
            },
            "threads": {
                "values": [
                    {
                        "stacktrace": {
                            "frames": [{"vars": {"refresh_token": "abc.def.ghi"}}]
                        }
                    }
                ]
            },
        }
        result = self.monitoring._before_send(event, {})
        frame = result["exception"]["values"][0]["stacktrace"]["frames"][0]
        self.assertEqual(frame["vars"]["password"], "***")
        self.assertEqual(frame["vars"]["account"], "a@b.com")
        self.assertEqual(frame["vars"]["self"], "<UserService>")
        thread_vars = result["threads"]["values"][0]["stacktrace"]["frames"][0]["vars"]
        self.assertEqual(thread_vars["refresh_token"], "***")

    def test_before_send_scrubs_source_context(self):
        """出错行的源码若写死了凭据，不能连同代码一起上报。"""
        event = {
            "stacktrace": {
                "frames": [
                    {
                        "context_line": '    client.post(url, json={"password": "hunter2"})',
                        "pre_context": ["def f():", "    token = 'abc.def.ghi'"],
                        "post_context": ["    return True"],
                    }
                ]
            }
        }
        result = self.monitoring._before_send(event, {})
        frame = result["stacktrace"]["frames"][0]
        self.assertNotIn("hunter2", frame["context_line"])
        self.assertNotIn("abc.def.ghi", frame["pre_context"][1])
        self.assertEqual(frame["post_context"], ["    return True"])

    def test_before_send_tolerates_missing_request(self):
        result = self.monitoring._before_send({"event_id": "x"}, {})
        self.assertEqual(result["event_id"], "x")


class MonitoringGateTests(unittest.TestCase):
    """未配置 DSN 时不得启用监控（零网络请求、零开销）。"""

    def setUp(self):
        from app.core import monitoring

        self.monitoring = monitoring
        self._saved = (monitoring._initialized, monitoring._enabled)
        monitoring._initialized = False
        monitoring._enabled = False

    def tearDown(self):
        self.monitoring._initialized, self.monitoring._enabled = self._saved

    def test_disabled_without_dsn(self):
        with patch.object(self.monitoring.settings, "SENTRY_DSN", ""):
            self.assertFalse(self.monitoring.init_monitoring())
        self.assertFalse(self.monitoring.is_monitoring_enabled())

    def test_whitespace_dsn_treated_as_unset(self):
        with patch.object(self.monitoring.settings, "SENTRY_DSN", "   "):
            self.assertFalse(self.monitoring.init_monitoring())

    def test_init_is_idempotent(self):
        with patch.object(self.monitoring.settings, "SENTRY_DSN", ""):
            self.monitoring.init_monitoring()
            self.monitoring.init_monitoring()
        self.assertTrue(self.monitoring._initialized)


class SchemaRevisionTests(unittest.IsolatedAsyncioTestCase):
    """库版本 == 代码 head 的校验（P2-14）。

    「库落后于代码」是一种**只在部分接口上暴露**的静默失败：应用能启动、/health 也 ok，
    但凡是 select 到新列的接口都 500（Unknown column），表现为「某几个页面加载失败」。
    生产靠 compose 的 migrate 服务先跑 alembic；本机开发则完全可能忘了跑。
    """

    async def asyncSetUp(self):
        from app.services import health_service

        self.health_service = health_service
        self.head = health_service._code_head_revision()
        # 守卫：若代码侧读不到 head，本类所有用例都会退化成「未知」而失去意义
        self.assertIsNotNone(self.head, "读不到代码侧 head，校验形同虚设")

    async def test_matching_revision_is_ok(self):
        with patch(
            "app.database.async_session",
            _session_factory(_ScalarResult(0), _RowResult((self.head,))),
        ):
            result = await self.health_service.probe_mysql()
        self.assertTrue(result["ok"])
        self.assertEqual(result["schema_revision"], self.head)
        self.assertEqual(result["schema_head"], self.head)

    async def test_outdated_schema_is_flagged(self):
        with patch(
            "app.database.async_session",
            _session_factory(_ScalarResult(0), _RowResult(("seed_comment_permissions",))),
        ):
            result = await self.health_service.probe_mysql()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "SchemaOutOfDate")
        self.assertEqual(result["schema_revision"], "seed_comment_permissions")

    async def test_unreadable_revision_is_unknown_not_failure(self):
        """读不到版本号（表不存在 / 权限不足）只标「未知」，不把连通性探测打成故障。"""
        with patch(
            "app.database.async_session",
            _session_factory(_ScalarResult(0), _RowResult(raise_on_first=True)),
        ):
            result = await self.health_service.probe_mysql()
        self.assertTrue(result["ok"])
        self.assertIsNone(result["schema_revision"])

    async def test_both_problems_reported_tz_first(self):
        """两项附加校验同时失败时，error 仍取第一个（保持既有单字段契约）。"""
        with patch(
            "app.database.async_session",
            _session_factory(_ScalarResult(28800), _RowResult(("old_rev",))),
        ):
            result = await self.health_service.probe_mysql()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "MySQLSessionNotUTC")
        self.assertEqual(result["schema_revision"], "old_rev")

    def test_code_head_is_resolved_from_alembic_scripts(self):
        """head 必须能从 alembic 配置解析出来（路径写错就会永远「未知」）。

        经 alembic.ini 推导（P2-21）：断言配置入口存在，且 script_location
        指向真实目录 —— 任何一环断掉都只剩「未知」，检查静默失效。
        """
        from pathlib import Path

        from app.services import health_service

        ini = health_service._INI_PATH
        self.assertTrue(ini.is_file(), f"alembic.ini 不存在: {ini}")
        script_location = (ini.parent / "alembic").resolve()
        self.assertTrue(script_location.is_dir(), f"alembic 目录不存在: {script_location}")
        self.assertNotEqual(self.head, "")


if __name__ == "__main__":
    unittest.main()
