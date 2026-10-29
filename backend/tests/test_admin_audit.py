"""管理操作审计的契约测试（缺口台账 P2-9 / 代码审查 D7）。

这一层要钉死的不是「代码跑得通」，而是四条**一旦破了不会报错、只会悄悄失去意义**
的契约：

1. **审计与业务同事务**。``record_admin_action`` 只 ``add`` 不 ``commit`` ——
   若它自己提交，业务随后失败回滚就会留下「记录说改了、实际没改」的假审计，
   比没有审计更糟。
2. **审计不能拖垮业务**。拿不到操作者时只记 warning 后跳过，不抛异常；
   未登记的动作码仍写入（漏登记只影响筛选下拉，不该让管理操作失败）。
3. **审计表不得有外键**。操作者/目标被删除后审计必须仍在；加外键会触发 1451
   拒绝删除，或配级联把审计一起删掉 —— 两种结果都让审计失去意义。
4. **写入口的动作码必须已登记**。否则后台筛选下拉里找不到该动作，
   而检索不到记录**不会有任何报错**（静态扫描 backend/app 守住）。
"""

import asyncio
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.rbac import match_route_permission
from app.models.admin_audit_log import AUDIT_ACTIONS, AdminAuditLog
from app.models.tutorial import Tutorial
from app.models.user import User
from app.services import admin_audit_service as aas
from app.services import admin_tutorial_service as ats
from app.services.rbac_catalog import ALL_PERMISSION_CODES, PERMISSION_CATALOG, SYSTEM_ROLES
from tests.fake_db import MemorySession

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "backend" / "app"

ADMIN = User(id=9001, account="admin@test")


def make_log(**kwargs) -> AdminAuditLog:
    """直接构造审计行（用于列表筛选测试，跳过 record 的截断逻辑）"""
    defaults = dict(
        id=kwargs.pop("id", 1),
        admin_id=9001,
        admin_account="admin@test",
        action="content_delete",
        target_type="tutorial",
        target_id=1,
        summary="删除教程《A》",
    )
    defaults.update(kwargs)
    return AdminAuditLog(**defaults)


class TestRecordSemantics(unittest.IsolatedAsyncioTestCase):
    async def test_record_writes_row_with_account_snapshot(self):
        db = MemorySession()
        aas.record_admin_action(
            db, ADMIN, "content_delete",
            target_type="tutorial", target_id=7,
            summary="删除教程《X》", detail={"ids": [7]},
        )
        await db.commit()
        rows = db.all_rows(AdminAuditLog)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["admin_id"], 9001)
        # 账号快照：账号可改名/注销，只存 id 事后无法回答「当时是谁」
        self.assertEqual(rows[0]["admin_account"], "admin@test")
        self.assertEqual(rows[0]["action"], "content_delete")
        self.assertEqual(rows[0]["detail"], {"ids": [7]})

    async def test_record_only_adds_and_never_commits(self):
        """关键契约：审计行必须随业务事务提交，业务回滚则审计一起消失。"""
        db = MemorySession()
        aas.record_admin_action(db, ADMIN, "user_delete", target_type="user", summary="删号")
        # 尚未 commit：数据库里不该有行
        self.assertEqual(db.all_rows(AdminAuditLog), [])
        await db.commit()
        self.assertEqual(len(db.all_rows(AdminAuditLog)), 1)

    async def test_record_rollback_discards_audit_row(self):
        """业务失败回滚时，不能留下「记录说做了、实际没做」的假审计。"""
        db = MemorySession()
        aas.record_admin_action(db, ADMIN, "user_delete", target_type="user", summary="删号")
        await db.rollback()
        await db.commit()
        self.assertEqual(db.all_rows(AdminAuditLog), [])

    async def test_record_skips_when_operator_unidentifiable(self):
        """审计不能反过来阻断业务：拿不到操作者时跳过，不抛异常。"""
        db = MemorySession()
        aas.record_admin_action(db, None, "user_delete", summary="删号")
        aas.record_admin_action(db, User(account="no-id"), "user_delete", summary="删号")
        await db.commit()
        self.assertEqual(db.all_rows(AdminAuditLog), [])

    async def test_record_unknown_action_still_written(self):
        """未登记的动作码仍要落库 —— 漏登记只影响筛选下拉，不该丢审计。"""
        db = MemorySession()
        aas.record_admin_action(db, ADMIN, "not_registered_yet", summary="某个新动作")
        await db.commit()
        rows = db.all_rows(AdminAuditLog)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "not_registered_yet")

    async def test_record_truncates_long_fields(self):
        db = MemorySession()
        aas.record_admin_action(
            db,
            User(id=1, account="a" * 300),
            "user_delete",
            summary="x" * 900,
        )
        await db.commit()
        row = db.all_rows(AdminAuditLog)[0]
        self.assertEqual(len(row["admin_account"]), 120)
        self.assertEqual(len(row["summary"]), 500)


