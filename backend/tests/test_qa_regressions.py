"""2026-10-29 全链路测试暴露的两个 500 的回归钉子。

背景：当时 1815 个后端用例全绿，但真实 MySQL 上 `POST /api/v1/reports` 与
`POST /api/v1/users/favorites`（教程）**100% 返回 500**（`MissingGreenlet`）：

1. 举报：`submit_report` 在 flush/commit 后直接读 `created_at`，该列是
   `server_default=func.now()`，INSERT 后处于「未加载」状态，属性访问触发一次同步 SELECT；
2. 收藏：`_get_target_snapshot` 用 `load_only(id, title, description)` 投影，却 `getattr`
   了没投影的 `cover_image`（只有教程的 `favorite_cover=True`），同样触发同步懒加载。

根因不在这两行代码，而在 **`fake_db` 把 server_default 列当成 None**（真实库是"读一次要发
SQL"）、也不模拟 `load_only` 投影之外的懒加载 —— 仿真层比真实会话宽松，bug 就隐形了。

2026-10-29 起 `tests/fake_db.py` 已按真实语义收紧：INSERT 后时间型 server_default 列在对象上
是 `_Unloaded` 哨兵（读即抛），`load_only` 没投影的列同样不可读，只有 `refresh()` 或一次
取回该列的 SELECT 才会填值。所以本文件里不再自建会话替身 —— **直接用 MemorySession
就是在测真实语义**。用 `git stash` 撤掉修复后，本文件的用例与 `test_moderation.py`
的 6 条会一起变红（已验证）。
"""

import unittest

from app.core.exceptions import AppException
from app.models.favorite import Favorite
from app.models.tutorial import Tutorial
from app.services import favorite_service as fs
from app.services import report_service as rs
from tests.fake_db import MemorySession


class ReportRefreshRegressionTest(unittest.IsolatedAsyncioTestCase):
    """BUG-01：举报提交必须在 commit 之后 refresh 再序列化。"""

    async def test_submit_report_reads_back_server_default_column(self):
        db = MemorySession()
        row = await rs.submit_report(
            db, 1, content_type="tutorial", target_id="42", reason="spam", detail="有问题"
        )
        # 未 refresh 时读到的是 _Unloaded 哨兵（读即抛），能拿到字符串就说明修复在
        self.assertIsInstance(row["created_at"], str)
        self.assertEqual(row["content_type"], "tutorial")
        self.assertEqual(row["target_id"], "42")

    async def test_submit_report_still_rejects_duplicate(self):
        """refresh 不能顺手把唯一约束的判定搞丢。"""
        db = MemorySession()
        await rs.submit_report(db, 1, content_type="tutorial", target_id="42", reason="spam")
        with self.assertRaises(AppException) as ctx:
            await rs.submit_report(db, 1, content_type="tutorial", target_id="42", reason="spam")
        self.assertEqual(ctx.exception.code, 400)

    async def test_my_reports_payload_fields_match_schema(self):
        """`/reports/mine` 的字段名必须与 `ReportItem` 对齐（曾声明 user_id/target_type
        等 service 从不产出的键，前端拿到的是一串 null）。"""
        db = MemorySession()
        await rs.submit_report(db, 7, content_type="tutorial", target_id="11", reason="spam")
        payload = await rs.list_my_reports(db, 7)
        item = payload["items"][0]
        for key in (
            "id", "reporter_id", "content_type", "content_type_label",
            "target_id", "reason", "reason_label", "status", "status_label", "created_at",
        ):
            self.assertIn(key, item)
        self.assertNotIn("user_id", item)
        self.assertNotIn("target_type", item)
        # target_id 必须是字符串：前端用 String(targetId) 相等于它来判断"已举报过"
        self.assertIsInstance(item["target_id"], str)


