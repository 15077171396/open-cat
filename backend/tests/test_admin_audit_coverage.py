"""每个 /admin 写路由都必须留下审计行：record_admin_action 是手工调用，无机制保证新端点也调用，
本仓库曾整域缺审计。判定方式：展开真实 app 路由树（FastAPI 0.139 延迟展开须用 _walk），
取 handler 直接 await 的 services 函数，断言其源码引用 record_admin_action 或在 AUDIT_EXEMPT 显式豁免。
已知局限：只看 handler 直接 await 的那一层；只判断「有没有留痕」不判断内容是否够用。"""

import inspect
import re
import unittest

from app.main import app
from tests.test_admin_route_guards import _walk

# 刻意豁免：不是「内容/权益变更」的辅助调用，或审计由同请求内的其它函数负责
AUDIT_EXEMPT = {
    # 缓存失效函数：本身不写业务数据，同一请求里 save_settings / 内容写函数已记审计
    "app.services.setting_service.invalidate_public_settings_cache",
    "app.services.setting_service.invalidate_legal_documents_cache",
    "app.services.ai_config_service.invalidate_ai_config_cache",
    # 翻译任务：只把译文回给编辑器，落库仍走教程/章节的 content_update（那里有审计）
    "app.services.translate_service.submit_translate_task",
    # AI 写作执行流（POST /ai/writing/run）：生成结果只回给编辑器，正文/配图落库仍走
    # 教程/章节/百科的内容写接口（那里已有审计）。这里涉及的全是额度与用量计数，
    # 属于「计量」而不是「内容/权益变更」，逐次生成写审计行只会淹没真正的配置变更；
    # 每次生成的消耗已由 ai_usage 的写作操作计数与用量看板负责。
    "app.services.ai_usage_service.reserve_quota",
    "app.services.ai_usage_service.release_quota",
    "app.services.ai_usage_service.reserve_image_quota",
    "app.services.ai_usage_service.release_image_quota",
    "app.services.ai_usage_service.record_writing_operation",
    "app.services.ai_usage_service.get_usage_summary",
    "app.services.ai_usage_service.get_quota_config",
    "app.services.ai_writing.service.check_hourly_limit",
    "app.services.ai_writing.service.is_enabled",
    # 仅同级交换 sort，正文与可见性都不变
    "app.services.tutorial_node_service.admin_move_node",
    # 素材使用计数只是浏览/复用的计量，不改内容与权益；
    # 素材入库本身由上传/导入接口负责，这里不重复写审计。
    "app.services.materials_service.touch_material",
}

# 捕获**完整点号路径**（不是最后一个标识符）。
# ⚠️ 原先的正则只取 `(\w+)\(`，于是 `await comment_service.delete_comment(...)` 解析出
# `delete_comment`，再到**路由模块**命名空间里 `getattr(module, "delete_comment")` ——
# 路由导入的是 `comment_service` 这个模块对象，拿不到函数 → 被判成「不是 service 调用」跳过。
# 结果是守卫对这类写法**结构性不可见**：实测 55 条管理写路由里 14 条看不见，
# 其中 9 条确实没有审计行（RBAC 角色/部门增删改、评论处置与恢复、举报处置、审核复核），
# 而它一直绿（兜底断言只看 `checked > 30`）。现在解析不到的名字一律红灯。
_AWAIT_CALL_RE = re.compile(r"await\s+((?:\w+\.)*\w+)\(")


def _resolve(module, dotted: str):
    """按点号逐级解析；解析不到返回 None（由调用方判为守卫盲区并报错）。"""
    obj = module
    for part in dotted.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def _admin_write_routes():
    for path, route, _deps in _walk(app.router):
        methods = getattr(route, "methods", set()) or set()
        if not methods & {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        if not path.startswith("/api/v1/admin"):
            continue
        yield path, route.endpoint


class AdminWriteAuditCoverageTests(unittest.TestCase):
    def test_every_admin_write_route_records_audit(self):
        missing = []
        unresolved = []
        checked = 0
        total = 0
        for path, handler in _admin_write_routes():
            total += 1
            module = inspect.getmodule(handler)
            src = inspect.getsource(handler)
            # 审计也可以写在路由层（如「保存系统设置」：只有路由知道改了哪些键，
            # 且必须在 db.commit() 之前落审计行）—— 那同样算留痕
            if "record_admin_action" in src:
                continue
            service_calls = []
            local_names = set(inspect.signature(handler).parameters)
            for dotted in _AWAIT_CALL_RE.findall(src):
                root = dotted.split(".")[0]
                if root in local_names:
                    # `await db.commit()` 里的 `db` 是注入的会话参数，不是模块导入
                    continue
                target = _resolve(module, dotted)
                if target is None:
                    unresolved.append(f"{path} -> await {dotted}() 解析不到函数")
                    continue
                if not callable(target):
                    continue
                target_module = getattr(target, "__module__", "") or ""
                if not target_module.startswith("app.services"):
                    continue
                key = f"{target_module}.{target.__qualname__}"
                if key in AUDIT_EXEMPT:
                    continue
                service_calls.append(key)
            if not service_calls:
                continue
            checked += 1
            # 该路由涉及的 service 函数里，至少要有一处真的写审计
            covered = False
            for key in service_calls:
                mod_name, qual = key.rsplit(".", 1)
                obj = getattr(__import__(mod_name, fromlist=["*"]), qual, None)
                try:
                    func_src = inspect.getsource(obj)
                except (OSError, TypeError):
                    continue
                if "record_admin_action" in func_src:
                    covered = True
                    break
            if not covered:
                missing.append(f"{path} -> {', '.join(sorted(set(service_calls)))}")

        # 解析不到 = 守卫又对某类写法瞎了（第五轮审查 R5-03 就是这么发生的），必须红灯
        self.assertEqual(
            unresolved,
            [],
            "这些 await 调用在本守卫里解析不到函数，守卫对它们不可见 —— "
            "请扩展 _AWAIT_CALL_RE / _resolve，而不是放着不管：\n" + "\n".join(unresolved),
        )
        # 覆盖面积本身也要钉住：只解析到个位数说明守卫已失效而不是「项目变干净了」
        self.assertGreaterEqual(
            checked, 30,
            f"只解析到 {checked} 条管理写路由（路由总数 {total}），守卫可能已失效",
        )
        self.assertEqual(
            missing,
            [],
            "以下管理端写路由没有留下审计行（补 record_admin_action，或加进 AUDIT_EXEMPT 并写明理由）：\n"
            + "\n".join(missing),
        )

    def test_exempt_list_is_still_needed(self):
        """豁免表不能变成垃圾场：条目对应的函数若已补审计，就该从表里删掉"""
        still_exempt = []
        for key in sorted(AUDIT_EXEMPT):
            mod_name, qual = key.rsplit(".", 1)
            try:
                obj = getattr(__import__(mod_name, fromlist=["*"]), qual, None)
                func_src = inspect.getsource(obj)
            except (OSError, TypeError, AttributeError, ImportError):
                raise AssertionError(f"豁免条目已失效（找不到该函数）：{key}")
            if "record_admin_action" in func_src:
                still_exempt.append(key)
        self.assertEqual(
            still_exempt,
            [],
            f"这些函数现在已写审计，应从 AUDIT_EXEMPT 移除：{still_exempt}",
        )


if __name__ == "__main__":
    unittest.main()
