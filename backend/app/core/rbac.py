"""RBAC 路由权限校验：规则表 + 依赖注入。

规则表显式枚举 (method, path 正则) → 权限码（any-of 命中即放行，空列表 = 仅需 admin 门槛）；
未匹配规则的管理端请求一律 403（fail-closed，新增路由必须同步登记）；
super_admin 绕过全部细粒度检查；/admin 前缀之外的端点由 require_permission 工厂显式声明。
"""

import re

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.core.exceptions import PermissionDeniedError
from app.models.user import User
from app import module_registry
from app.services import rbac_service

# 路径参数占位：OpenAPI 模板 {xxx} → 段级通配
_PARAM = r"[^/]+"

# 内容编辑辅助端点（第三方导入/翻译）：任一内容模块编辑权限即可使用
_CONTENT_EDIT_ANY = ["tutorial:edit"]

# 规则条目：(method, path 模板, 所需权限码列表)
# path 模板使用 OpenAPI 风格 {param} 占位，匹配时转为正则。
_RULES: list[tuple[str, str, list[str]]] = [
    # 数据看板
    ("GET", "/api/v1/admin/dashboard", ["dashboard:view"]),
    # AI 用量属看板类只读数据，复用 dashboard:view，不新增权限码——
    # 新增码需配套「补种给已有内置角色」的迁移，否则已有部署上线即 403
    ("GET", "/api/v1/admin/ai-usage", ["dashboard:view"]),
    # 系统状态（依赖探活/库版本/备份年龄/任务心跳）同样只读，复用 dashboard:view
    ("GET", "/api/v1/admin/system-status", ["dashboard:view"]),
    # 版本与升级（当前版本 / 迁移待执行数 / 最新 release）只读运维，同样复用 dashboard:view
    ("GET", "/api/v1/admin/version", ["dashboard:view"]),
    # 备份管理：列表只给元数据（setting:view）；下载是全库数据外带，要求 setting:update。
    # 刻意不新建 backup:* 权限码：新码要配套补种迁移，而这两档权限已经覆盖「可信运维」边界。
    ("GET", "/api/v1/admin/backups", ["setting:view"]),
    ("GET", "/api/v1/admin/backups/{name}/download", ["setting:update"]),
    # 部署体检（存储/站点地址/邮件/短信/AI/支付是否真的可用）：运维只读，同上
    ("GET", "/api/v1/admin/readiness", ["dashboard:view"]),
    # 顶栏待办聚合：任何管理员都能看到铃铛，具体类别由服务层按权限裁剪
    # （无权限的类别直接不出现，而不是显示一个点进去 403 的数字）
    ("GET", "/api/v1/admin/todos", []),
    # 统一回收站：汇总只返回有查看权限的类型；列表/恢复按类型显式登记权限。
    # 恢复复用「能删才放回」口径（tutorial:delete / comment:delete / user:restore）
    ("GET", "/api/v1/admin/recycle-bin/summary", []),
    ("GET", "/api/v1/admin/recycle-bin/tutorials", ["tutorial:view"]),
    ("POST", "/api/v1/admin/recycle-bin/tutorials/restore", ["tutorial:delete"]),
    ("GET", "/api/v1/admin/recycle-bin/comments", ["comment:view"]),
    ("POST", "/api/v1/admin/recycle-bin/comments/restore", ["comment:delete"]),
    ("GET", "/api/v1/admin/recycle-bin/users", ["user:view"]),
    ("POST", "/api/v1/admin/recycle-bin/users/restore", ["user:restore"]),

    # 列表导出：权限与各自的列表页**完全一致** —— 能看见就能导出，
    # 不多给一个 export:xxx 权限码（新码必须配「补种给已有角色」的迁移，
    # 且「能看不能导」在实践中没有真实需求，只会多一层配置负担）。
    # 导出本身是数据外带，所以每个端点都会写审计（见 admin_export._csv_response）。
    ("GET", "/api/v1/admin/export/users", ["user:view"]),
    ("GET", "/api/v1/admin/export/tutorials", ["tutorial:view"]),
    ("GET", "/api/v1/admin/export/point-transactions", ["points:view"]),
    # 定时任务运行状态（周期/上次执行/是否超期）：运维只读数据，同样是 dashboard:view。
    # 刻意不新建 scheduler:view —— 新码必须配套「补种给已有内置角色」的迁移，
    # 而这里的信息量与「系统状态」卡完全同一层级
    ("GET", "/api/v1/admin/scheduled-jobs", ["dashboard:view"]),
    # 写操作单开 scheduler:manage（启停/改周期会静默改变全站维护行为，不能给「只读运维」顺手做）
    ("PUT", "/api/v1/admin/scheduled-jobs/{job_id}", ["scheduler:manage"]),
    ("POST", "/api/v1/admin/scheduled-jobs/{job_id}/run", ["scheduler:manage"]),
    # 转化漏斗与留存：看板类只读数据，同样复用 dashboard:view
    ("GET", "/api/v1/admin/funnel", ["dashboard:view"]),
    # 搜索词报告：运营选题用，只读，复用 dashboard:view（不新增权限码）
    ("GET", "/api/v1/admin/search-keywords", ["dashboard:view"]),
    # 内容归因榜：同上，只读看板数据
    ("GET", "/api/v1/admin/content-attribution", ["dashboard:view"]),

    # 实名审核
    ("GET", "/api/v1/admin/auth-requests", ["auth_request:view"]),
    ("PUT", "/api/v1/admin/auth-requests/{auth_request_id}/approve", ["auth_request:review"]),
    ("PUT", "/api/v1/admin/auth-requests/{auth_request_id}/reject", ["auth_request:review"]),

    # 教程管理（含章节）
    ("GET", "/api/v1/admin/tutorials", ["tutorial:view"]),
    ("POST", "/api/v1/admin/tutorials", ["tutorial:create"]),
    ("POST", "/api/v1/admin/tutorials/batch-delete", ["tutorial:delete"]),
    # 回收站恢复复用 :delete —— 「能删」才「能放回来」。
    # 刻意不新建 tutorial:restore 权限码：新码必须配套「补种给已有内置角色」的迁移，
    # 否则老部署升级后管理员点恢复直接 403（与 user:restore 那次同样的坑）。
    ("POST", "/api/v1/admin/tutorials/batch-restore", ["tutorial:delete"]),
    ("POST", "/api/v1/admin/tutorials/{tutorial_id}/restore", ["tutorial:delete"]),
    ("POST", "/api/v1/admin/tutorials/batch-publish", ["tutorial:edit"]),
    ("GET", "/api/v1/admin/tutorials/{tutorial_id}", ["tutorial:view"]),
    ("PUT", "/api/v1/admin/tutorials/{tutorial_id}", ["tutorial:edit"]),
    ("PATCH", "/api/v1/admin/tutorials/{tutorial_id}/publish", ["tutorial:edit"]),
    ("DELETE", "/api/v1/admin/tutorials/{tutorial_id}", ["tutorial:delete"]),
    ("GET", "/api/v1/admin/tutorials/{tutorial_id}/nodes", ["tutorial:view"]),
    ("POST", "/api/v1/admin/tutorials/{tutorial_id}/nodes", ["tutorial:edit"]),
    ("PUT", "/api/v1/admin/tutorials/{tutorial_id}/nodes/{node_id}", ["tutorial:edit"]),
    ("POST", "/api/v1/admin/tutorials/{tutorial_id}/nodes/{node_id}/move", ["tutorial:edit"]),
    ("DELETE", "/api/v1/admin/tutorials/{tutorial_id}/nodes/{node_id}", ["tutorial:edit"]),

    # 内容编辑辅助（第三方资料导入 / AI 翻译）
    ("GET", "/api/v1/admin/fetch-resource", _CONTENT_EDIT_ANY),
    ("POST", "/api/v1/admin/translate-content", _CONTENT_EDIT_ANY),
    ("GET", "/api/v1/admin/translate-content/{task_id}", _CONTENT_EDIT_ANY),

    # 分类分组（分类页左下栏）：权限码复用 category:*，理由见 api/admin_category_groups.py
    # ⚠️ reorder 必须排在 /{group_id} 之前：{group_id} 的通配正则会同时匹配 "reorder"
    ("GET", "/api/v1/admin/category-groups", ["category:view"]),
    ("POST", "/api/v1/admin/category-groups", ["category:create"]),
    ("PUT", "/api/v1/admin/category-groups/reorder", ["category:edit"]),
    ("PUT", "/api/v1/admin/category-groups/{group_id}", ["category:edit"]),
    ("DELETE", "/api/v1/admin/category-groups/{group_id}", ["category:delete"]),
    # 把分类移入/移出分组
    ("PUT", "/api/v1/admin/categories/{category_id}/group", ["category:edit"]),

    # 用户管理
    ("GET", "/api/v1/admin/users", ["user:view"]),
    ("POST", "/api/v1/admin/users", ["user:create"]),
    ("POST", "/api/v1/admin/users/batch-delete", ["user:delete"]),
    ("GET", "/api/v1/admin/users/{user_id}", ["user:view"]),
    ("PUT", "/api/v1/admin/users/{user_id}", ["user:edit"]),
    ("GET", "/api/v1/admin/users/{user_id}/points", ["user:view"]),
    ("DELETE", "/api/v1/admin/users/{user_id}", ["user:delete"]),
    # 恢复已注销账号：与删除成对，但独立成码（「能删」不等于「能放回来」）
    ("POST", "/api/v1/admin/users/{user_id}/restore", ["user:restore"]),
    # 用户 RBAC 角色与部门分配
    ("PUT", "/api/v1/admin/users/{user_id}/rbac", ["user:edit"]),
    # 强制下线：让别人立刻失去访问权，敏感度与「改角色」同级，复用 user:edit
    # （新码必须配套「补种给已有内置角色」的迁移，这里没有额外信息量值得新开一码）
    ("POST", "/api/v1/admin/users/{user_id}/force-logout", ["user:edit"]),

    # 登录设备：都是「操作自己名下的会话」，空权限列表 = 仅需 admin 门槛。
    # ⚠️ 必须登记：规则表 fail-closed，漏登记会让这三个端点对所有人 403
    ("GET", "/api/v1/admin/sessions", []),
    ("DELETE", "/api/v1/admin/sessions", []),
    ("DELETE", "/api/v1/admin/sessions/{sid}", []),

    # 积分流水
    ("GET", "/api/v1/admin/points/transactions", ["points:view"]),

    # 积分兑换码
    ("GET", "/api/v1/admin/redemption-codes", ["redeem_code:view"]),
    ("POST", "/api/v1/admin/redemption-codes", ["redeem_code:create"]),
    ("POST", "/api/v1/admin/redemption-codes/batch-disable", ["redeem_code:disable"]),
    ("GET", "/api/v1/admin/redemption-codes/{code_id}/records", ["redeem_code:view"]),
    ("PATCH", "/api/v1/admin/redemption-codes/{code_id}/disable", ["redeem_code:disable"]),

    # 公告管理
    ("GET", "/api/v1/admin/announcements", ["announcement:view"]),
    ("POST", "/api/v1/admin/announcements", ["announcement:create"]),
    ("POST", "/api/v1/admin/announcements/batch-delete", ["announcement:delete"]),
    ("POST", "/api/v1/admin/announcements/batch-publish", ["announcement:edit"]),
    ("PUT", "/api/v1/admin/announcements/{announcement_id}", ["announcement:edit"]),
    ("DELETE", "/api/v1/admin/announcements/{announcement_id}", ["announcement:delete"]),

    # 用户反馈
    ("GET", "/api/v1/admin/feedbacks", ["feedback:view"]),
    ("PUT", "/api/v1/admin/feedbacks/{feedback_id}", ["feedback:review"]),
    ("DELETE", "/api/v1/admin/feedbacks/{feedback_id}", ["feedback:review"]),

    # 站内消息通知：查看已发批次 + 发送（发送是面向全体用户的触达，单独成码）
    ("GET", "/api/v1/admin/messages", ["message:view"]),
    ("POST", "/api/v1/admin/messages", ["message:send"]),

    # 内容审核：机审留痕 + 人工举报；举报处理独立成权限码，
    # 便于把「只看不处理」的权限分给审核实习生
    ("GET", "/api/v1/admin/moderation/summary", ["moderation:view", "report:view"]),
    ("GET", "/api/v1/admin/moderation/logs", ["moderation:view"]),
    ("PUT", "/api/v1/admin/moderation/logs/{log_id}", ["moderation:review"]),
    ("GET", "/api/v1/admin/reports", ["report:view"]),
    ("PUT", "/api/v1/admin/reports/{report_id}", ["report:handle"]),

    # 内容评论
    ("GET", "/api/v1/admin/comments", ["comment:view"]),
    ("DELETE", "/api/v1/admin/comments/{comment_id}", ["comment:delete"]),
    ("POST", "/api/v1/admin/comments/{comment_id}/restore", ["comment:delete"]),

    # 系统设置
    ("GET", "/api/v1/admin/settings", ["setting:view"]),
    ("PUT", "/api/v1/admin/settings", ["setting:update"]),

    # 登录管理（从「系统设置 → 登录与短信」抽出）：读写都用独立权限码，
    # 这样「能开关登录方式」与「能改 COS/支付密钥」两件事可以分开授权。
    # 写接口内部还有一层 surface 约束（只接受登录配置键），见 login_settings_service.update
    ("GET", "/api/v1/admin/login-settings", ["login_setting:view"]),
    ("PUT", "/api/v1/admin/login-settings", ["login_setting:update"]),

    # 法律文档（从「系统设置 → 法律文档」抽出）：对外公示的合规文本，
    # 读写独立授权；写接口同样有 surface 约束（只接受三份法务文本）
    ("GET", "/api/v1/admin/legal-documents", ["legal_doc:view"]),
    ("PUT", "/api/v1/admin/legal-documents", ["legal_doc:update"]),

    # 支付与渠道（从「系统设置 → 支付与渠道」抽出）：资金链路配置，
    # 读写独立授权；写接口同样有 surface 约束（只接受 payment_* 键）
    ("GET", "/api/v1/admin/payment-settings", ["payment_setting:view"]),
    ("PUT", "/api/v1/admin/payment-settings", ["payment_setting:update"]),

    # 邮件通知（从「系统设置 → 邮件通知」抽出）：SMTP 通道配置 + 测试发信。
    # 测试发信算写操作 —— 它是一次对外动作，不只是读
    ("GET", "/api/v1/admin/mail-settings", ["mail_setting:view"]),
    ("PUT", "/api/v1/admin/mail-settings", ["mail_setting:update"]),
    ("POST", "/api/v1/admin/mail-settings/test", ["mail_setting:update"]),

    # 支付套餐 / 订单（管理端）
    ("GET", "/api/v1/admin/payment-products", ["payment_product:view"]),
    ("POST", "/api/v1/admin/payment-products", ["payment_product:create"]),
    ("PUT", "/api/v1/admin/payment-products/{product_id}", ["payment_product:edit"]),
    ("DELETE", "/api/v1/admin/payment-products/{product_id}", ["payment_product:delete"]),
    ("GET", "/api/v1/admin/payment-orders", ["payment_order:view"]),
    ("GET", "/api/v1/admin/payment-orders/{order_no}", ["payment_order:view"]),

    # AI 模型管理：列表/编辑需模型权限；options 只暴露非密钥的模型名与展示名，
    # 工具编辑页（tool:edit）也要用，故对任何 admin 开放。
    ("GET", "/api/v1/admin/ai-models/options", []),
    # 成本护栏（2026-10-27 从「系统设置 → AI 成本护栏」并入本模块）：
    # **必须排在 `{model_id}` 之前** —— 匹配按声明顺序、`{param}` 会吃掉字面量路径
    ("GET", "/api/v1/admin/ai-models/cost-settings", ["ai_model:view"]),
    ("PUT", "/api/v1/admin/ai-models/cost-settings", ["ai_model:edit"]),
    ("GET", "/api/v1/admin/ai-models", ["ai_model:view"]),
    ("POST", "/api/v1/admin/ai-models", ["ai_model:create"]),
    ("PUT", "/api/v1/admin/ai-models/{model_id}", ["ai_model:edit"]),
    # 测试连接会用库里的 Key 真的发一次请求（花钱、且可能被上游记账）→ 归写权限
    ("POST", "/api/v1/admin/ai-models/{model_id}/test", ["ai_model:edit"]),
    ("DELETE", "/api/v1/admin/ai-models/{model_id}", ["ai_model:delete"]),

    # 素材库：浏览/复用是内容编辑的配套动作，复用内容编辑权限
    ("GET", "/api/v1/admin/materials", _CONTENT_EDIT_ANY),
    ("GET", "/api/v1/admin/materials/stats", _CONTENT_EDIT_ANY),
    ("POST", "/api/v1/admin/materials/{material_id}/touch", _CONTENT_EDIT_ANY),

    # 图片搜索：检索/导入都是编辑器内的动作，复用内容编辑权限，不新增权限码
    ("GET", "/api/v1/admin/image-search/options", _CONTENT_EDIT_ANY),
    ("GET", "/api/v1/admin/image-search/search", _CONTENT_EDIT_ANY),
    ("POST", "/api/v1/admin/image-search/import", _CONTENT_EDIT_ANY),

    # AI 写作：执行面复用内容编辑权限（写作是编辑器内的动作，不新增权限码）；
    # 提示词配置与模型管理同属「AI 行为配置」，由 ai_model:* 收口
    ("GET", "/api/v1/admin/ai/writing/options", _CONTENT_EDIT_ANY),
    ("POST", "/api/v1/admin/ai/writing/run", _CONTENT_EDIT_ANY),
    ("GET", "/api/v1/admin/ai/writing/prompts", ["ai_model:view"]),
    ("PUT", "/api/v1/admin/ai/writing/prompts/{operation}", ["ai_model:edit"]),
    ("POST", "/api/v1/admin/ai/writing/prompts/{operation}/reset", ["ai_model:edit"]),

    # 管理操作审计：审计日志本身是敏感数据（记录谁对谁做了什么），
    # 刻意不授予「只读管理员」内置角色，默认扩大可见面反而不利
    ("GET", "/api/v1/admin/audit-logs", ["audit:view"]),

    # 侧边菜单（任何 admin 可读）：菜单结构不含业务数据，可见性由前端按权限过滤。
    # 若在这里加权限码，低权限角色将整条菜单链拿不到 → 侧边栏空白。
    ("GET", "/api/v1/admin/menus", []),
    # 数据字典：系统配置层，复用 setting:view / setting:update
    # 监控管理：只读运维数据，复用 dashboard:view
    ("GET", "/api/v1/admin/monitor/online", ["dashboard:view"]),
    ("GET", "/api/v1/admin/monitor/server", ["dashboard:view"]),
    ("GET", "/api/v1/admin/monitor/cache", ["dashboard:view"]),
    ("GET", "/api/v1/admin/dicts", ["setting:view"]),
    ("POST", "/api/v1/admin/dicts", ["setting:update"]),
    ("PUT", "/api/v1/admin/dicts/{type_id}", ["setting:update"]),
    ("DELETE", "/api/v1/admin/dicts/{type_id}", ["setting:update"]),
    ("GET", "/api/v1/admin/dicts/{type_id}/items", ["setting:view"]),
    ("POST", "/api/v1/admin/dicts/{type_id}/items", ["setting:update"]),
    ("PUT", "/api/v1/admin/dicts/{type_id}/items/{item_id}", ["setting:update"]),
    ("DELETE", "/api/v1/admin/dicts/{type_id}/items/{item_id}", ["setting:update"]),
    # 岗位目录：组织架构，复用部门权限
    ("GET", "/api/v1/admin/positions", ["department:view"]),
    ("POST", "/api/v1/admin/positions", ["department:edit"]),
    ("PUT", "/api/v1/admin/positions/{position_id}", ["department:edit"]),
    ("DELETE", "/api/v1/admin/positions/{position_id}", ["department:edit"]),
    # 菜单管理：读取菜单目录用 setting:view，写展示设置用 setting:update。
    # order 必须排在 /{menu_key} 之前，否则会被路径参数通配吞掉（同 category-groups）。
    ("GET", "/api/v1/admin/menu-settings", ["setting:view"]),
    ("PUT", "/api/v1/admin/menu-settings/order", ["setting:update"]),
    ("POST", "/api/v1/admin/menu-settings/reset", ["setting:update"]),
    ("PUT", "/api/v1/admin/menu-settings/item", ["setting:update"]),
    ("DELETE", "/api/v1/admin/menu-settings/item", ["setting:update"]),

    # RBAC：自身档案（任何 admin 可看，前端动态菜单数据源）
    ("GET", "/api/v1/admin/rbac/profile", []),

    # RBAC：权限目录（角色分配界面用，归入角色查看）
    ("GET", "/api/v1/admin/rbac/permissions", ["role:view"]),

    # RBAC：角色管理
    ("GET", "/api/v1/admin/rbac/roles", ["role:view"]),
    ("POST", "/api/v1/admin/rbac/roles", ["role:create"]),
    ("GET", "/api/v1/admin/rbac/roles/{role_id}", ["role:view"]),
    ("PUT", "/api/v1/admin/rbac/roles/{role_id}", ["role:edit"]),
    ("DELETE", "/api/v1/admin/rbac/roles/{role_id}", ["role:delete"]),
    ("GET", "/api/v1/admin/rbac/roles/{role_id}/permissions", ["role:view"]),
    ("PUT", "/api/v1/admin/rbac/roles/{role_id}/permissions", ["role:edit"]),

    # RBAC：部门管理
    ("GET", "/api/v1/admin/rbac/departments", ["department:view"]),
    ("POST", "/api/v1/admin/rbac/departments", ["department:create"]),
    ("PUT", "/api/v1/admin/rbac/departments/{department_id}", ["department:edit"]),
    ("DELETE", "/api/v1/admin/rbac/departments/{department_id}", ["department:delete"]),
]