class FavoriteCoverProjectionTest(unittest.IsolatedAsyncioTestCase):
    """BUG-02：load_only 的投影列必须覆盖 cover_attr，否则懒加载即 500。"""

    def test_every_cover_spec_projects_its_cover_column(self):
        checked = 0
        for module, spec in fs._TARGET_SPECS.items():
            if not spec.cover_attr:
                continue
            projected = {getattr(col, "name", None) for col in spec.load_columns}
            self.assertIn(
                spec.cover_attr, projected,
                f"{module} 的快照要读 {spec.cover_attr}，但 load_only 没投影它 → 异步懒加载必炸",
            )
            checked += 1
        self.assertGreaterEqual(checked, 1, "教程必须是被检查到的封面快照类型")

    def test_tutorial_spec_projects_cover(self):
        spec = fs._TARGET_SPECS["tutorial"]
        self.assertEqual(spec.cover_attr, "cover_image")
        self.assertIn("cover_image", {getattr(c, "name", None) for c in spec.load_columns})

    async def test_add_tutorial_favorite_writes_cover_snapshot(self):
        db = MemorySession()
        db.seed(
            Tutorial(
                id=11, title="梳理工作流节点线", description="简介",
                cover_image="https://cdn.example.com/cover.png",
                is_published=True, status="published",
            )
        )
        result = await fs.add_favorite(db, _user(7), "tutorial", 11)
        self.assertEqual(result["message"], "收藏成功")
        rows = db.all_rows(Favorite)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cover"], "https://cdn.example.com/cover.png")
        self.assertEqual(rows[0]["title"], "梳理工作流节点线")

    async def test_add_favorite_is_idempotent(self):
        db = MemorySession()
        db.seed(
            Tutorial(
                id=11, title="梳理工作流节点线", description="简介",
                cover_image="https://cdn.example.com/cover.png",
                is_published=True, status="published",
            )
        )
        await fs.add_favorite(db, _user(7), "tutorial", 11)
        again = await fs.add_favorite(db, _user(7), "tutorial", 11)
        self.assertEqual(again["message"], "已收藏过")
        self.assertEqual(len(db.all_rows(Favorite)), 1)

    async def test_unpublished_tutorial_cannot_be_favorited(self):
        """封面快照顺带修的顺序里不能把"草稿不可收藏"弄丢。"""
        db = MemorySession()
        db.seed(
            Tutorial(
                id=12, title="草稿", description=None, cover_image=None,
                is_published=False, status="updating",
            )
        )
        with self.assertRaises(AppException):
            await fs.add_favorite(db, _user(7), "tutorial", 12)


def _user(user_id: int):
    from app.models.user import User

    return User(id=user_id, account=f"u{user_id}", password_hash="x", role="user")


class RegisterConflictMessageTest(unittest.IsolatedAsyncioTestCase):
    """注册撞 IntegrityError 时不能一律说「该账号已注册」。

    主键是 10 位随机数（`generate_id`），极小概率撞已有行。此时账号根本没注册过，
    却回「该账号已注册」—— 用户换任何密码都注册不上，客服也无法从这句话定位。
    修复方式：冲突后二次确认账号是否真的存在。本测试钉住那个判定函数。
    """

    async def test_account_exists_true_for_seeded_account(self):
        from app.models.user import User
        from app.services import auth_service

        db = MemorySession()
        db.seed(User(id=1, account="someone@example.com", password_hash="x", role="user"))
        self.assertTrue(await auth_service._account_exists(db, "someone@example.com"))

    async def test_account_exists_false_when_account_unknown(self):
        from app.services import auth_service

        db = MemorySession()
        self.assertFalse(await auth_service._account_exists(db, "nobody@example.com"))


class SecurityHeadersTest(unittest.TestCase):
    """BUG-07：直连后端（绕过 nginx）也必须带基础安全头，且不得抢走 RequestId 的最外层位置。"""

    def test_headers_present_on_success_and_500(self):
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient

        from app.core.security_headers import SecurityHeadersMiddleware

        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware)

        # 与生产一致：异常由应用内 handler 兜成 500 响应（未注册 handler 时 500 由
        # Starlette 的 ServerErrorMiddleware 在**用户中间件之外**生成，任何中间件都加不上头）
        @app.exception_handler(RuntimeError)
        async def _on_error(request: Request, exc: RuntimeError):
            return JSONResponse({"code": 500, "message": "服务器内部错误"}, status_code=500)

        @app.get("/_ok")
        async def _ok():
            return {"ok": True}

        @app.get("/_boom")
        async def _boom():
            raise RuntimeError("qa")

        client = TestClient(app, raise_server_exceptions=False)
        for path, code in (("/_ok", 200), ("/_boom", 500)):
            resp = client.get(path)
            self.assertEqual(resp.status_code, code)
            self.assertEqual(resp.headers.get("x-content-type-options"), "nosniff")
            self.assertEqual(resp.headers.get("x-frame-options"), "SAMEORIGIN")
            self.assertEqual(
                resp.headers.get("referrer-policy"), "strict-origin-when-cross-origin"
            )
            # CSP 由 nginx 唯一负责，应用层不写（两处不一致会让浏览器取交集）
            self.assertIsNone(resp.headers.get("content-security-policy"))

    def test_registered_and_keeps_request_id_outermost(self):
        from app.main import app

        names = [m.cls.__name__ for m in app.user_middleware]
        self.assertIn("SecurityHeadersMiddleware", names, "create_app() 未挂载安全响应头中间件")
        self.assertEqual(
            names[0], "RequestIdMiddleware", "RequestId 必须保持最外层（429/500 也要有关联 ID）"
        )