class TestSummarizeIds(unittest.TestCase):
    def test_short_list_passthrough(self):
        self.assertEqual(aas.summarize_ids([1, 2, 3]), [1, 2, 3])

    def test_long_list_truncated_with_sentinel(self):
        """批量操作可能几百个 id，全存会把审计表撑起来；末尾 -1 提示「还有更多」。"""
        ids = list(range(1, 201))
        out = aas.summarize_ids(ids)
        self.assertEqual(len(out), aas.MAX_DETAIL_IDS + 1)
        self.assertEqual(out[-1], -1)
        self.assertEqual(out[:-1], ids[: aas.MAX_DETAIL_IDS])


class TestListFilters(unittest.IsolatedAsyncioTestCase):
    async def _seeded(self) -> MemorySession:
        db = MemorySession()
        db.seed(
            make_log(id=1, action="content_delete", admin_id=9001, target_type="tutorial"),
            make_log(id=2, action="user_update_role", admin_id=9002, target_type="user",
                     admin_account="other@test", summary="角色 user -> admin"),
            make_log(id=3, action="content_delete", admin_id=9002, target_type="plugin",
                     admin_account="other@test", summary="删除扩展《B》"),
        )
        return db

    async def test_filter_by_action(self):
        db = await self._seeded()
        data = await aas.list_audit_logs(db, action="content_delete")
        self.assertEqual(data["total"], 2)
        self.assertTrue(all(i["action"] == "content_delete" for i in data["items"]))

    async def test_filter_by_admin_id(self):
        db = await self._seeded()
        data = await aas.list_audit_logs(db, admin_id=9002)
        self.assertEqual(data["total"], 2)

    async def test_filter_by_target_type(self):
        db = await self._seeded()
        data = await aas.list_audit_logs(db, target_type="plugin")
        self.assertEqual(data["total"], 1)

    async def test_keyword_matches_summary(self):
        db = await self._seeded()
        data = await aas.list_audit_logs(db, keyword="扩展")
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["action"], "content_delete")

    async def test_keyword_matches_operator_account(self):
        db = await self._seeded()
        data = await aas.list_audit_logs(db, keyword="other@test")
        self.assertEqual(data["total"], 2)

    async def test_keyword_wildcard_is_escaped(self):
        """``%`` 必须被转义成字面量：否则搜 ``%`` 会命中全部记录（escape_like 的意义）。"""
        db = MemorySession()
        db.seed(
            make_log(id=1, summary="折扣 100% 到账"),
            make_log(id=2, summary="无关记录"),
        )
        data = await aas.list_audit_logs(db, keyword="%")
        self.assertEqual(data["total"], 1)

    async def test_all_sentinel_means_no_filter(self):
        db = await self._seeded()
        data = await aas.list_audit_logs(db, action="all", target_type="all")
        self.assertEqual(data["total"], 3)

    async def test_items_carry_label_and_detail(self):
        db = MemorySession()
        db.seed(make_log(id=1, action="role_permission_update", detail={"added": ["audit:view"]}))
        data = await aas.list_audit_logs(db)
        item = data["items"][0]
        self.assertEqual(item["action_label"], AUDIT_ACTIONS["role_permission_update"])
        self.assertEqual(item["detail"], {"added": ["audit:view"]})


