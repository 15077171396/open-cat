"""模块注册表：**「有哪些模块、默认开不开、由哪些路径与菜单承载」的唯一声明处**。

为什么需要它
------------
开源版允许使用者按需裁剪（支付、积分、实名这类模块对多数内容站用不上）。
在没有注册表时，「关掉一个模块」要同时改四处：`api/admin.py` 的 import、
`core/rbac.py` 的规则表、`admin_menu.py` 的菜单项、前端路由——漏一处就是
「菜单还在但接口 403」或「接口能被调用但没有任何入口」。

现在的分工是：
- **权限码**仍由 `services/rbac_catalog.py` 声明（80 个，含按钮级）；
  本表只引用其模块前缀，不重复定义码。
- **菜单结构**仍由 `services/admin_menu.py` 声明（分组/顺序/图标）；
  本表只登记「哪个菜单项属于哪个模块」，用于过滤。
- **开关**由 `ENABLED_MODULES` 决定；`core/rbac.py` 与 `admin_menus` 分别据此拒绝
  请求、过滤菜单，两处都从本表读，不会各自漂移。

⚠️ 新增模块的三步（`tests/test_module_registry.py` 会检查前两步）：
1. 在 `MODULES` 里加一条 `ModuleDef`，写清路径前缀与菜单 key；
2. 在 `rbac_catalog.py` 里加对应的权限码；
3. 若是默认关闭的，把 key 写进文档的「模块开关表」。
"""

from __future__ import annotations

from typing import NamedTuple

from app.config import settings


class ModuleDef(NamedTuple):
    """一个可开关的功能模块。

    :param key: 模块标识，同时是 `ENABLED_MODULES` 里书写的值
    :param label: 中文名（用于报错文案与文档）
    :param default_enabled: 未显式配置时是否启用
    :param path_prefixes: 该模块的接口路径前缀（含 `/api/v1`，用于按段匹配）
    :param menu_keys: 该模块在 `admin_menu.MENU_GROUPS` 里的菜单 key
    :param always_on: 核心模块，配置里写不写都启用（关掉它后台就没法登录了）
    """

    key: str
    label: str
    default_enabled: bool
    path_prefixes: tuple[str, ...]
    menu_keys: tuple[str, ...]
    always_on: bool = False


MODULES: tuple[ModuleDef, ...] = (
    # ---------- 核心（不可关闭） ----------
    ModuleDef(
        key="core",
        label="核心（登录 / 权限 / 审计 / 系统设置）",
        default_enabled=True,
        path_prefixes=(
            "/api/v1/admin/users",
            # 导出用户属于核心能力：关掉用户模块等于关掉后台本身
            "/api/v1/admin/export/users",
            "/api/v1/admin/rbac",
            "/api/v1/admin/audit-logs",
            "/api/v1/admin/menus",
            "/api/v1/admin/settings",
            "/api/v1/admin/login-settings",
            "/api/v1/admin/mail-settings",
            "/api/v1/admin/legal-documents",
        ),
        menu_keys=(
            "/users", "/roles", "/departments", "/audit-logs",
            "/settings", "/login-settings", "/mail-settings", "/legal-documents",
            "/profile",
        ),
        always_on=True,
    ),
    # ---------- 内容 ----------
    ModuleDef(
        key="tutorial",
        label="教程",
        default_enabled=True,
        path_prefixes=("/api/v1/admin/tutorials", "/api/v1/admin/export/tutorials"),
        menu_keys=("/tutorials",),
    ),
    ModuleDef(
        key="category",
        label="分类",
        default_enabled=True,
        path_prefixes=("/api/v1/categories",),
        menu_keys=("/categories",),
    ),
    ModuleDef(
        key="materials",
        label="素材库与图库检索",
        default_enabled=True,
        # 图库检索只在素材库与编辑器里用，跟着素材库一起开关
        path_prefixes=("/api/v1/admin/materials", "/api/v1/admin/image-search"),
        menu_keys=("/materials",),
    ),
    # ---------- 运营 ----------
    ModuleDef(
        key="ops",
        label="看板与运维（数据看板 / 系统状态 / 定时任务 / 搜索词）",
        default_enabled=True,
        path_prefixes=(
            "/api/v1/admin/dashboard",
            "/api/v1/admin/ai-usage",
            "/api/v1/admin/funnel",
            "/api/v1/admin/content-attribution",
            "/api/v1/admin/system-status",
            "/api/v1/admin/readiness",
            "/api/v1/admin/scheduled-jobs",
            "/api/v1/admin/search-keywords",
        ),
        menu_keys=("/", "/search-keywords", "/scheduled-jobs", "/readiness"),
    ),
    ModuleDef(
        key="announcement",
        label="公告",
        default_enabled=True,
        path_prefixes=("/api/v1/admin/announcements",),
        menu_keys=("/announcements",),
    ),
    ModuleDef(
        key="moderation",
        label="内容审核 / 评论 / 举报 / 反馈",
        default_enabled=True,
        path_prefixes=(
            "/api/v1/admin/moderation",
            "/api/v1/admin/reports",
            "/api/v1/admin/comments",
            "/api/v1/admin/feedbacks",
        ),
        menu_keys=("/moderation", "/comments", "/feedback"),
    ),
    ModuleDef(
        key="message",
        label="站内消息通知",
        default_enabled=True,
        path_prefixes=("/api/v1/admin/messages",),
        menu_keys=("/messages",),
    ),
    ModuleDef(
        key="ai",
        label="AI 模型与 AI 写作",
        default_enabled=True,
        path_prefixes=("/api/v1/admin/ai-models", "/api/v1/admin/ai/writing"),
        menu_keys=("/ai-models", "/ai-writing"),
    ),
    # ---------- 商业化（默认关闭：内容站多数用不上） ----------
    ModuleDef(
        key="payment",
        label="支付套餐 / 订单 / 支付渠道",
        default_enabled=False,
        path_prefixes=(
            "/api/v1/admin/payment-products",
            "/api/v1/admin/payment-orders",
            "/api/v1/admin/payment-settings",
        ),
        menu_keys=("/payment-products", "/payment-orders", "/payment-settings"),
    ),
    ModuleDef(
        key="points",
        label="积分流水 / 兑换码",
        default_enabled=False,
        path_prefixes=(
            "/api/v1/admin/points",
            "/api/v1/admin/redemption-codes",
            # 关掉积分模块时，流水数据也不该被导出
            "/api/v1/admin/export/point-transactions",
        ),
        menu_keys=("/points", "/redeem-codes"),
    ),
    ModuleDef(
        key="auth_request",
        label="实名审核",
        default_enabled=False,
        path_prefixes=("/api/v1/admin/auth-requests",),
        menu_keys=("/auth-requests",),
    ),
    # ---------- 内容辅助（随内容模块，默认开） ----------
    ModuleDef(
        key="content_helper",
        label="内容辅助（第三方导入 / 翻译）",
        default_enabled=True,
        path_prefixes=(
            "/api/v1/admin/translate-content",
            "/api/v1/admin/fetch-resource",
        ),
        # 没有独立菜单：从内容编辑页里调用
        menu_keys=(),
    ),
)