def _compile(pattern: str) -> re.Pattern:
    """OpenAPI 风格路径模板 → 全匹配正则（{param} → [^/]+）"""
    regex = re.sub(r"\{[^/]+\}", _PARAM, pattern)
    return re.compile(f"^{regex}$")


# 预编译规则（按声明顺序匹配：具体路径在前，含参数路径在后）
_COMPILED_RULES: list[tuple[str, re.Pattern, list[str]]] = [
    (method, _compile(path), perms) for method, path, perms in _RULES
]


def match_route_permission(method: str, path: str) -> list[str] | None:
    """解析 (method, path) 所需权限码；未匹配返回 None（fail-closed 由调用方处理）。
    HEAD 请求按 GET 处理（Starlette GET 路由同时服务 HEAD）。"""
    if method == "HEAD":
        method = "GET"
    for rule_method, pattern, perms in _COMPILED_RULES:
        if rule_method == method and pattern.match(path):
            return perms
    return None


async def _assert_any_permission(db: AsyncSession, user: User, required: list[str]) -> None:
    """校验用户持有任一所需权限；super_admin 直接放行"""
    if not required:
        return
    rbac = await rbac_service.get_user_rbac(db, user.id)
    if await rbac_service.is_super_admin(rbac):
        return
    user_perms = set(rbac.get("perms", []))
    if user_perms & set(required):
        return
    raise PermissionDeniedError(f"缺少权限: {' / '.join(required)}")


