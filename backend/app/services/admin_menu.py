"""管理端侧边菜单配置（**唯一数据源**）。

前端 `AdminLayout` 通过 `GET /api/v1/admin/menus` 拉取本配置渲染侧边栏：
分组、顺序、图标、权限码全部在此声明 —— **新增/调整后台菜单只改这里，前端零改动**。

约定：
- `key`：SPA 路由路径（以 `/` 开头），必须与 `admin-frontend/src/App.tsx` 的路由一致；
  外链用任意唯一 key + `url`（`url` 非空 = 新窗口打开，不走 SPA 路由）。
  `tests/test_admin_menu.py` 有跨语言守卫（读 App.tsx 比对）。
- `perm` / `perms`：RBAC 权限码。`perm` 为单一码，`perms` 为「任一命中」；
  两者都为空 = 任何已登录管理员可见（如个人中心）。
- `icon`：图标**名字符串**，由前端 `ICON_MAP` 解析成组件
  （React 组件不可 JSON 序列化，故不下发组件本身）。新增图标名必须同步前端映射表，
  守卫测试会比对。
- 菜单结构本身不含业务数据，接口对任何 admin 开放（`rbac.py` 规则表登记为空权限列表）；
  **可见性过滤在前端按 `permissions` 做**，这样权限被撤销后无需重拉菜单即时生效。

层级：目前是「分组（父级）→ 菜单项（子级）」两层。若将来某页需要三级，
把 `MenuItem` 换成带 `children` 的递归结构，并同步前端 `utils/menu.ts` 的构建函数。
"""

from typing import Mapping, NamedTuple

from app.core.i18n import DEFAULT_LOCALE, pick


class MenuItem(NamedTuple):
    """侧边栏叶子节点（一个可点击的页面入口）"""

    key: str
    label: str
    icon: str
    perm: str | None = None
    perms: tuple[str, ...] = ()
    url: str | None = None


class MenuGroup(NamedTuple):
    """侧边栏父级分组（可折叠子菜单）"""

    key: str
    label: str
    icon: str
    items: tuple[MenuItem, ...]


class MenuOverride(NamedTuple):
    """菜单展示层覆盖（存 DB，不改变代码里的路由 / 权限定义）。

    只允许覆盖「怎么显示」：中文名、英文名、图标、是否可见、排序。
    路由与权限码仍由本文件的 MenuItem 决定 —— 后台菜单管理页不提供新建/删除，
    避免出现点进去 404（无路由）或 403（无权限）的死入口。
    """

    label_zh: str | None = None
    label_en: str | None = None
    icon: str | None = None
    visible: bool | None = None
    sort_order: int | None = None


