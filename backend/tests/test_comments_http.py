"""评论两条路由的**端到端**契约（第五轮审查 R5-20）。

为什么单独有这一个文件：泛化 90% 的断言原先都打在 service 与纯函数上，
`/comments` 与 `/admin/comments` **一条 HTTP 用例都没有**（实测
`grep -rn "api/v1/comments" backend/tests/*.py` 零命中）。这正是第四轮报告
第七节第 1 条与 `docs/05-报告/README.md` 第五节第 5 条目名点过的教训 ——
P0-12（`/resources/hot` 每次调用必 500）能穿过 633 条用例，就是因为
「只断言 service 返回值」测不到路由层的默认值、参数绑定与 `response_model`。

本轮已经用真实请求实测出三处只在路由层才成立的行为，全部钉在这里：
1. `target_type` 带默认值 → **省略它的请求不会 400**，而是被静默解释成教程评论
   （service 层的纯函数用例测的是另一回事，它测不到默认值）；
2. 管理端 `tutorial_id` 兼容参数（R5-06）：删掉它不会报错，只会让筛选**静默失效**
   返回全部评论 —— 必须发真请求才测得到；
3. 管理端响应里的 `tutorial_id`/`tutorial_title` 过渡别名（R5-06），
   旧 bundle 的「所属内容」列靠它渲染。
"""

import unittest
from contextlib import asynccontextmanager

import httpx

from app.api.comments import resolve_comment_target
from app.core import rbac as _rbac
from app.core.exceptions import AppException
from app.database import get_db
from app.core.permissions import require_admin
from app.models.comment import (
    ContentComment,
    MAX_COMMENT_IMAGES_PER_USER_PER_DAY,
)
from app.models.node import Node
from app.models.tutorial import Tutorial
from app.models.user import User
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession
from app.services.storage import StorageError, storage

AUTHOR = User(id=100, account="u100@example.com", password_hash="x", role="user")
ADMIN = User(id=9001, account="admin@test", password_hash="x", role="admin")


def _seed_db() -> MemorySession:
    """教程 100 与节点 100 **同 id**，各挂一条评论 —— 路由只要漏掉 target_type 就会串。"""
    db = MemorySession()
    tutorial = Tutorial(id=100, title="教程A", description="简介", content="正文", category="ai")
    tutorial.is_published = True
    db.seed(AUTHOR, ADMIN, tutorial, Node(id=100, name="节点A", category="core", description="简介", content="正文"))
    db.seed(
        ContentComment(id=501, target_type="tutorial", target_id=100, user_id=AUTHOR.id, content="教程侧评论"),
        ContentComment(id=502, target_type="node", target_id=100, user_id=AUTHOR.id, content="节点侧评论"),
    )
    return db


class _RouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = _seed_db()

    @asynccontextmanager
    async def _client(self):
        from app.main import app

        async def _override_db():
            yield self.db

        async def _override_admin():
            return ADMIN

        async def _no_route_check():
            pass

        app.dependency_overrides[get_db] = _override_db
        # 路由级细粒度权限依赖真实角色表（由 test_rbac / test_admin_route_guards 专项覆盖），
        # 这里只覆盖它，`require_admin` 仍走真实实现之外的注入点（下面显式给管理员）
        app.dependency_overrides[_rbac.check_route_permission] = _no_route_check
        app.dependency_overrides[require_admin] = _override_admin
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test", timeout=10.0
            ) as client:
                yield client
        finally:
            for dep in (get_db, _rbac.check_route_permission, require_admin):
                app.dependency_overrides.pop(dep, None)


