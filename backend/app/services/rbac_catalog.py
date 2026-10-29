"""RBAC 权限目录（纯数据模块）：被 rbac_service（运行时）与 alembic 迁移（种子）共同引用，
保证「代码中声明的权限」与「库中登记的权限」一致。
type: menu=菜单/查看级（侧边栏与模块内 GET），button=按钮级（写操作）；
code 命名: {module}:{action}。"""

from typing import NamedTuple


class PermissionDef(NamedTuple):
    code: str
    name: str
    type: str      # menu | button
    module: str
    module_name: str
    sort: int


PERMISSION_CATALOG: list[PermissionDef] = [
    # 数据看板
    PermissionDef("dashboard:view", "看板查看", "menu", "dashboard", "数据看板", 1),

    # 教程管理
    PermissionDef("tutorial:view", "教程查看", "menu", "tutorial", "教程管理", 10),
    PermissionDef("tutorial:create", "新增教程", "button", "tutorial", "教程管理", 11),
    PermissionDef("tutorial:edit", "编辑教程", "button", "tutorial", "教程管理", 12),
    PermissionDef("tutorial:delete", "删除教程", "button", "tutorial", "教程管理", 13),

    # 分类管理
    PermissionDef("category:view", "分类查看", "menu", "category", "分类管理", 60),
    PermissionDef("category:create", "新增分类", "button", "category", "分类管理", 61),
    PermissionDef("category:edit", "编辑分类", "button", "category", "分类管理", 62),
    PermissionDef("category:delete", "删除分类", "button", "category", "分类管理", 63),

    # 公告管理
    PermissionDef("announcement:view", "公告查看", "menu", "announcement", "公告管理", 70),
    PermissionDef("announcement:create", "新增公告", "button", "announcement", "公告管理", 71),
    PermissionDef("announcement:edit", "编辑公告", "button", "announcement", "公告管理", 72),
    PermissionDef("announcement:delete", "删除公告", "button", "announcement", "公告管理", 73),

    # 支付套餐与订单（2026-10-27）：套餐改价直接影响收款，按高危按钮权限管理
    PermissionDef("payment_product:view", "套餐查看", "menu", "payment_product", "充值套餐", 74),
    PermissionDef("payment_product:create", "新增套餐", "button", "payment_product", "充值套餐", 75),
    PermissionDef("payment_product:edit", "编辑套餐", "button", "payment_product", "充值套餐", 76),
    PermissionDef("payment_product:delete", "删除套餐", "button", "payment_product", "充值套餐", 77),
    # 订单只读：不提供改状态/退款按钮（退款走商户后台，避免站内出现第二套资金操作）
    PermissionDef("payment_order:view", "订单查看", "menu", "payment_order", "支付订单", 78),

    # 用户管理
    PermissionDef("user:view", "用户查看", "menu", "user", "用户管理", 80),
    PermissionDef("user:create", "新增用户", "button", "user", "用户管理", 81),
    PermissionDef("user:edit", "编辑用户", "button", "user", "用户管理", 82),
    PermissionDef("user:delete", "删除用户", "button", "user", "用户管理", 83),
    # 恢复已注销用户（清 deleted_at）：与删除成对，但**刻意独立成码** ——
    # 「能删」不等于「能放回来」，恢复等于把访问权还给一个已被处置的账号，
    # 敏感度不低于删除。内置角色里只有 super_admin 拿到（见 SYSTEM_ROLES 派生规则）。
    PermissionDef("user:restore", "恢复用户", "button", "user", "用户管理", 84),

    # 积分流水
    PermissionDef("points:view", "积分流水查看", "menu", "points", "积分流水", 90),

    # 积分兑换码
    PermissionDef("redeem_code:view", "兑换码查看", "menu", "redeem_code", "积分兑换码", 100),
    PermissionDef("redeem_code:create", "生成兑换码", "button", "redeem_code", "积分兑换码", 101),
    PermissionDef("redeem_code:disable", "作废兑换码", "button", "redeem_code", "积分兑换码", 102),

    # 实名审核
    PermissionDef("auth_request:view", "审核列表查看", "menu", "auth_request", "实名审核", 110),
    PermissionDef("auth_request:review", "审核处理", "button", "auth_request", "实名审核", 111),

    # 用户反馈
    PermissionDef("feedback:view", "反馈查看", "menu", "feedback", "用户反馈", 120),
    PermissionDef("feedback:review", "反馈处理", "button", "feedback", "用户反馈", 121),

    # 内容审核（台账 P0-5：机审留痕复核 + 人工举报处理）
    # 只保留一个 menu 级权限（moderation:view）——管理端是同一个「内容审核」页的
    # 两个 Tab，权限树里拆成两个模块反而让授权界面多出一屏。只读角色按
    # ":view" 后缀规则自动获得 report:view，无需额外登记。
    PermissionDef("moderation:view", "机审留痕查看", "menu", "moderation", "内容审核", 124),
    PermissionDef("moderation:review", "机审复核", "button", "moderation", "内容审核", 125),
    PermissionDef("report:view", "举报查看", "button", "moderation", "内容审核", 126),
    PermissionDef("report:handle", "举报处理", "button", "moderation", "内容审核", 127),

    # 内容评论（台账 P1-9；2026-10-12 泛化到教程 + 百科节点）：评论是 UGC，
    # 管理员必须能看见并处置。
    # 只设 view / delete 两个：评论内容不支持编辑（改内容等于篡改用户发言记录），
    # 误删由 restore_comment 恢复，不再单独立权限码。
    PermissionDef("comment:view", "评论查看", "menu", "comment", "内容评论", 122),
    PermissionDef("comment:delete", "评论删除", "button", "comment", "内容评论", 123),

    # 站内消息通知（2026-10-28）：后台向用户发系统通知（全站 / 指定用户）。
    # 只设 view / send：消息是「一次性广播」，没有编辑与删除语义 ——
    # 发出去就已经进了用户收件箱，撤回等于删用户数据，不做。
    # ⚠️ send 是**面向全体用户**的触达能力，与内容编辑不同级：授予时按「谁能代表平台说话」判断。
    PermissionDef("message:view", "消息通知查看", "menu", "message", "消息通知", 128),
    PermissionDef("message:send", "发送系统通知", "button", "message", "消息通知", 129),

    # 系统设置
    PermissionDef("setting:view", "设置查看", "menu", "setting", "系统设置", 130),
    PermissionDef("setting:update", "保存设置", "button", "setting", "系统设置", 131),

    # 登录管理（2026-10-27）：登录方式开关 / 微信凭据 / 短信通道从系统设置抽出的独立模块。
    # 独立成码的动机就是**授权面收窄**：开关登录方式、改短信签名是运营/客服的日常动作，
    # 不该连带拿到 `setting:update`（那份权限同时能改 COS 密钥、支付商户私钥）。
    # 查看级进 viewer（只读管理员要能看清「哪个渠道开着、缺什么」）；
    # 写操作只给显式授权角色 —— 关错渠道 = 一批用户再也登不进来。
    PermissionDef("login_setting:view", "登录管理查看", "menu", "login_setting", "登录管理", 132),
    PermissionDef("login_setting:update", "保存登录配置", "button", "login_setting", "登录管理", 133),

    # 法律文档（2026-10-27）：隐私政策 / 用户协议 / 免责声明从系统设置抽出的独立模块。
    # 对外公示的合规文本，改动通常只由负责人做 → 一律单开权限码，不与系统设置共用。
    PermissionDef("legal_doc:view", "法律文档查看", "menu", "legal_doc", "法律文档", 134),
    PermissionDef("legal_doc:update", "保存法律文档", "button", "legal_doc", "法律文档", 135),

    # 支付与渠道（2026-10-27）：支付开关 / 微信 / 支付宝凭据从系统设置抽出的独立模块。
    # 资金链路配置，敏感度不低于其它密钥类设置；单开权限码后「能配支付渠道」
    # 与「能改 COS 密钥」可以分开授权。写操作只给显式授权角色。
    PermissionDef("payment_setting:view", "支付配置查看", "menu", "payment_setting", "支付与渠道", 136),
    PermissionDef("payment_setting:update", "保存支付配置", "button", "payment_setting", "支付与渠道", 137),

    # AI 模型管理（2026-10-27）：模型/凭据/单价从系统设置迁出，独立成模块。
    # 查看级给 viewer，写操作只给显式授权角色（content_admin 不含系统模块）。
    PermissionDef("ai_model:view", "模型查看", "menu", "ai_model", "模型管理", 138),
    PermissionDef("ai_model:create", "新增模型", "button", "ai_model", "模型管理", 139),
    PermissionDef("ai_model:edit", "编辑模型", "button", "ai_model", "模型管理", 140),
    PermissionDef("ai_model:delete", "删除模型", "button", "ai_model", "模型管理", 141),

    # 角色管理（RBAC）
    PermissionDef("role:view", "角色查看", "menu", "role", "角色管理", 142),
    PermissionDef("role:create", "新增角色", "button", "role", "角色管理", 143),
    PermissionDef("role:edit", "编辑角色", "button", "role", "角色管理", 144),
    PermissionDef("role:delete", "删除角色", "button", "role", "角色管理", 145),

    # 邮件通知（2026-10-27）：SMTP 通道从系统设置抽出，与短信通道同属「通道型配置」。
    # 单独成码的理由：运维改 SMTP 不该连带拿到 COS / 支付密钥的 `setting:update`；
    # 而「发一封测试邮件」是一次对外动作，归到写权限（update）下。
    # sort 取 146/147：138-145 已被模型管理/角色占用，改号需要连带改已种入库的行。
    PermissionDef("mail_setting:view", "邮件通道查看", "menu", "mail_setting", "邮件通知", 146),
    PermissionDef("mail_setting:update", "保存邮件通道", "button", "mail_setting", "邮件通知", 147),

    # 部门管理（RBAC）
    PermissionDef("department:view", "部门查看", "menu", "department", "部门管理", 150),
    PermissionDef("department:create", "新增部门", "button", "department", "部门管理", 151),
    PermissionDef("department:edit", "编辑部门", "button", "department", "部门管理", 152),
    PermissionDef("department:delete", "删除部门", "button", "department", "部门管理", 153),

    # 操作审计（台账 P2-9）：管理端敏感操作的持久化追溯。
    # **只有 view**：审计日志是只读证据 —— 能编辑/删除审计等于没有审计，
    # 因此刻意不提供任何写权限码。
    PermissionDef("audit:view", "操作审计查看", "menu", "audit", "操作审计", 160),

    # 定时任务（2026-10-27）：查看沿用 dashboard:view（运维只读数据同一层级），
    # 这里只加**写**权限 —— 启停 / 改周期 / 立即执行都会改变系统行为。
    # ⚠️ 新增码必须配套补种迁移（0029_seed_scheduler_permission），否则已有部署升级即 403。
    PermissionDef("scheduler:manage", "定时任务管理", "button", "scheduler", "定时任务", 170),
]