# ⚠️ 分组顺序 = 侧边栏展示顺序。按「日常使用频率」排：
# 概览 → 内容（示例模块） → 运营 → 用户与权限 → 系统（最低频，压在最下面）
MENU_GROUPS: tuple[MenuGroup, ...] = (
    MenuGroup(
        key="overview",
        label="概览",
        icon="home",
        items=(
            MenuItem("/", "数据看板", "dashboard", perm="dashboard:view"),
            # 工作台：快捷入口 + 待办 + 最近动态，复用现有接口
            MenuItem("/workplace", "工作台", "workplace", perm="dashboard:view"),
            # 数据分析：前端本地假数据演示页，不新增接口；权限与数据看板同层级
            MenuItem("/analysis", "数据分析", "chart", perm="dashboard:view"),
        ),
    ),
    MenuGroup(
        key="content",
        label="内容管理",
        icon="file-text",
        items=(
            # 教程是**示例业务模块**：列表、树形章节、富文本编辑、审核、回收站全都在，
            # 新增自己的业务模块时照着它抄一遍即可（见 docs 的开发文档 §五）。
            MenuItem("/tutorials", "教程管理", "book", perm="tutorial:view"),
            MenuItem("/categories", "分类管理", "tags", perm="category:view"),
            # 素材库：上传/图库导入/AI 图的统一浏览与复用，任何后台都用得上。
            MenuItem("/materials", "素材库", "picture", perm="tutorial:view"),
        ),
    ),
    MenuGroup(
        key="operations",
        label="运营管理",
        icon="notification",
        items=(
            MenuItem("/announcements", "公告管理", "sound", perm="announcement:view"),
            # 支付：套餐维护 + 只读订单（退款仍在商户后台操作，站内不提供资金操作）
            MenuItem("/payment-products", "充值套餐", "gift", perm="payment_product:view"),
            MenuItem("/payment-orders", "支付订单", "wallet", perm="payment_order:view"),
            # 机审留痕与人工举报是同一页的两个 Tab，任一查看权限都应显示入口
            MenuItem("/moderation", "内容审核", "audit", perms=("moderation:view", "report:view")),
            MenuItem("/comments", "内容评论", "message", perm="comment:view"),
            MenuItem("/feedback", "用户反馈", "comment", perm="feedback:view"),
            # 站内消息：给用户发系统通知（全站 / 指定用户），消息中心「系统通知」tab 的数据来源
            MenuItem("/messages", "消息通知", "notification", perm="message:view"),
            # 搜索词报告：运营选题来源（零结果词 = 缺哪些内容）。只读，复用 dashboard:view
            MenuItem("/search-keywords", "搜索词分析", "search", perm="dashboard:view"),
        ),
    ),
    MenuGroup(
        key="monitor",
        label="监控管理",
        icon="monitor",
        items=(
            MenuItem("/monitor/online", "在线用户", "online", perm="dashboard:view"),
            MenuItem("/monitor/server", "服务器监控", "server", perm="dashboard:view"),
            MenuItem("/monitor/cache", "缓存监控", "cache", perm="dashboard:view"),
        ),
    ),
    MenuGroup(
        key="users",
        label="用户与权限",
        icon="idcard",
        items=(
            MenuItem("/users", "用户管理", "team", perm="user:view"),
            MenuItem("/auth-requests", "实名审核", "check-circle", perm="auth_request:view"),
            MenuItem("/points", "积分流水", "wallet", perm="points:view"),
            MenuItem("/redeem-codes", "积分兑换码", "gift", perm="redeem_code:view"),
            MenuItem("/roles", "角色管理", "safety", perm="role:view"),
            MenuItem("/departments", "部门管理", "apartment", perm="department:view"),
            MenuItem("/positions", "岗位管理", "position", perm="department:view"),
        ),
    ),
    MenuGroup(
        key="system",
        label="系统",
        icon="control",
        items=(
            MenuItem("/settings", "系统设置", "setting", perm="setting:view"),
            # 菜单管理：只调整展示层（名称/图标/顺序/可见），路由与权限码仍由代码同源定义
            MenuItem("/menu-settings", "菜单管理", "menu", perm="setting:view"),
            # 数据字典：类型 + 字典项，统一维护业务枚举的展示文案
            MenuItem("/dicts", "字典管理", "dict", perm="setting:view"),
            # 登录管理：登录方式开关 / 微信网站应用凭据 / 短信通道
            # （2026-10-27 从「系统设置 → 登录与短信」抽出，独立权限码）
            MenuItem("/login-settings", "登录管理", "login", perm="login_setting:view"),
            # 法律文档：隐私政策 / 用户协议 / 免责声明
            # （2026-10-27 从「系统设置 → 法律文档」抽出，对外合规文本单独授权）
            MenuItem("/legal-documents", "法律文档", "file-protect", perm="legal_doc:view"),
            # 支付与渠道：支付开关 / 微信 / 支付宝凭据
            # （2026-10-27 从「系统设置 → 支付与渠道」抽出，资金链路配置单独授权）
            MenuItem("/payment-settings", "支付与渠道", "credit-card", perm="payment_setting:view"),
            # 邮件通知：SMTP 通道（验证码发信）+ 测试发信
            # （2026-10-27 从「系统设置 → 邮件通知」抽出，与短信同属通道型配置）
            MenuItem("/mail-settings", "邮件通知", "mail", perm="mail_setting:view"),
            # 模型管理：AI 供应商凭据、模型与单价（从系统设置迁出，独立成模块）
            MenuItem("/ai-models", "模型管理", "robot", perm="ai_model:view"),
            # AI 写作：操作级提示词与生成参数，与模型管理同属「AI 行为配置」，复用 ai_model:view
            MenuItem("/ai-writing", "AI 写作", "robot", perm="ai_model:view"),
            # 定时任务：只读运维页（周期/上次执行/是否超期）。
            # 复用 dashboard:view —— 与「数据看板 → 系统状态」卡同一份数据、同一层级权限
            # 部署体检放在定时任务之前：首次部署时先看「还差什么」，再看「任务跑没跑」
            MenuItem("/readiness", "部署体检", "readiness", perm="dashboard:view"),
            MenuItem("/scheduled-jobs", "定时任务", "clock", perm="dashboard:view"),
            # 代码生成：只读表结构，预览 / 下载后端 CRUD 骨架（接入仍需人工登记路由与权限）
            MenuItem("/codegen", "代码生成", "code", perm="setting:update"),
            # 备份管理：只读列表 + 下载；真正的备份/恢复在宿主机脚本执行
            MenuItem("/backups", "备份管理", "database", perm="setting:view"),
            # 回收站：教程 / 评论 / 用户三类软删除记录的统一入口（类型由接口按权限裁剪）
            MenuItem(
                "/recycle-bin", "回收站", "delete",
                perms=("tutorial:view", "comment:view", "user:view"),
            ),
            # 审计与「内容审核」图标刻意区分（同一图标会让两个菜单看起来是同一件事）
            MenuItem("/audit-logs", "操作审计", "file-search", perm="audit:view"),
        ),
    ),
)