_BY_KEY: dict[str, ModuleDef] = {m.key: m for m in MODULES}

#: 菜单 key → 模块 key（过滤菜单用；未登记视为 core，避免误删入口）
MENU_MODULE: dict[str, str] = {
    menu_key: module.key for module in MODULES for menu_key in module.menu_keys
}


def _parse_enabled() -> frozenset[str]:
    """解析 `ENABLED_MODULES`。

    空字符串 = 按各模块的 `default_enabled`（开源默认）；
    非空 = 显式白名单，此时**只启用列出的模块**（加 always_on）。
    这样「裁剪」与「默认」两种意图都表达得出来，且不需要额外的 DISABLED 变量。
    """
    raw = (settings.ENABLED_MODULES or "").strip()
    always = {m.key for m in MODULES if m.always_on}

    if not raw:
        return frozenset(always | {m.key for m in MODULES if m.default_enabled})

    wanted = {part.strip().lower() for part in raw.split(",") if part.strip()}
    unknown = wanted - set(_BY_KEY)
    if unknown:
        # 拼错的模块名不能静默忽略：否则「配了但要的模块没开」只能靠点页面发现
        raise RuntimeError(
            f"ENABLED_MODULES 里有未知模块: {sorted(unknown)}；"
            f"可选值: {sorted(_BY_KEY)}"
        )
    return frozenset(always | wanted)


_enabled_cache: frozenset[str] | None = None


def enabled_keys() -> frozenset[str]:
    global _enabled_cache
    if _enabled_cache is None:
        _enabled_cache = _parse_enabled()
    return _enabled_cache


def reset_cache() -> None:
    """测试用：改完 settings 后清缓存。"""
    global _enabled_cache
    _enabled_cache = None


def is_enabled(key: str) -> bool:
    return key in enabled_keys()


def module_for_path(path: str) -> ModuleDef | None:
    """按路径前缀找所属模块。

    必须按**段边界**匹配：`/api/v1/admin/payment-products`.startswith("/api/v1/admin/payment")
    为真，直接 startswith 会把支付套餐误判成支付模块之外的东西（或反之）。
    """
    best: ModuleDef | None = None
    best_len = -1
    for module in MODULES:
        for prefix in module.path_prefixes:
            if path == prefix or path.startswith(prefix + "/"):
                if len(prefix) > best_len:
                    best, best_len = module, len(prefix)
    return best


def is_path_enabled(path: str) -> bool:
    """未登记路径一律放行（由 `core/rbac.py` 的规则表负责 fail-closed）。"""
    module = module_for_path(path)
    return True if module is None else is_enabled(module.key)


def menu_visible(menu_key: str) -> bool:
    module_key = MENU_MODULE.get(menu_key)
    return True if module_key is None else is_enabled(module_key)


def enabled_modules() -> tuple[ModuleDef, ...]:
    return tuple(m for m in MODULES if is_enabled(m.key))


def disabled_modules() -> tuple[ModuleDef, ...]:
    return tuple(m for m in MODULES if not is_enabled(m.key))