class TestModelInvariants(unittest.TestCase):
    def test_table_has_no_foreign_keys(self):
        """操作者/目标被删后审计必须仍在 —— 加外键会让 1451 拒绝删除或级联删掉审计。"""
        fks = list(AdminAuditLog.__table__.foreign_keys)
        self.assertEqual(fks, [], f"审计表不得有外键，实际发现: {[f.target_fullname for f in fks]}")

    def test_declared_indexes_cover_three_query_shapes(self):
        names = {idx.name for idx in AdminAuditLog.__table__.indexes}
        self.assertIn("ix_aal_admin_created", names)   # 按操作者回溯
        self.assertIn("ix_aal_action_created", names)  # 按动作排查
        self.assertIn("ix_aal_target", names)          # 按目标回溯


class TestActionCodesAreRegistered(unittest.TestCase):
    """静态扫描：所有 record_admin_action 写入口的动作码都必须在 AUDIT_ACTIONS 登记。

    漏登记不会报错，只会让后台筛选下拉缺一项 → 该动作的记录搜不到。
    """

    _CALL_RE = re.compile(
        r"record_admin_action\(\s*[^,()]+,\s*[^,()]+,\s*[\"']([a-z_]+)[\"']", re.S
    )

    def test_all_written_action_codes_are_registered(self):
        found: set[str] = set()
        for path in APP_DIR.rglob("*.py"):
            if path.name == "admin_audit_service.py":  # 定义处不是写入口
                continue
            found.update(self._CALL_RE.findall(path.read_text(encoding="utf-8")))
        self.assertTrue(found, "未扫描到任何 record_admin_action 调用，正则可能已失效")
        self.assertEqual(found - set(AUDIT_ACTIONS), set())


class TestRbacWiring(unittest.TestCase):
    def test_audit_view_permission_declared(self):
        self.assertIn("audit:view", ALL_PERMISSION_CODES)

    def test_audit_has_no_write_permission(self):
        """审计只能看不能改：出现任何 audit:create/edit/delete 都是设计错误。"""
        writes = {c for c in ALL_PERMISSION_CODES if c.startswith("audit:") and c != "audit:view"}
        self.assertEqual(writes, set())

    def test_route_rule_requires_audit_view(self):
        self.assertEqual(
            match_route_permission("GET", "/api/v1/admin/audit-logs"), ["audit:view"]
        )

    def test_viewer_role_excludes_audit_view(self):
        """只读管理员默认看不到审计（审计会暴露正在被调查的对象）。"""
        viewer = next(r for r in SYSTEM_ROLES if r["code"] == "viewer")
        self.assertNotIn("audit:view", viewer["permissions"])

    def test_super_admin_holds_audit_view(self):
        super_admin = next(r for r in SYSTEM_ROLES if r["code"] == "super_admin")
        self.assertIn("audit:view", super_admin["permissions"])

    def test_permission_module_declared(self):
        perm = next(p for p in PERMISSION_CATALOG if p.code == "audit:view")
        self.assertEqual(perm.module, "audit")
        self.assertEqual(perm.type, "menu")


class TestContentDeleteWritesAudit(unittest.IsolatedAsyncioTestCase):
    """埋点接线：硬删除必须在**同一事务**里留下审计行。"""

    async def test_delete_tutorial_records_audit(self):
        db = MemorySession()
        db.seed(Tutorial(id=1, title="教程A"))
        await ats.delete_tutorial(db, 1, ADMIN)
        rows = db.all_rows(AdminAuditLog)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "content_delete")
        self.assertEqual(rows[0]["target_type"], "tutorial")
        self.assertEqual(rows[0]["target_id"], 1)
        self.assertIn("教程A", rows[0]["summary"])

    async def test_delete_missing_tutorial_writes_no_audit(self):
        """目标不存在时抛 404，此时**不能**留下审计行（否则是假记录）。"""
        db = MemorySession()
        with self.assertRaises(Exception):
            await ats.delete_tutorial(db, 999, ADMIN)
        self.assertEqual(db.all_rows(AdminAuditLog), [])

    async def test_batch_delete_records_count_and_ids(self):
        db = MemorySession()
        db.seed(Tutorial(id=1, title="A"), Tutorial(id=2, title="B"))
        await ats.batch_delete_tutorials(db, [1, 2], ADMIN)
        rows = db.all_rows(AdminAuditLog)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["detail"]["count"], 2)
        self.assertEqual(sorted(rows[0]["detail"]["ids"]), [1, 2])