# 分组之外的独立项（渲染在分组下方，与分组同级）。
# 个人中心**不带 perm**：任何已登录管理员都能看自己的资料/密码/权限，与业务权限无关。
MENU_FOOTER: tuple[MenuItem, ...] = (
    MenuItem("/profile", "个人中心", "user"),
)


# 英文文案（按 key 索引）。**缺译回退中文**，不会出现空菜单；
# tests/test_i18n.py 断言「每个菜单 key 都有英文」，防止新增菜单时静默漏翻。
MENU_LABELS_EN: dict[str, str] = {
    # 分组
    "overview": "Overview",
    "content": "Content",
    "operations": "Operations",
    "monitor": "Monitoring",
    "users": "Users & Permissions",
    "system": "System",
    # 概览
    "/": "Dashboard",
    "/workplace": "Workplace",
    "/analysis": "Analytics",
    # 内容
    "/tutorials": "Tutorials",
    "/categories": "Categories",
    "/materials": "Media Library",
    # 运营
    "/announcements": "Announcements",
    "/payment-products": "Recharge Plans",
    "/payment-orders": "Payment Orders",
    "/moderation": "Content Review",
    "/comments": "Comments",
    "/feedback": "User Feedback",
    "/messages": "Notifications",
    "/search-keywords": "Search Terms",
    # 监控
    "/monitor/online": "Online Users",
    "/monitor/server": "Server",
    "/monitor/cache": "Cache",
    # 用户与权限
    "/users": "Users",
    "/auth-requests": "Identity Review",
    "/points": "Points Ledger",
    "/redeem-codes": "Redeem Codes",
    "/roles": "Roles",
    "/departments": "Departments",
    "/positions": "Positions",
    # 系统
    "/settings": "Settings",
    "/menu-settings": "Menu Management",
    "/dicts": "Dictionaries",
    "/login-settings": "Login",
    "/legal-documents": "Legal Documents",
    "/payment-settings": "Payment Channels",
    "/mail-settings": "Email",
    "/ai-models": "AI Models",
    "/ai-writing": "AI Writing",
    "/readiness": "Deployment Check",
    "/scheduled-jobs": "Scheduled Jobs",
    "/codegen": "Code Generator",
    "/backups": "Backups",
    "/recycle-bin": "Recycle Bin",
    "/audit-logs": "Audit Log",
    # 分组之外
    "/profile": "Profile",
}


def menu_label(
    key: str,
    default_zh: str,
    locale: str = DEFAULT_LOCALE,
    override: MenuOverride | None = None,
) -> str:
    """菜单文案：DB 覆盖 > 代码中文/英文 > 回退中文（绝不返回空串）"""
    zh = (override.label_zh if override and override.label_zh else default_zh)
    en = override.label_en if override and override.label_en else MENU_LABELS_EN.get(key)
    return pick(locale, zh, en)


def menu_icon(default_icon: str, override: MenuOverride | None = None) -> str:
    """图标名：DB 覆盖优先，否则用代码里的默认图标"""
    return override.icon if override and override.icon else default_icon