# 全部权限码集合（快速校验用）
ALL_PERMISSION_CODES: set[str] = {p.code for p in PERMISSION_CATALOG}

# 各模块的查看级权限码（菜单渲染用）
MENU_PERMISSION_CODES: set[str] = {p.code for p in PERMISSION_CATALOG if p.type == "menu"}


def permissions_grouped_by_module() -> dict[str, dict]:
    """按模块分组（权限分配树渲染用）：{module: {name, menu, buttons: [...]}}"""
    groups: dict[str, dict] = {}
    for p in sorted(PERMISSION_CATALOG, key=lambda x: x.sort):
        g = groups.setdefault(p.module, {"name": p.module_name, "menu": None, "buttons": []})
        if p.type == "menu":
            g["menu"] = {"code": p.code, "name": p.name}
        else:
            g["buttons"].append({"code": p.code, "name": p.name})
    return groups


# 内置角色种子（迁移时写入；is_system=True 不可删除）
SYSTEM_ROLES: list[dict] = [
    {
        "code": "super_admin",
        "name": "超级管理员",
        "description": "拥有全部权限，且校验时直接绕过权限检查（逃逸通道，不可删除）",
        "permissions": sorted(ALL_PERMISSION_CODES),
    },
    {
        "code": "content_admin",
        "name": "内容管理员",
        "description": "教程/分类/公告的内容管理权限，含内容审核，不含用户与系统设置",
        "permissions": sorted(
            code for code in ALL_PERMISSION_CODES
            if code.split(":")[0] in
            {
                "tutorial", "category",
                "announcement", "dashboard",
                # 内容审核归内容侧：机审复核与举报处理都是内容运营动作
                "moderation", "report",
                # 评论是教程内容的附属 UGC，由内容管理员一并治理
                "comment",
            }
        ),
    },
    {
        "code": "viewer",
        "name": "只读管理员",
        "description": "仅可查看各模块数据，不可做任何写操作（**不含操作审计**，见下方注释）",
        # ⚠️ 刻意把 audit:view 从「所有 :view 权限」里排除：审计日志记录的是
        # 「谁对谁做了什么」，属最敏感的一类只读数据（会暴露正在被调查的对象）。
        # 只读角色默认不应看到它，需要时由超管在角色管理里显式勾选。
        "permissions": sorted(
            code for code in ALL_PERMISSION_CODES
            if code.endswith(":view") and code != "audit:view"
        ),
    },
]