class TestFakeDbContainsSupport(unittest.TestCase):
    """内存会话的 LIKE 语义自检：本文件大量依赖它，它错了测试会假绿。"""

    def test_contains_plain_substring(self):
        from tests.fake_db import _contains_match

        self.assertTrue(_contains_match("删除教程《A》", "教程", "\\"))
        self.assertFalse(_contains_match("删除扩展《B》", "教程", "\\"))

    def test_contains_honours_escape(self):
        from tests.fake_db import _contains_match

        # escape_like 会把用户输入的 % / _ 转义成 \% / \_
        self.assertTrue(_contains_match("折扣 100% 到账", "100\\%", "\\"))
        self.assertFalse(_contains_match("折扣 1000 到账", "100\\%", "\\"))
        self.assertFalse(_contains_match("折扣 100% 到账", "10\\_0\\%", "\\"))
        self.assertTrue(_contains_match("折扣 10_0% 到账", "10\\_0\\%", "\\"))

    def test_unescaped_wildcards_still_work(self):
        from tests.fake_db import _contains_match

        self.assertTrue(_contains_match("abcdef", "c%d", "\\"))
        self.assertTrue(_contains_match("zabfy", "a_f", "\\"))   # a + 任意单字符 + f
        self.assertFalse(_contains_match("abcdef", "a_f", "\\"))  # a/b/c 不是 a/?/f


class TestAuditSourceFields(unittest.IsolatedAsyncioTestCase):
    """来源字段（第三轮增量审查 P1-49③ / 台账 P2-19）：
    IP / User-Agent / request_id 的写入、缺口与保留期清理。
    """

    async def test_source_fields_filled_from_request_context(self):
        """请求上下文中写审计 → 三个来源字段全部落库（且与响应头呼应）"""
        import httpx
        from fastapi import FastAPI

        from app.core.request_context import RequestIdMiddleware

        db = MemorySession()
        app = FastAPI()
        app.add_middleware(RequestIdMiddleware)

        @app.post("/audit")
        async def _audit():
            aas.record_admin_action(db, ADMIN, "user_delete", target_type="user", summary="删号")
            await db.commit()
            return {"ok": True}

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"User-Agent": "pytest-agent/1.0"},
        ) as client:
            resp = await client.post("/audit", headers={"X-Request-Id": "rid-audit-1"})

        self.assertEqual(resp.status_code, 200)
        rows = db.all_rows(AdminAuditLog)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["request_id"], "rid-audit-1", "关联 ID 必须与请求一致，否则无法回溯")
        self.assertEqual(rows[0]["user_agent"], "pytest-agent/1.0")
        # ASGITransport 的对端是 127.0.0.1（回环属可信代理网段）→ 无转发头时取对端地址
        self.assertEqual(rows[0]["ip"], "127.0.0.1")

    async def test_source_fields_null_without_request_context(self):
        """脚本/测试等无请求上下文时三者必须为 NULL，而不是空串或 "-" """
        db = MemorySession()
        aas.record_admin_action(db, ADMIN, "user_delete", target_type="user", summary="删号")
        await db.commit()
        row = db.all_rows(AdminAuditLog)[0]
        self.assertIsNone(row["ip"])
        self.assertIsNone(row["user_agent"])
        self.assertIsNone(row["request_id"])

    async def test_source_fields_truncated_to_column_width(self):
        """超长 UA 不得超出列宽（MySQL 严格模式下会直接报错丢掉整条审计）"""
        import httpx
        from fastapi import FastAPI

        from app.core.request_context import RequestIdMiddleware

        db = MemorySession()
        app = FastAPI()
        app.add_middleware(RequestIdMiddleware)

        @app.post("/audit")
        async def _audit():
            aas.record_admin_action(db, ADMIN, "user_delete", target_type="user", summary="删号")
            await db.commit()
            return {"ok": True}

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/audit", headers={"User-Agent": "U" * 800})

        row = db.all_rows(AdminAuditLog)[0]
        self.assertEqual(len(row["user_agent"]), 500)

    async def test_record_exposes_source_fields_in_list(self):
        """列表接口必须回传来源字段（前端才能展示「来源 IP」）"""
        db = MemorySession()
        aas.record_admin_action(db, ADMIN, "user_delete", target_type="user", summary="删号")
        await db.commit()
        result = await aas.list_audit_logs(db)
        self.assertEqual(len(result["items"]), 1)
        for key in ("ip", "user_agent", "request_id"):
            self.assertIn(key, result["items"][0])

    async def test_cleanup_removes_only_expired_rows(self):
        """保留期清理：只删超期行，且返回删除条数（只执行不提交）"""
        db = MemorySession()
        now = datetime.now(timezone.utc)
        db.seed(
            make_log(id=1, created_at=now - timedelta(days=aas.AUDIT_LOG_RETENTION_DAYS + 1)),
            make_log(id=2, created_at=now - timedelta(days=1)),
        )

        deleted = await aas.cleanup_expired_audit_logs(db)

        self.assertEqual(deleted, 1)
        self.assertEqual(db.commits, 0, "清理不得自行提交（事务归调用方）")
        self.assertEqual([r["id"] for r in db.all_rows(AdminAuditLog)], [2])