def localized_label(key: str, zh: str, locale: str) -> str:
    return menu_label(key, zh, locale, None)


def _item_to_dict(
    item: MenuItem,
    locale: str = DEFAULT_LOCALE,
    override: MenuOverride | None = None,
) -> dict:
    """叶子节点 → 接口响应（省略空字段，避免前端拿到一堆 null）"""
    data: dict = {
        "key": item.key,
        "label": menu_label(item.key, item.label, locale, override),
        "icon": menu_icon(item.icon, override),
    }
    if item.perm:
        data["perm"] = item.perm
    if item.perms:
        data["perms"] = list(item.perms)
    if item.url:
        data["url"] = item.url
    return data


def _sort_key(index: int, override: MenuOverride | None) -> tuple[int, int]:
    order = override.sort_order if override and override.sort_order is not None else index
    return (order, index)


def menu_tree(
    only_enabled: bool = True,
    locale: str = DEFAULT_LOCALE,
    overrides: Mapping[str, MenuOverride] | None = None,
) -> dict:
    """菜单树（GET /admin/menus 的 data 部分）。

    :param only_enabled: 按 `module_registry` 的模块开关过滤。
        关掉的模块不该留下入口——否则点进去只会拿到「模块未启用」的 403。
        空分组会被整组丢弃，避免侧边栏出现只有标题的折叠块。
    :param overrides: 后台「菜单管理」保存的展示层覆盖（名称/图标/可见/排序）。
        不传 = 代码默认配置，测试与脚本行为不变。
    """
    from app import module_registry

    overrides = overrides or {}

    def keeps(item: MenuItem) -> bool:
        return not only_enabled or module_registry.menu_visible(item.key)

    group_rows: list[tuple[tuple[int, int], dict]] = []
    for group_index, group in enumerate(MENU_GROUPS):
        group_override = overrides.get(group.key)
        if group_override and group_override.visible is False:
            continue

        item_rows: list[tuple[tuple[int, int], dict]] = []
        for item_index, item in enumerate(group.items):
            item_override = overrides.get(item.key)
            if item_override and item_override.visible is False:
                continue
            if not keeps(item):
                continue
            item_rows.append(
                (_sort_key(item_index, item_override), _item_to_dict(item, locale, item_override))
            )
        if not item_rows:  # 整组被裁光时不渲染
            continue
        item_rows.sort(key=lambda row: row[0])
        group_rows.append(
            (
                _sort_key(group_index, group_override),
                {
                    "key": group.key,
                    "label": menu_label(group.key, group.label, locale, group_override),
                    "icon": menu_icon(group.icon, group_override),
                    "items": [row[1] for row in item_rows],
                },
            )
        )

    group_rows.sort(key=lambda row: row[0])

    footer_rows: list[tuple[tuple[int, int], dict]] = []
    for item_index, item in enumerate(MENU_FOOTER):
        item_override = overrides.get(item.key)
        if item_override and item_override.visible is False:
            continue
        if not keeps(item):
            continue
        footer_rows.append(
            (_sort_key(item_index, item_override), _item_to_dict(item, locale, item_override))
        )
    footer_rows.sort(key=lambda row: row[0])

    return {
        "groups": [row[1] for row in group_rows],
        "footer": [row[1] for row in footer_rows],
    }


def all_menu_keys() -> set[str]:
    """可被菜单管理覆盖的全部 key（分组 + 叶子 + 页脚）"""
    keys = {group.key for group in MENU_GROUPS}
    keys.update(item.key for item in iter_menu_items())
    return keys


def menu_key_exists(key: str) -> bool:
    return key in all_menu_keys()


def iter_menu_items() -> list[MenuItem]:
    """扁平化全部叶子节点（守卫测试与工具脚本用）"""
    items: list[MenuItem] = []
    for group in MENU_GROUPS:
        items.extend(group.items)
    items.extend(MENU_FOOTER)
    return items


def all_menu_perms() -> set[str]:
    """菜单引用的全部权限码（守卫测试：必须都在 RBAC 权限目录里）"""
    codes: set[str] = set()
    for item in iter_menu_items():
        if item.perm:
            codes.add(item.perm)
        codes.update(item.perms)
    return codes