class PublicCommentRouteTests(_RouteTests):
    async def test_target_type_selects_the_right_tree(self):
        async with self._client() as client:
            node = (await client.get("/api/v1/comments", params={"target_type": "node", "target_id": 100})).json()
            tutorial = (await client.get("/api/v1/comments", params={"target_type": "tutorial", "target_id": 100})).json()
        self.assertEqual([i["content"] for i in node["data"]["items"]], ["节点侧评论"])
        self.assertEqual([i["content"] for i in tutorial["data"]["items"]], ["教程侧评论"])

    async def test_omitting_target_type_silently_means_tutorial(self):
        """**当前契约**：省略 target_type 不报错，按教程处理。

        这是路由默认值造成的行为，纯函数用例测不到（`resolve_comment_target` 单测里
        传的是显式 None）。本用例把它钉下来，是为了让这个决定**可见**：
        将来有人把默认值改成必填（更严格、也更合理），这里会红，逼他显式确认
        「旧 bundle 会不会被打成 400」——而不是悄悄改掉一个对外行为。
        """
        async with self._client() as client:
            resp = await client.get("/api/v1/comments", params={"target_id": 100})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([i["content"] for i in resp.json()["data"]["items"]], ["教程侧评论"])

    async def test_unknown_target_type_is_400_not_500(self):
        async with self._client() as client:
            resp = await client.get("/api/v1/comments", params={"target_type": "plugin", "target_id": 100})
        self.assertEqual(resp.status_code, 400, f"未开放的目标类型必须 400，实得 {resp.status_code}")

    async def test_service_shape_matches_route_shape(self):
        """路由与 service 必须返回同一棵树（防「路由写死 target_type」这类回归）"""
        from app.services import comment_service

        async with self._client() as client:
            via_route = (await client.get("/api/v1/comments", params={"target_type": "node", "target_id": 100})).json()
        via_service = await comment_service.list_comments(self.db, "node", 100)
        self.assertEqual([i["id"] for i in via_route["data"]["items"]], [i["id"] for i in via_service["items"]])


class AdminCommentRouteTests(_RouteTests):
    async def test_legacy_tutorial_id_still_filters(self):
        """旧参数必须**仍然生效**（R5-06）。

        删掉这个 Query 参数不会让任何用例变红：FastAPI 静默忽略未声明的 query，
        症状是「筛选框填了值、列表却是全部评论」，管理员据此误删漏删。
        """
        async with self._client() as client:
            resp = await client.get("/api/v1/admin/comments", params={"tutorial_id": 100})
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["data"]["items"]
        self.assertEqual([i["content"] for i in items], ["教程侧评论"], "旧参数失效就等于不过滤")

    async def test_admin_response_carries_legacy_aliases(self):
        """教程行必须同时给出 `tutorial_id`/`tutorial_title` 别名（旧 bundle 的列靠它们渲染）"""
        async with self._client() as client:
            items = (await client.get("/api/v1/admin/comments", params={"target_type": "tutorial", "target_id": 100})).json()["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["target_type"], "tutorial")
        self.assertEqual(items[0]["tutorial_id"], 100, "缺别名 → 旧管理端「所属内容」列渲染成空白")
        self.assertEqual(items[0]["tutorial_title"], "教程A")
        self.assertEqual(items[0]["target_title"], "教程A")

    async def test_node_rows_do_not_carry_tutorial_alias(self):
        """别名只在教程行上给：node/resource 行不得凭空多出 tutorial_id（避免扩大对外承诺面）"""
        async with self._client() as client:
            items = (await client.get("/api/v1/admin/comments", params={"target_type": "node", "target_id": 100})).json()["data"]["items"]
        self.assertEqual([i["content"] for i in items], ["节点侧评论"])
        self.assertNotIn("tutorial_id", items[0])
        self.assertNotIn("tutorial_title", items[0])

    async def test_new_params_win_over_legacy(self):
        """同时给新参数与旧参数时以新参数为准（兼容层不能反过来覆盖新契约）"""
        async with self._client() as client:
            items = (await client.get(
                "/api/v1/admin/comments",
                params={"target_type": "node", "target_id": 100, "tutorial_id": 777},
            )).json()["data"]["items"]
        self.assertEqual([i["content"] for i in items], ["节点侧评论"])


class ResolveTargetContractTests(unittest.TestCase):
    """把「路由默认值」与纯函数行为的**差异**写清楚（防下一个审查员误判）。"""

    def test_function_rejects_missing_type_but_route_defaults_it(self):
        with self.assertRaises(AppException):
            resolve_comment_target(None, 100, None)   # 纯函数：缺类型必须拒
        # 而经路由时 target_type 有默认值 "tutorial"，同样的请求会被解释成教程评论
        self.assertEqual(resolve_comment_target("tutorial", 100, None), ("tutorial", 100))


class CommentImageUploadQuotaTests(unittest.IsolatedAsyncioTestCase):
    """评论配图必须有按用户的日张数上限（第五轮审查 R5-12）。

    这是全站唯一「任意注册用户可写、对象永不复用、不产生任何业务行」的公开入口：
    文本评论有 30 条/日配额，图片一条都没有，而上传成功即返回本站 COS 公开可读 URL
    —— **不必真的发评论**。所以这里守两件事：
    ① 配额在**读文件与内容校验之前**判定（否则每次浪费都免费）；
    ② 到上界后返回 429，而不是继续铸对象。
    """

    def setUp(self):
        from app.core import rate_limit as _rl
        from app.dependencies import get_current_user
        from app.main import app

        self.db = _seed_db()
        self._orig_redis = _rl.redis_client
        _rl.redis_client = FakeRedis()          # 计数走内存，绝不碰真实 Redis

        async def _override_db():
            yield self.db

        async def _override_user():
            return AUTHOR

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = _override_user
        self._app, self._get_current_user = app, get_current_user

    def tearDown(self):
        from app.core import rate_limit as _rl
        _rl.redis_client = self._orig_redis
        self._app.dependency_overrides.clear()

    async def _post_junk(self, client):
        # 故意用「假 PNG 字节」：内容嗅探会拒成 400，但**配额已经先扣掉了**，
        # 这正好同时证明配额在文件校验之前执行
        return await client.post(
            "/api/v1/comments/images", files={"file": ("x.png", b"not-a-real-png", "image/png")}
        )

    async def test_upload_hits_daily_cap_then_429(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self._app), base_url="http://test"
        ) as client:
            codes = [(await self._post_junk(client)).status_code
                     for _ in range(MAX_COMMENT_IMAGES_PER_USER_PER_DAY + 2)]

        self.assertEqual(
            codes[:MAX_COMMENT_IMAGES_PER_USER_PER_DAY],
            [400] * MAX_COMMENT_IMAGES_PER_USER_PER_DAY,
            f"上界内应走到内容校验（400），实得前三个 {codes[:3]}",
        )
        self.assertTrue(
            all(c == 429 for c in codes[MAX_COMMENT_IMAGES_PER_USER_PER_DAY:]),
            f"超过日上界后必须 429，实得 {codes[MAX_COMMENT_IMAGES_PER_USER_PER_DAY:]}",
        )

    async def test_quota_is_per_user_not_one_global_bucket(self):
        """A 用满不应让 B 传不了（按 user_id 分桶）"""
        other = User(id=200, account="u200@example.com", password_hash="x", role="user")
        self.db.seed(other)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self._app), base_url="http://test"
        ) as client:
            for _ in range(MAX_COMMENT_IMAGES_PER_USER_PER_DAY):
                await self._post_junk(client)

            async def _override_other():
                return other

            self._app.dependency_overrides[self._get_current_user] = _override_other
            resp = await self._post_junk(client)
        self.assertEqual(
            resp.status_code, 400,
            "新用户应正常走到内容校验，而不是被别人的配额拦住（429）",
        )