class TestSourceFieldWiring(unittest.TestCase):
    """接线守卫：模型列 / 迁移接线 / 调度任务 —— 任一环缺失都是静默失效"""

    def test_model_has_source_columns(self):
        cols = {c.name for c in AdminAuditLog.__table__.columns}
        for name in ("ip", "user_agent", "request_id"):
            self.assertIn(name, cols, f"审计模型缺少来源字段 {name}")

    def test_new_migration_is_alembic_head(self):
        """新迁移必须挂在链尾 —— 挂在中间会让 head 之前的库缺列而无人察觉

        ⚠️ 这里的 expected head 是**有意硬编码**的棘轮：每新增一个迁移都必须
        同步改这里（并顺手确认自己确实接在链尾）。若改成「动态取最新文件」，
        「迁移挂到链中间」就再也测不出来了。
        """
        import re

        versions_dir = REPO_ROOT / "backend" / "alembic" / "versions"
        files = sorted(versions_dir.glob("*.py"))
        revisions: dict[str, str | None] = {}
        for path in files:
            text = path.read_text(encoding="utf-8")
            rev = re.search(r"^revision:\s*str\s*=\s*['\"]([^'\"]+)['\"]", text, re.M)
            down = re.search(r"^down_revision:\s*Union\[str,\s*None\]\s*=\s*(?:['\"]([^'\"]+)['\"]|None)", text, re.M)
            if rev:
                revisions[rev.group(1)] = down.group(1) if down and down.group(1) else None

        # 解析数必须等于文件数：否则某条迁移换了注解写法就会被**静默跳过**，
        # 链少一个节点，而下面的断言只会红成「head 不对」，看不出真正的原因。
        self.assertEqual(
            len(revisions), len(files),
            f"有迁移没被解析到 revision（解析 {len(revisions)} / 文件 {len(files)}）"
            " —— 检查 revision/down_revision 的注解写法是否符合本测试的正则",
        )
        self.assertIn("add_audit_source_fields", revisions, "新迁移文件不存在")
        referenced = {down for down in revisions.values() if down}
        heads = sorted(set(revisions) - referenced)
        self.assertEqual(
            heads,
            # ⚠️ 每次新增迁移都要同步改这里：这里刻意写死而非「断言 head 唯一」，
            # 2026-10-30 分类分组新增 category_groups 迁移。
            # 因为只判唯一会漏掉「新迁移插错了位置、链仍能收敛成一个 head」的情况。
            # 代价是加迁移要改一处测试 —— 换来的是 head 漂移一定会被发现。
            ["add_category_groups"],
            f"alembic head 不是最新迁移（当前 heads={heads}）—— 迁移链可能断开或挂错位置",
        )

    def test_migration_adds_three_columns(self):
        text = (REPO_ROOT / "backend" / "alembic" / "versions"
                / "20261011_0037_add_audit_source_fields.py").read_text(encoding="utf-8")
        for name in ("'ip'", "'user_agent'", "'request_id'"):
            self.assertIn(name, text, f"迁移里缺少 {name} 列")
        self.assertIn("admin_audit_logs", text)

    def test_scheduler_registers_cleanup_job(self):
        tasks = (REPO_ROOT / "backend" / "scheduler" / "tasks.py").read_text(encoding="utf-8")
        self.assertIn("cleanup_audit_logs", tasks, "保留期清理任务未注册（审计表将无限增长）")
        self.assertIn("cleanup_expired_audit_logs", tasks)
        self.assertEqual(aas.AUDIT_LOG_RETENTION_DAYS, 365)


