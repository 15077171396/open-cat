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

from typing import NamedTuple


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


# ⚠️ 分组顺序 = 侧边栏展示顺序。按「日常使用频率」排：
# 概览 → 内容（最高频） → 运营 → 用户与权限 → 系统（最低频，压在最下面）
MENU_GROUPS: tuple[MenuGroup, ...] = (
    MenuGroup(
        key="overview",
        label="概览",
        icon="home",
        items=(
            MenuItem("/", "数据看板", "dashboard", perm="dashboard:view"),
            # 访问统计是自建 Umami，独立域名，新窗口打开
            MenuItem("umami-stats", "访问统计", "chart", perm="dashboard:view", url="https://tj.wdmao.cn/"),
        ),
    ),
    MenuGroup(
        key="content",
        label="内容管理",
        icon="file-text",
        items=(
            MenuItem("/tutorials", "教程管理", "book", perm="tutorial:view"),
            MenuItem("/resources", "资源管理", "hdd", perm="resource:view"),
            MenuItem("/nodes", "知识百科", "appstore", perm="node:view"),
            MenuItem("/tools", "在线工具", "tool", perm="tool:view"),
            MenuItem("/plugins", "扩展管理", "api", perm="plugin:view"),
            MenuItem("/categories", "分类管理", "tags", perm="category:view"),
            # 素材库：上传/图库导入/AI 图的统一浏览与复用。任一内容模块可编辑即可进入。
            MenuItem(
                "/materials", "素材库", "picture",
                perms=("tutorial:view", "resource:view", "plugin:view", "node:view"),
            ),
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
        ),
    ),
    MenuGroup(
        key="system",
        label="系统",
        icon="control",
        items=(
            MenuItem("/settings", "系统设置", "setting", perm="setting:view"),
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


def _item_to_dict(item: MenuItem) -> dict:
    """叶子节点 → 接口响应（省略空字段，避免前端拿到一堆 null）"""
    data: dict = {"key": item.key, "label": item.label, "icon": item.icon}
    if item.perm:
        data["perm"] = item.perm
    if item.perms:
        data["perms"] = list(item.perms)
    if item.url:
        data["url"] = item.url
    return data


def menu_tree(only_enabled: bool = True) -> dict:
    """菜单树（GET /admin/menus 的 data 部分）。

    :param only_enabled: 按 `module_registry` 的模块开关过滤。
        关掉的模块不该留下入口——否则点进去只会拿到「模块未启用」的 403。
        空分组会被整组丢弃，避免侧边栏出现只有标题的折叠块。
    """
    from app import module_registry

    def keeps(item: MenuItem) -> bool:
        return not only_enabled or module_registry.menu_visible(item.key)

    groups = []
    for group in MENU_GROUPS:
        items = [item for item in group.items if keeps(item)]
        if not items:  # 整组被裁光时不渲染
            continue
        groups.append(
            {
                "key": group.key,
                "label": group.label,
                "icon": group.icon,
                "items": [_item_to_dict(item) for item in items],
            }
        )

    return {
        "groups": groups,
        "footer": [_item_to_dict(item) for item in MENU_FOOTER if keeps(item)],
    }


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