async def has_any_permission(db: AsyncSession, user: User, required: list[str]) -> bool:
    """用户是否持有任一所需权限（不抛错版本，与 _assert_any_permission 同一套判断，避免第二份权限口径）"""
    if not required:
        return True
    rbac = await rbac_service.get_user_rbac(db, user.id)
    if await rbac_service.is_super_admin(rbac):
        return True
    return bool(set(rbac.get("perms", [])) & set(required))


async def check_route_permission(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """管理端路由级权限校验依赖（挂在 admin router 的 dependencies 上）：
    匹配且权限码为空 → 仅需 admin 门槛；非空 → any-of 校验；未匹配规则 → fail-closed 403"""
    # 模块开关先于权限判定：被裁掉的模块，其接口对任何人都不可用
    # （含 super_admin），否则「关掉支付」只是把菜单藏了，接口仍可调。
    if not module_registry.is_path_enabled(request.url.path):
        module = module_registry.module_for_path(request.url.path)
        label = module.label if module else "该模块"
        raise PermissionDeniedError(f"{label} 未启用（ENABLED_MODULES 中未包含）")

    required = match_route_permission(request.method, request.url.path)
    if required is None:
        raise PermissionDeniedError(f"该接口未配置访问权限: {request.method} {request.url.path}")
    await _assert_any_permission(db, user, required)


def require_permission(*codes: str):
    """显式权限校验依赖工厂（用于 /admin 前缀之外的管理端点，如分类管理）。
    用法: ``Depends(require_permission("category:create"))`` — any-of 语义。"""
    if not codes:
        raise ValueError("require_permission 至少需要一个权限码")

    async def _checker(
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> User:
        await _assert_any_permission(db, user, list(codes))
        return user

    return _checker