class TestAuditFieldChanges(unittest.TestCase):
    """字段级新旧值（第五轮全量审查 P2-55 之四）。

    审计原先只记「改了哪些字段名」，答不了「把对外署名从谁改成谁」——
    而备案站点被要求能追溯的正是后者。
    """

    def test_records_old_and_new_values(self):
        from sqlalchemy.orm.attributes import set_committed_value

        node = Tutorial(id=1, title="新标题")
        # 模拟「对象是从会话里读出来的」：把基线值写成旧值，之后的赋值才有历史可比
        set_committed_value(node, "title", "旧标题")
        node.title = "新标题"
        changes = aas.audit_changes(node, {"title": "新标题"})
        self.assertEqual(changes["title"], ["旧标题", "新标题"])

    def test_null_old_value_is_not_confused_with_missing_baseline(self):
        """`None` 是这些字段的合法旧值（清空封面/署名）；没基线时必须用显式占位区分开。"""
        from sqlalchemy.orm.attributes import set_committed_value

        node = Tutorial(id=1, title="T")
        set_committed_value(node, "seo_title", None)
        node.seo_title = "SEO 标题"
        self.assertEqual(aas.audit_changes(node, {"seo_title": "SEO 标题"})["seo_title"], [None, "SEO 标题"])

        transient = Tutorial(id=2, title="T")
        transient.cover_image = "https://x/y.jpg"
        recorded = aas.audit_changes(transient, {"cover_image": "https://x/y.jpg"})["cover_image"]
        self.assertEqual(recorded[0], aas._NO_BASELINE, "没有基线时应显式标注，不能写成 None（那是伪造历史）")

    def test_sensitive_fields_are_redacted(self):
        """命中敏感命名的字段连值都不落（本项目这些字段已是密文/掩码，此处防的是新增字段）。"""
        node = Tutorial(id=1, title="T")
        changes = aas.audit_changes(node, {"api_key": "sk-abcdef", "password_hash": "x"})
        self.assertEqual(changes["api_key"], ["***", "***"])
        self.assertEqual(changes["password_hash"], ["***", "***"])

    def test_long_values_are_truncated(self):
        from sqlalchemy.orm.attributes import set_committed_value

        node = Tutorial(id=1, title="T")
        set_committed_value(node, "content", "旧正文")
        node.content = "x" * 5000
        old, new = aas.audit_changes(node, {"content": node.content})["content"]
        self.assertEqual(old, "旧正文")
        self.assertLessEqual(len(new), aas.AUDIT_VALUE_MAX + 1, "正文这类 MEDIUMTEXT 必须截断，否则审计表会吃掉存储")

    def test_every_update_site_records_changes(self):
        """静态守卫：新增写操作时不许再退回「只记字段名」。"""
        offenders = []
        services = Path(aas.__file__).parent
        for path in sorted(services.glob("*.py")):
            if path.name == "admin_audit_service.py":
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").split(chr(10)), 1):
                if '"fields": sorted(updates.keys())' in line and '"changes"' not in line:
                    offenders.append(f"{path.name}:{n}")
        self.assertEqual(offenders, [], "以下写操作仍只记字段名，没记新旧值: " + "; ".join(offenders))


if __name__ == "__main__":
    unittest.main()