class _ExpiredAfterRollbackUser:
    """回滚后读属性即抛 MissingGreenlet —— 真实 AsyncSession 的 expire-on-rollback 语义。

    本机已用真实 MySQL 会话实测：load → `await session.rollback()` → 读 `user.id`
    抛 `MissingGreenlet: greenlet_spawn has not been called`。
    """

    def __init__(self, user: User):
        self._user = user
        self._expired = False

    def expire(self) -> None:
        self._expired = True

    def __getattr__(self, name):
        if self._expired:
            from sqlalchemy.exc import MissingGreenlet

            raise MissingGreenlet(
                "greenlet_spawn has not been called; can't call await_only() here."
            )
        return getattr(self._user, name)


class _RollbackSession:
    """只实现本路由用到的 rollback()，并把回滚如实转成「对象过期」。"""

    def __init__(self, target: _ExpiredAfterRollbackUser):
        self._target = target

    async def rollback(self) -> None:
        self._target.expire()


class CommentImageUploadRollbackTests(unittest.IsolatedAsyncioTestCase):
    """`POST /comments/images` 在 `await db.rollback()` 之后不得再碰 `current_user` 的属性。

    该路由先回滚释放鉴权事务（全程不写库），再用 `current_user.id` 计配额；而
    `get_current_user` 与本路由用的是**同一条会话**（FastAPI 会缓存 `get_db`），
    回滚会过期这条会话加载出的对象，于是那次属性读取变成一次「没有 greenlet 的补载 SELECT」
    → MissingGreenlet → 全局兜底 500「服务器内部错误」。
    2026-10-13 线上评论配图全量失败即此因；`/uploads/image` 同形但回滚后不碰对象，所以没炸。
    MemorySession 的 rollback 是空操作，测不到这条语义，故单独立一组。
    """

    # 真实可解码的 1×1 PNG：此前是「魔数 + 64 个 0」的伪图，只够过魔数校验；
    # 上传链路现在会解析图片头拿尺寸（拦解压炸弹），伪图会被判为无法解析
    VALID_PNG = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\r\xefF\xb8"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    def setUp(self):
        from app.core import rate_limit as _rl
        from app.dependencies import get_current_user
        from app.main import app
        

        self.user = _ExpiredAfterRollbackUser(
            User(id=131, account="u131@example.com", password_hash="x", role="user")
        )
        self.db = _RollbackSession(self.user)
        self._redis = FakeRedis()
        self._orig_redis = _rl.redis_client
        _rl.redis_client = self._redis
        self._orig_upload = storage.upload_bytes

        async def _fake_upload(data, *, folder, ext, content_type):
            return f"https://cos.invalid/{folder}/obj{ext}"

        storage.upload_bytes = _fake_upload

        async def _override_db():
            yield self.db

        async def _override_user():
            return self.user

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = _override_user
        self._app, self._get_current_user, self._get_db = app, get_current_user, get_db

    def tearDown(self):
        from app.core import rate_limit as _rl
        

        _rl.redis_client = self._orig_redis
        storage.upload_bytes = self._orig_upload
        self._app.dependency_overrides.clear()

    async def _post_png(self, client: httpx.AsyncClient):
        return await client.post(
            "/api/v1/comments/images",
            files={"file": ("shot.png", self.VALID_PNG, "image/png")},
        )

    async def test_upload_survives_the_auth_transaction_rollback(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self._app), base_url="http://test"
        ) as client:
            resp = await self._post_png(client)
            self.assertEqual(
                resp.status_code, 200,
                f"回滚后读 ORM 属性会 500（MissingGreenlet），实得 {resp.status_code} {resp.text[:200]}",
            )
            self.assertEqual(resp.json()["data"]["url"], "https://cos.invalid/comments/obj.png")

    async def test_quota_is_charged_to_the_calling_user_not_to_none(self):
        """id 必须在回滚**之前**取：取不到时键会退化成 `user_daily:None`，
        等于全站共用一个桶 —— 60 张/日 的上限对所有人一起失效。"""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self._app), base_url="http://test"
        ) as client:
            await self._post_png(client)
        self.assertEqual(str(await self._redis.get("comment_image:user_daily:131")), "1")
        self.assertIsNone(await self._redis.get("comment_image:user_daily:None"))


class CommentImageUploadCosFailureTests(unittest.IsolatedAsyncioTestCase):
    """COS 侧失败必须落在「图片存储失败」这条可定位的 500 上，而不是全局兜底。

    路由只 `except StorageError`；`storage` 的契约是任何初始化/写入失败都收进
    CosError（见 tests/test_cos_client_init.py）。这条用例把两端接起来：一旦有人把
    `_get_client()` 的新异常漏出映射，用户看到的就是无信息量的「服务器内部错误」。
    """

    # 真实可解码的 1×1 PNG：此前是「魔数 + 64 个 0」的伪图，只够过魔数校验；
    # 上传链路现在会解析图片头拿尺寸（拦解压炸弹），伪图会被判为无法解析
    VALID_PNG = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\r\xefF\xb8"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    def setUp(self):
        from app.core import rate_limit as _rl
        from app.dependencies import get_current_user
        from app.main import app
        

        user = User(id=132, account="u132@example.com", password_hash="x", role="user")
        db = MemorySession()
        db.seed(user)
        self._orig_redis = _rl.redis_client
        _rl.redis_client = FakeRedis()
        self._orig_upload = storage.upload_bytes

        async def _failing_upload(data, *, folder, ext, content_type):
            raise StorageError("COS 客户端初始化失败（ModuleNotFoundError: ...）")

        storage.upload_bytes = _failing_upload

        async def _override_db():
            yield db

        async def _override_user():
            return user

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = _override_user
        self._app = app

    def tearDown(self):
        from app.core import rate_limit as _rl
        

        _rl.redis_client = self._orig_redis
        storage.upload_bytes = self._orig_upload
        self._app.dependency_overrides.clear()

    async def test_cos_failure_maps_to_actionable_500(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self._app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/comments/images",
                files={"file": ("shot.png", self.VALID_PNG, "image/png")},
            )
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["message"], "图片存储失败, 请稍后重试")
