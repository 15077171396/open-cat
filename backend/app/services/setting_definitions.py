"""系统设置项的**声明**（第五轮审查 MNT-08 拆分自 setting_service）。

这里只放「有哪些设置项」：数据类型、分组声明、默认值、管理端渲染用的 payload。
「怎么读写 / 怎么校验值 / 怎么缓存」都在 `setting_service`。

拆分动机：原文件 918 行里 500+ 行是纯声明（79 条定义 + 18 个分组 + 3 份法务默认文案），
混着服务逻辑后，「加一个设置项」要在近千行的文件里翻找，且改动范围难以判断。
拆开后：加设置项只动本文件；改校验/缓存只动 service。

⚠️ `setting_service` 仍然**重新导出**这里的所有名字（`SETTING_DEFINITIONS` /
`SETTING_GROUPS` / `definitions_payload` / `groups_payload`），既有 import 路径不变。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# 默认值大多来自 .env（Settings）：定义本身是「默认值从哪来」的声明，
# 因此这里必须能读 settings。反过来 settings 不依赖本模块，无环。
from app.config import settings


@dataclass(frozen=True)
class SettingDefinition:
    """设置项定义"""
    key: str
    label: str
    type: str  # text / textarea / secret_text / image / url / richtext / password / number / json / bool / select
    group: str  # 分组标识（basic / business / ai / moderation / upload / ratelimit / search / seo / fetch / storage / miniapp / mail / hero / default / qrcode / social / invite；登录链路：auth_phone / auth_wechat / auth_sms；支付：payment_general / payment_wechat / payment_alipay；法务：legal）
    group_label: str
    default: str = ""
    # secret=True：密钥类配置三重保护——加密落库、永不进入公开设置、管理端只回显掩码
    secret: bool = False
    # public=False：仅供管理端的运维配置不进「公开设置」（该接口发给所有访客，
    # 只放前台渲染要用的东西）；密钥靠 secret 排除，非密钥运维项（如 AI base_url）靠此标记排除
    public: bool = True
    # select 类型的候选值：[{"value": "reject", "label": "拒绝提交"}, ...]
    options: list[dict] = field(default_factory=list)
    # 输入框下方的补充说明（单位、取值范围、生效时机等）
    help_text: str = ""


# 法律文档默认内容：管理端未配置时的兜底文案
_LEGAL_PRIVACY_DEFAULT = """<h2>隐私政策</h2><p>我们非常重视您的个人隐私。本政策说明我们在您使用平台服务时如何收集、使用、存储和保护您的个人信息。</p><h3>信息收集</h3><p>在您注册账号、浏览内容、下载资源或参与互动时，我们可能收集您的账号信息、浏览记录及必要的设备信息。</p><h3>信息使用</h3><p>收集的信息仅用于提供与改进平台服务，包括内容推荐、账号安全验证与使用统计分析。</p><h3>信息保护</h3><p>我们采取加密存储、访问控制等安全措施保护您的信息，不会向任何第三方出售或非法提供您的个人信息。</p><h3>政策更新</h3><p>我们可能适时修订本政策，修订后的内容将在本页面发布并即时生效。</p>"""

_LEGAL_AGREEMENT_DEFAULT = """<h2>用户协议</h2><p>欢迎使用本平台。在使用平台服务前，请您仔细阅读并充分理解本协议的全部内容。</p><h3>账号规则</h3><p>您应妥善保管账号与密码，并对账号下的全部操作行为负责。发现账号被盗用时应立即联系我们。</p><h3>使用规范</h3><p>您承诺不利用平台从事任何违反法律法规或损害他人合法权益的活动，不干扰平台的正常运行。</p><h3>内容说明</h3><p>平台内的教程、资源等内容仅供学习交流使用，请尊重内容创作者的知识产权，未经授权不得商用或传播。</p><h3>协议变更</h3><p>我们有权根据业务发展需要修订本协议，修订后的协议将在本页面发布并即时生效。</p>"""

_LEGAL_DISCLAIMER_DEFAULT = """<h2>免责声明</h2><p>在使用本平台服务前，请您仔细阅读以下免责声明。</p><h3>内容来源</h3><p>平台内的教程、资源与工具信息部分来源于公开渠道整理，我们尽力保证其准确性，但不对内容的完整性和时效性作任何明示或默示的保证。</p><h3>风险提示</h3><p>您在使用平台提供的第三方工具、下载资源时应自行判断并承担相应风险，由此造成的损失平台不承担责任。</p><h3>服务变更</h3><p>平台可能不定期调整、中止或终止部分服务内容，届时将尽可能提前公告，但不承担由此造成的间接损失。</p><h3>法律适用</h3><p>本声明受中华人民共和国法律管辖。若您对声明内容有任何疑问，欢迎通过页面提供的方式联系我们。</p>"""


#: 设置项归属的「管理界面」（surface）。
#:
#: - `settings`：通行于「系统 → 系统设置」的通用分组（默认值）；
#: - `login`：登录链路专用配置，由「系统 → 登录管理」独立页面承载；
#: - `legal`：全站公示的三份法律文本，由「系统 → 法律文档」独立页面承载；
#: - `payment`：支付开关与渠道凭据，由「系统 → 支付与渠道」独立页面承载；
#: - `model`：AI 成本护栏（全站限额 + 兜底单价），由「系统 → 模型管理」承载；
#: - `mail`：SMTP 邮件通道（验证码发信），由「系统 → 邮件通知」承载。
#:
#: 为什么用声明而不是在白名单里写死分组 key：抽出模块（登录管理 / 法律文档 / 支付渠道 /
#: 模型管理都是先例）时，最容易出的错是「新页面加了、老页面没减」，同一批开关出现在两个
#: tab 里，两边各存一次、互相覆盖也没人发现。归属写在定义旁边，读写两侧按同一个 surface
#: 过滤，想再抽下一个模块只改一行 `surface=`。
#:
#: `model` 是**反向收拢**的先例：模型管理抽出时只带走了模型与单模型单价，把「全站日/月
#: 调用上限 + 兜底单价」留在了系统设置。结果同一件成本问题被劈成两半 —— 调模型单价时
#: 看不到全站限额，改兜底单价时看不到有哪些模型已单独定价。这里把它归到模型管理，
#: 页面顶部的用量/剩余额度与实际生效配置同源。
SURFACE_SETTINGS = "settings"
SURFACE_LOGIN = "login"
SURFACE_LEGAL = "legal"
SURFACE_PAYMENT = "payment"
SURFACE_MODEL = "model"
SURFACE_MAIL = "mail"


@dataclass(frozen=True)
class SettingGroup:
    """设置分组：**顺序即后台 tab 顺序**，说明文案由后端下发。

    收敛前这份「分组顺序 + 分组说明」只存在于管理端的 `GROUP_ORDER` /
    `GROUP_DESCRIPTIONS` 两个常量里，而 tab 渲染是
    `GROUP_ORDER.filter(g => map.has(g))` —— 后端新增一个 group 但没同步改前端时，
    **整组设置会被静默丢弃**：接口返回了、前端不渲染、也没有任何报错。
    现在分组由后端声明，前端按返回顺序渲染，并把未知分组追加在末尾（宁可位置难看，不可丢失）。
    """

    key: str
    label: str
    description: str = ""
    #: 归属的管理界面（见 SURFACE_SETTINGS / SURFACE_LOGIN）
    surface: str = SURFACE_SETTINGS


#: 分组声明（顺序即 tab 顺序）。每个 SettingDefinition 的 group 必须在这里登记，
#: 且 group_label 必须与这里的 label 一致 —— 导入期校验，不一致直接启动失败。
SETTING_GROUPS: tuple[SettingGroup, ...] = (
    SettingGroup(
        "basic", "基础设置",
        "站点基础品牌信息，前台导航栏 / 页面标题 / 浏览器标签页图标同步生效",
    ),
    SettingGroup(
        "business", "业务规则",
        "免费试用时长、单用户 AI 限额与调用重试策略。改完立即生效，无需重启",
    ),
    SettingGroup(
        "ai", "全站成本护栏",
        "全站日/月调用上限与未单独定价模型的兜底单价。0 表示不启用该维度限制（不是"
        "「0 次可用」）；单价为 0 时看板不估算金额。改完即时生效，无需重启容器",
        # 2026-10-27：与模型、单模型单价同页（「系统 → 模型管理」）。此前拆在两个页面，
        # 导致「调单模型单价时看不到全站限额」——成本相关配置必须能在一屏内对照。
        surface=SURFACE_MODEL,
    ),
    SettingGroup(
        "ai_writing", "AI 写作",
        "后台编辑器 AI 改写/续写/插图的总开关与图片日限额；提示词在「AI 写作」页面维护",
    ),
    SettingGroup(
        "moderation", "内容审核",
        "UGC 内容审核：总开关、命中处置方式与追加词库。词库改完保存即重建匹配器，无需重启",
    ),
    SettingGroup(
        "upload", "上传限制",
        "各类上传的大小上限、允许的头像格式，以及图片 WebP 转码策略。改动只影响新上传，已上传文件不受影响",
    ),
    SettingGroup(
        "image_search", "图片搜索",
        "编辑器内免版税图库检索的开关、图源密钥与限额。密钥加密保存、管理端只回显掩码；"
        "关闭后工具栏入口隐藏，已插入的图片不受影响",
    ),
    SettingGroup(
        "ratelimit", "访问限流",
        "按 IP / 用户计的固定窗口限流。调低可能误伤正常用户，调高会削弱防刷能力；健康检查与 CORS 预检不计入",
    ),
    SettingGroup(
        "search", "站内搜索",
        "站内搜索的全文检索开关与关键词长度上限。全文索引出问题时可用开关一键退回 LIKE",
    ),
    SettingGroup(
        "seo", "站点与 SEO",
        "站点根地址与收录推送凭据。站点根地址会写进 sitemap、推送链接与分享海报，改错会让收录链接全错",
    ),
    SettingGroup(
        "fetch", "第三方导入",
        "后台「从第三方导入」用到的出站代理与凭据（GitHub / HuggingFace / 国外文章平台）。服务器可直连时全部留空",
    ),
    SettingGroup(
        "storage", "对象存储",
        "腾讯云 COS 对象存储凭据。全站上传（头像 / 图片 / 资源文件）都走这里，填错时上传会明确报错",
    ),
    SettingGroup(
        "auth_phone", "手机号验证码登录",
        "手机号 + 短信验证码登录：总开关与首登策略。关闭后 /auth/phone/* 直接拒绝，"
        "前台登录页不显示手机号入口",
        surface=SURFACE_LOGIN,
    ),
    SettingGroup(
        "auth_wechat", "微信登录",
        "微信扫码（网站应用）与小程序登录。凭据没配齐时即使开关打开也走不通，"
        "页面顶部的渠道状态会标出还缺什么",
        surface=SURFACE_LOGIN,
    ),
    SettingGroup(
        "miniapp", "小程序凭据",
        "微信小程序 AppID / AppSecret，用于 code2session 静默登录与账号绑定。"
        "生产环境未配置时小程序登录返回 503（开发环境会退化为 mock openid，便于联调）；"
        "AppSecret 加密存储，页面只回显掩码",
        # 2026-10-27：这两个键的**唯一消费方**是 miniapp_service.code_to_session（登录/绑定），
        # 此前留在「系统设置 → 小程序」是历史错位 —— 登录页说「缺凭据」，
        # 却要跳到另一个菜单才能填。归位到登录管理，改与不改在同一屏。
        surface=SURFACE_LOGIN,
    ),
    SettingGroup(
        "auth_sms", "短信通道",
        "短信服务商与凭据、验证码有效期与发送频控。Mock 只在本地联调可用（线上不会真正下发）；"
        "凭据加密存储，页面只回显掩码。只需填所选服务商那一组参数",
        surface=SURFACE_LOGIN,
    ),
    SettingGroup(
        "payment_general", "支付开关与订单",
        "在线支付总开关、Mock 模拟支付与订单有效期。总开关关闭时下单接口直接拒绝、"
        "前台不展示充值入口；Mock 只在非生产环境生效，正式环境请保持关闭",
        surface=SURFACE_PAYMENT,
    ),
    SettingGroup(
        "payment_wechat", "微信支付",
        "微信支付 v3 Native 扫码：商户号 / AppID / 证书序列号 / APIv3 密钥 / 商户私钥 / "
        "平台公钥 / 回调地址。私钥加密存储，页面只显示是否已配置；缺任一项该渠道不会出现在前台",
        surface=SURFACE_PAYMENT,
    ),
    SettingGroup(
        "payment_alipay", "支付宝",
        "支付宝电脑网站支付：AppID / 应用私钥 / 支付宝公钥 / 通知与跳回地址 / 网关。"
        "公钥与私钥别填反（一个用于签名、一个用于验签），填反的表现是「支付成功但回调验签失败」",
        surface=SURFACE_PAYMENT,
    ),
    SettingGroup(
        "mail", "SMTP 通道",
        "邮件验证码（注册 / 找回密码）的通道配置。SMTP 授权码加密存储，页面只回显掩码；"
        "没配齐时生产环境发信直接失败 —— 用户点「获取验证码」只会看到一句「发送失败」。"
        "保存后可在页面上直接发一封测试邮件验证，不必等真实用户来撞",
        # 2026-10-27：与短信通道同属「通道型配置」，抽到「系统 → 邮件通知」独立页。
        # 抽出的理由不是它大（7 项），而是它**默认就是坏的**：不填 SMTP 生产环境发不出验证码，
        # 而页面只能填参数、没法验证「到底能不能发出去」。
        surface=SURFACE_MAIL,
    ),
    SettingGroup(
        "hero", "模块主视觉图",
        "各模块首页顶部 Hero 区的主视觉插画，建议横版透明底图片",
    ),
    SettingGroup(
        "default", "默认图片",
        "内容未单独设置图片时的兜底展示；不设置则维持各处原有的渐变 / 图标回退",
    ),
    SettingGroup(
        "qrcode", "二维码",
        "前台扫码入口展示的二维码图片（在线客服 / 微信公众号 / 社区 / 知识星球）",
    ),
    SettingGroup(
        "social", "社交链接",
        "前台页脚「关注我们」的社交图标跳转链接（抖音 / B站 / GitHub），"
        "以及底部「其他作品」的外链列表；未配置的项不展示，清空保存即隐藏",
    ),
    SettingGroup(
        "invite", "邀请返利",
        "开启后用户可通过邀请链接 / 邀请码邀请好友注册，邀请人获得积分奖励。"
        "关闭开关后链接仍可分享但不再发放积分",
    ),
    SettingGroup(
        "legal", "法律文档",
        "前台页脚「隐私政策 / 用户协议 / 免责声明」页面的富文本内容；清空保存后恢复系统默认文案",
        # 2026-10-27：本组已抽为独立模块「系统 → 法律文档」，不再出现在系统设置的 tab 里。
        # 定义仍留在此处（键与默认值的唯一事实来源），只是归属换了 surface。
        surface=SURFACE_LEGAL,
    ),
)


SETTING_DEFINITIONS: list[SettingDefinition] = [
    # 基础设置
    SettingDefinition("site_logo", "站点 Logo", "image", "basic", "基础设置", "/logo_new.png"),
    SettingDefinition("site_name", "平台名称", "text", "basic", "基础设置", "AI学习猫"),
    # 管理员强制二次验证：开启前要求所有有效管理员都已绑定 TOTP，避免把人锁在门外
    SettingDefinition(
        "admin_require_2fa", "管理员强制二次验证", "bool", "basic", "基础设置", "0",
        public=False,
        help_text="开启后管理员必须绑定 TOTP 才能登录；开启前需确保所有有效管理员已绑定，"
        "开启期间管理员不能关闭二次验证。新管理员请先建成普通账号并完成绑定，再提升为管理员。",
    ),
    # 各模块首页顶部主视觉图
    SettingDefinition("hero_home", "首页主视觉图", "image", "hero", "模块主视觉图", "/hero_home.webp"),
    SettingDefinition("hero_tutorials", "AI教程页主视觉图", "image", "hero", "模块主视觉图", "/hero_tutorials.webp"),
    SettingDefinition("hero_resources", "AI资源页主视觉图", "image", "hero", "模块主视觉图", "/hero_resources.webp"),
    SettingDefinition("hero_tools", "AI工具页主视觉图", "image", "hero", "模块主视觉图", "/hero_tools.webp"),
    SettingDefinition("hero_nodes", "AI百科页主视觉图", "image", "hero", "模块主视觉图", "/hero_nodes.webp"),
    SettingDefinition("hero_plugins", "AI扩展页主视觉图", "image", "hero", "模块主视觉图", "/hero_plugins.webp"),
    # 默认图片（无图时的兜底，留空则维持各处原有的渐变/图标回退）
    SettingDefinition("default_tutorial_cover", "教程默认封面图", "image", "default", "默认图片"),
    SettingDefinition("default_resource_cover", "资源默认封面图", "image", "default", "默认图片"),
    SettingDefinition("default_node_cover", "知识百科默认封面图", "image", "default", "默认图片"),
    SettingDefinition("default_avatar", "用户默认头像", "image", "default", "默认图片"),
    # 二维码
    SettingDefinition("community_qrcode", "学习社区二维码", "image", "qrcode", "二维码", "/shequ.png"),
    SettingDefinition("star_auth_qrcode", "知识星球认证二维码", "image", "qrcode", "二维码", "/shenqing.webp"),
    SettingDefinition("service_qrcode", "在线客服二维码", "image", "qrcode", "二维码", ""),
    SettingDefinition("wechat_qrcode", "微信公众号二维码", "image", "qrcode", "二维码", "/gzh.jpg"),
    # 社交链接
    SettingDefinition("social_douyin", "抖音主页链接", "url", "social", "社交链接", ""),
    SettingDefinition("social_bilibili", "B站主页链接", "url", "social", "社交链接", ""),
    SettingDefinition("social_github", "GitHub 主页链接", "url", "social", "社交链接", ""),
    # 对外联系邮箱：只给前台「联系我们」页展示（mailto），留空则整块不显示。
    # 用 text 而不是 url：url 类型会校验 http(s) 格式，邮箱不是 URL。
    SettingDefinition(
        "contact_email", "对外联系邮箱", "text", "social", "社交链接", "",
        help_text="展示在「联系我们」页，用于商务合作与内容授权；留空则整块不显示",
    ),
    # 页脚「其他作品」：一行一条「名称 | 链接」，加新站只在这里加一行，无需改代码重新部署。
    # 不用 json 类型：那个校验是给 AI 单价表写死的（要求对象套对象），不适合链接列表。
    SettingDefinition(
        "footer_sites",
        "页脚「其他作品」",
        "textarea",
        "social",
        "社交链接",
        "",
        help_text=(
            "一行一条，格式「名称 | 链接」，例如：\n"
            "See Every Step | https://example.com\n"
            "只接受 http/https 链接，最多展示 8 条，留空即整块不展示"
        ),
    ),
    # 邀请返利
    # 邀请返利：开关用 bool（后台渲染成 Switch），积分用 number（带数值校验）。
    # 存储值仍是 "1"/"0" 与十进制数字串，invite_service 的读取口径不变。
    SettingDefinition(
        "invite_enabled", "邀请返利开关", "bool", "invite", "邀请返利", "1",
        help_text="关闭后邀请链接仍可分享，但不再发放邀请奖励积分",
    ),
    SettingDefinition(
        "invite_reward_points", "邀请奖励积分（每邀请一位好友注册）", "number", "invite", "邀请返利", "50",
        help_text="邀请人每成功邀请一位新用户注册获得的积分；0 = 不发放积分",
    ),
    # 法律文档（前台 /legal 页展示，富文本 HTML）
    SettingDefinition("legal_privacy", "隐私政策", "richtext", "legal", "法律文档", _LEGAL_PRIVACY_DEFAULT),
    SettingDefinition("legal_agreement", "用户协议", "richtext", "legal", "法律文档", _LEGAL_AGREEMENT_DEFAULT),
    SettingDefinition("legal_disclaimer", "免责声明", "richtext", "legal", "法律文档", _LEGAL_DISCLAIMER_DEFAULT),

    # AI 成本护栏（模型/凭据/按模型单价已迁到「系统 → 模型管理」，这里只留全站策略）
    # 默认值直接取环境变量，未配置时行为与升级前完全一致。
    #   · 数值项统一「0 = 不启用」：0 次上限 = 不限制，0 单价 = 不估算金额；
    #   · 单模型单价在模型管理里逐条配置，未配置的模型回退这里的兜底单价。
    SettingDefinition("ai_global_daily_limit", "全站日调用上限（次/天，0=不限制）", "number", "ai", "全站成本护栏", str(settings.AI_GLOBAL_DAILY_LIMIT), public=False),
    SettingDefinition("ai_global_monthly_limit", "全站月调用上限（次/月，0=不限制）", "number", "ai", "全站成本护栏", str(settings.AI_GLOBAL_MONTHLY_LIMIT), public=False),
    SettingDefinition("ai_price_input_per_mtok", "兜底输入单价（元/百万 tokens，0=不估算）", "number", "ai", "全站成本护栏", str(settings.AI_PRICE_INPUT_PER_MTOK), public=False),
    SettingDefinition("ai_price_output_per_mtok", "兜底输出单价（元/百万 tokens，0=不估算）", "number", "ai", "全站成本护栏", str(settings.AI_PRICE_OUTPUT_PER_MTOK), public=False),

    # AI 写作（后台编辑器改写/续写/插图；提示词本身在「AI 写作」页面维护）
    SettingDefinition(
        "ai_writing_enabled", "AI 写作总开关", "bool", "ai_writing", "AI 写作",
        "1" if settings.AI_WRITING_ENABLED else "0", public=False,
        help_text="关闭后编辑器隐藏 AI 入口，接口拒绝请求",
    ),
    SettingDefinition(
        "ai_writing_image_daily_limit", "AI 插图日限额（张/天，0=不限）", "number",
        "ai_writing", "AI 写作", str(settings.AI_WRITING_IMAGE_DAILY_LIMIT), public=False,
        help_text="全站每天可成功生成的插图总数",
    ),

    # 图片搜索（编辑器工具栏；图源密钥与限额，前台不消费）
    SettingDefinition(
        "image_search_enabled", "图片搜索总开关", "bool", "image_search", "图片搜索",
        "1" if settings.IMAGE_SEARCH_ENABLED else "0", public=False,
        help_text="关闭后编辑器隐藏搜索入口；已插入的图片与本站素材不受影响",
    ),
    SettingDefinition(
        "image_search_pexels_key", "Pexels API Key", "password", "image_search", "图片搜索",
        settings.IMAGE_SEARCH_PEXELS_KEY, secret=True, public=False,
        help_text="免费申请：https://www.pexels.com/api/；留空则回退环境变量",
    ),
    SettingDefinition(
        "image_search_pixabay_key", "Pixabay API Key", "password", "image_search", "图片搜索",
        settings.IMAGE_SEARCH_PIXABAY_KEY, secret=True, public=False,
        help_text="免费申请：https://pixabay.com/api/docs/；API 条款要求署名，导入时默认附带",
    ),
    SettingDefinition(
        "image_search_giphy_key", "GIPHY API Key", "password", "image_search", "图片搜索",
        settings.IMAGE_SEARCH_GIPHY_KEY, secret=True, public=False,
        help_text="申请：https://developers.giphy.com/dashboard/；Tenor 兼容源复用同一把 Key",
    ),
    SettingDefinition(
        "image_search_memecub_key", "memecub API Key（可选）", "password",
        "image_search", "图片搜索",
        settings.IMAGE_SEARCH_MEMECUB_KEY, secret=True, public=False,
        help_text="不填也能用（每 IP 每天 20 次搜索）；填写后按月额度。申请：https://memecub.com/zh/developers",
    ),
    SettingDefinition(
        "image_search_pexels_enabled", "启用 Pexels", "bool", "image_search", "图片搜索",
        "1", public=False, help_text="关闭后不请求 Pexels；已插入的图片不受影响",
    ),
    SettingDefinition(
        "image_search_pixabay_enabled", "启用 Pixabay", "bool", "image_search", "图片搜索",
        "1", public=False, help_text="关闭后不请求 Pixabay；已插入的图片不受影响",
    ),
    SettingDefinition(
        "image_search_giphy_enabled", "启用 GIPHY", "bool", "image_search", "图片搜索",
        "1", public=False, help_text="需要先填写 GIPHY API Key；支持 GIF 动图直通",
    ),
    SettingDefinition(
        "image_search_tenor_enabled", "启用 Tenor 兼容源", "bool", "image_search", "图片搜索",
        "0", public=False,
        help_text="Tenor 官方 API 已于 2026-06-30 停服；开启后走 GIPHY 的 /v2 兼容端点",
    ),
    SettingDefinition(
        "image_search_so360_enabled", "启用 360 图片爬取", "bool", "image_search", "图片搜索",
        "0", public=False, help_text="解析 360 图片公开 JSON 接口；国内直连快，无需 Key，默认关闭",
    ),
    SettingDefinition(
        "image_search_baidu_enabled", "启用百度图片爬取", "bool", "image_search", "图片搜索",
        "0", public=False, help_text="通过百度图片公开 JSON 接口检索；可能被站点安全验证拦截",
    ),
    SettingDefinition(
        "image_search_sogou_enabled", "启用搜狗图片爬取", "bool", "image_search", "图片搜索",
        "0", public=False, help_text="解析搜狗图片公开页面内嵌数据；无需 Key，默认关闭",
    ),
    SettingDefinition(
        "image_search_duitang_enabled", "启用堆糖爬取", "bool", "image_search", "图片搜索",
        "0", public=False, help_text="调用堆糖公开搜索接口；国内直连快，无需 Key，默认关闭",
    ),
    SettingDefinition(
        "image_search_doutupk_enabled", "启用斗图啦", "bool", "image_search", "图片搜索",
        "0", public=False, help_text="抓取 doutupk.com 搜索页；支持 GIF 表情包，默认关闭",
    ),
    SettingDefinition(
        "image_search_adoutu_enabled", "启用爱斗图", "bool", "image_search", "图片搜索",
        "0", public=False, help_text="解析 adoutu.com 搜索页；图片域名固定，支持 GIF，默认关闭",
    ),
    SettingDefinition(
        "image_search_memecub_enabled", "启用 memecub", "bool", "image_search", "图片搜索",
        "0", public=False,
        help_text="走 memecub 官方 REST API；未填 Key 时每 IP 每天 20 次搜索，默认关闭",
    ),
    SettingDefinition(
        "image_search_emoji_enabled", "启用 EmojiPackage", "bool", "image_search", "图片搜索",
        "0", public=False,
        help_text="检索 GitHub getActivity/EmojiPackage 仓库图片；按文件名搜索，默认关闭",
    ),
    SettingDefinition(
        "image_search_page_size", "每页结果数", "number", "image_search", "图片搜索",
        str(settings.IMAGE_SEARCH_PAGE_SIZE), public=False,
        help_text="单个图源每页返回数量，建议 12–40",
    ),
    SettingDefinition(
        "image_search_admin_hourly_limit", "管理员每小时搜索次数", "number",
        "image_search", "图片搜索", str(settings.IMAGE_SEARCH_ADMIN_HOURLY_LIMIT), public=False,
        help_text="按管理员账号计；每个图源请求计一次，防止单人打满图源额度",
    ),
    SettingDefinition(
        "image_search_daily_import_limit", "每日导入图片上限（张/天，0=不限）", "number",
        "image_search", "图片搜索", str(settings.IMAGE_SEARCH_DAILY_IMPORT_LIMIT), public=False,
        help_text="全站每天成功转存到 COS 的图库图片总数，控制存储成本",
    ),

    # ------------------------------------------------------------------
    # 运行期可改配置（2026-10-19）
    #
    # 这些项此前只能改 .env + 重启容器。默认值一律取环境变量，后台不配置时
    # 行为与升级前**完全一致**；保存后由 services/runtime_config.py 覆盖到
    # settings 上（缺省项回退 env 原值），无需重启。
    #
    # ⚠️ 刻意不放进来的：SECRET_KEY / JWT_* / DB_* / REDIS_* / CORS_ORIGINS /
    # TRUSTED_PROXIES（引导期与安全边界）、SENTRY_*（SDK 初始化一次）、
    # SEARCH_NGRAM_TOKEN_SIZE（须与 MySQL 服务端一致）、AI_TIMEOUT_SECONDS
    # （Provider 实例缓存，改了要重建才生效）。
    # ------------------------------------------------------------------

    # 业务规则
    SettingDefinition(
        "free_time_hours", "免费试用时长（小时）", "number", "business", "业务规则",
        f"{settings.FREE_TIME_LIMIT / 3600:g}", public=False,
        help_text="新用户免费学习时长。0 = 无免费额度（需认证后使用）。按自然时间计算，非学习时长",
    ),
    SettingDefinition(
        "ai_daily_limit_per_user", "单用户日 AI 调用上限（次/天）", "number", "business", "业务规则",
        str(settings.AI_DAILY_LIMIT_PER_USER), public=False,
        help_text="每个登录用户每天可调用 AI 工具的次数。0 = 完全禁止（谨慎填写）",
    ),
    SettingDefinition(
        "ai_retry_count", "AI 失败重试次数", "number", "business", "业务规则",
        str(settings.AI_RETRY_COUNT), public=False,
        help_text="调用失败后的重试次数（0-5）。与「总耗时预算」共同决定最坏响应时间",
    ),
    SettingDefinition(
        "ai_total_budget_seconds", "AI 调用总耗时预算（秒）", "number", "business", "业务规则",
        str(settings.AI_TOTAL_BUDGET_SECONDS), public=False,
        help_text="单次请求内所有重试累计的时间上限，超预算立即放弃重试并快速失败",
    ),

    # 内容审核
    SettingDefinition(
        "moderation_enabled", "启用 UGC 敏感词审核", "bool", "moderation", "内容审核",
        "1" if settings.MODERATION_ENABLED else "0", public=False,
        help_text="关闭后所有敏感词检查跳过（仅排障用，生产不建议关）",
    ),
    SettingDefinition(
        "moderation_action", "命中敏感词后的处置方式", "select", "moderation", "内容审核",
        settings.MODERATION_ACTION, public=False,
        options=[
            {"value": "reject", "label": "拒绝提交"},
            {"value": "mask", "label": "打码后入库"},
            {"value": "review", "label": "入库待人工复核"},
        ],
        help_text="reject = 直接报错；mask = 命中词替换为等长星号；review = 放行但进审核队列",
    ),
    SettingDefinition(
        "moderation_extra_words", "追加敏感词", "textarea", "moderation", "内容审核",
        settings.MODERATION_EXTRA_WORDS, public=False,
        help_text="在内置基线词库之上追加，逗号或换行分隔。保存后立即生效（自动重建匹配器）",
    ),

    # 上传限制
    SettingDefinition(
        "upload_max_file_mb", "资源文件大小上限（MB）", "number", "upload", "上传限制",
        str(settings.COS_MAX_FILE_SIZE // (1024 * 1024)), public=False,
        help_text="超出时拒绝上传并提示改用网盘链接",
    ),
    SettingDefinition(
        "upload_max_image_mb", "编辑器图片大小上限（MB）", "number", "upload", "上传限制",
        str(settings.COS_IMAGE_MAX_SIZE // (1024 * 1024)), public=False,
        help_text="教程/章节/评论等处插入图片的单张上限",
    ),
    SettingDefinition(
        "avatar_max_size_mb", "用户头像大小上限（MB）", "number", "upload", "上传限制",
        str(settings.AVATAR_MAX_SIZE // (1024 * 1024)), public=False,
        help_text="个人中心上传头像的单张上限",
    ),
    SettingDefinition(
        "avatar_allowed_types", "允许的头像格式（MIME 类型，逗号分隔）", "textarea", "upload", "上传限制",
        settings.AVATAR_ALLOWED_TYPES, public=False,
        help_text="例如 image/jpeg,image/png,image/webp,image/gif。收紧后新上传会被拒绝，已上传的不受影响",
    ),
    SettingDefinition(
        "image_webp_threshold_kb", "图片转 WebP 阈值（KB，0=关闭转码）", "number", "upload", "上传限制",
        str(settings.COS_IMAGE_WEBP_THRESHOLD // 1024), public=False,
        help_text="超过该大小的图片会在服务端转 WebP 再入 COS：省流量，但多一次 CPU 开销",
    ),
    SettingDefinition(
        "image_webp_quality", "WebP 转码质量（1-100）", "number", "upload", "上传限制",
        str(settings.COS_IMAGE_WEBP_QUALITY), public=False,
        help_text="与前端展示参数保持一致；越低越省流量，画质越差",
    ),

    # 访问限流
    SettingDefinition(
        "rate_limit_guest", "游客限流（次/窗口）", "number", "ratelimit", "访问限流",
        str(settings.RATE_LIMIT_GUEST), public=False,
        help_text="未登录访客在单位时间窗口内的请求上限，按 IP 计数",
    ),
    SettingDefinition(
        "rate_limit_user", "登录用户限流（次/窗口）", "number", "ratelimit", "访问限流",
        str(settings.RATE_LIMIT_USER), public=False,
        help_text="已登录用户在单位时间窗口内的请求上限，按用户计数",
    ),
    SettingDefinition(
        "rate_limit_window", "限流时间窗口（秒）", "number", "ratelimit", "访问限流",
        str(settings.RATE_LIMIT_WINDOW), public=False,
        help_text="固定窗口长度。改小会让突发流量更容易被拒，改大则更容易累积到上限",
    ),

    # 站内搜索
    SettingDefinition(
        "search_fulltext_enabled", "启用 MySQL 全文检索", "bool", "search", "站内搜索",
        "1" if settings.SEARCH_FULLTEXT_ENABLED else "0", public=False,
        help_text="关闭后一键退回改造前的 LIKE 匹配（全文索引出问题时的应急开关），无需发版",
    ),
    SettingDefinition(
        "search_max_keyword_len", "关键词最大长度（字符）", "number", "search", "站内搜索",
        str(settings.SEARCH_MAX_KEYWORD_LEN), public=False,
        help_text="超过该长度的关键词直接走 LIKE：查询串过长会被 MySQL 拒绝（errno 191）",
    ),

    # 站点与 SEO
    SettingDefinition(
        "site_base_url", "站点根地址", "url", "seo", "站点与 SEO",
        settings.SITE_BASE_URL, public=False,
        help_text="sitemap、百度推送、IndexNow、邀请海报、llms.txt 拼接链接用。改错会导致收录链接全错",
    ),
    SettingDefinition(
        "baidu_push_token", "百度搜索资源平台推送 token", "password", "seo", "站点与 SEO",
        settings.BAIDU_PUSH_TOKEN, secret=True, public=False,
        help_text="留空则每日推送任务自动跳过",
    ),
    SettingDefinition(
        "indexnow_key", "IndexNow 密钥（Bing 等）", "password", "seo", "站点与 SEO",
        settings.INDEXNOW_KEY, secret=True, public=False,
        help_text="32 位十六进制串。留空则推送任务与密钥文件路由同时停用",
    ),

    # 第三方导入
    SettingDefinition(
        "github_token", "GitHub Token", "password", "fetch", "第三方导入",
        settings.GITHUB_TOKEN, secret=True, public=False,
        help_text="可选。配置后 GitHub API 速率上限从 60 次/小时提升到 5000 次/小时",
    ),
    SettingDefinition(
        "github_proxy", "GitHub 出站代理", "text", "fetch", "第三方导入",
        settings.GITHUB_PROXY, public=False,
        help_text="形如 http://172.17.0.1:7890（容器可达的网桥地址）。留空则直连",
    ),
    SettingDefinition(
        "github_mirror_prefix", "GitHub 加速镜像前缀", "url", "fetch", "第三方导入",
        settings.GITHUB_MIRROR_PREFIX, public=False,
        help_text="需以 / 结尾。拉取顺序：jsDelivr → 官方 API → 官方 API（经代理）→ 本镜像",
    ),
    SettingDefinition(
        "hf_token", "HuggingFace Token", "password", "fetch", "第三方导入",
        settings.HF_TOKEN, secret=True, public=False,
        help_text="可选。匿名即可拉公开资源，配置后可拉受限资源并提升速率",
    ),
    SettingDefinition(
        "article_proxy", "国外文章平台出站代理", "text", "fetch", "第三方导入",
        settings.ARTICLE_PROXY, public=False,
        help_text="仅对 X/Twitter、Reddit、Medium、Hacker News 等国内无法直连的平台生效",
    ),

    # 对象存储
    SettingDefinition(
        "cos_secret_id", "COS SecretId", "password", "storage", "对象存储",
        settings.COS_SECRET_ID, secret=True, public=False,
        help_text="腾讯云访问密钥。上传头像/图片/资源文件都走 COS，改动会影响全站上传",
    ),
    SettingDefinition(
        "cos_secret_key", "COS SecretKey", "password", "storage", "对象存储",
        settings.COS_SECRET_KEY, secret=True, public=False,
        help_text="与 SecretId 配对。填错时上传会明确报错（不会静默失败）",
    ),
    SettingDefinition(
        "cos_bucket", "COS 存储桶", "text", "storage", "对象存储",
        settings.COS_BUCKET, public=False,
        help_text="形如 cat-1309118228（含 APPID 后缀）",
    ),
    SettingDefinition(
        "cos_region", "COS 地域", "text", "storage", "对象存储",
        settings.COS_REGION, public=False,
        help_text="形如 ap-guangzhou / ap-shanghai",
    ),
    SettingDefinition(
        "cos_cdn_domain", "COS 访问域名（CDN）", "text", "storage", "对象存储",
        settings.COS_CDN_DOMAIN, public=False,
        help_text="可选。留空使用 COS 默认域名；配置后所有文件链接改走该域名（含 https://）",
    ),

    # 小程序
    SettingDefinition(
        "wechat_mini_appid", "小程序 AppID", "text", "miniapp", "小程序凭据",
        settings.WECHAT_MINI_APPID, public=False,
        help_text="微信小程序登录用。与 Secret 同时配置后才生效",
    ),
    SettingDefinition(
        "wechat_mini_secret", "小程序 AppSecret", "password", "miniapp", "小程序凭据",
        settings.WECHAT_MINI_SECRET, secret=True, public=False,
        help_text="用于 code2session 换取 openid。生产环境未配置时小程序登录返回 503",
    ),

    # 登录链路（2026-10-27）：手机号 + 验证码、微信扫码、短信商参数。
    # ⚠️ 这三组归属 `SURFACE_LOGIN`，由「系统 → 登录管理」承载，不出现在系统设置的 tab 里；
    # 分组顺序即登录管理页的区块顺序（手机号 → 微信 → 短信通道）。
    SettingDefinition(
        "phone_login_enabled", "手机号验证码登录", "bool", "auth_phone", "手机号验证码登录", "0", public=False,
        help_text="关闭后 /auth/phone/* 直接拒绝，前端登录页不显示手机号登录",
    ),
    SettingDefinition(
        "phone_login_auto_register", "手机号首登自动注册", "bool", "auth_phone", "手机号验证码登录", "1", public=False,
        help_text="开启：未注册手机号验证通过后自动建号；关闭：必须先登录邮箱账号再绑定手机号",
    ),
    SettingDefinition(
        "wechat_web_login_enabled", "微信扫码登录", "bool", "auth_wechat", "微信登录", "0", public=False,
        help_text="需要微信开放平台「网站应用」资质与 AppID/Secret；未配置时按钮不会显示",
    ),
    SettingDefinition(
        "wechat_login_auto_register", "微信首登自动注册", "bool", "auth_wechat", "微信登录", "1", public=False,
        help_text="开启：未绑定账号的微信身份验证通过后自动建号；关闭：提示先绑定已有账号",
    ),
    SettingDefinition(
        "wechat_mini_login_enabled", "微信小程序登录", "bool", "auth_wechat", "微信登录", "0", public=False,
        help_text="小程序登录总开关；关闭后 /auth/miniapp/login 直接拒绝",
    ),
    SettingDefinition(
        "wechat_web_appid", "网站应用 AppID", "text", "auth_wechat", "微信登录", "", public=False,
        help_text="微信开放平台网站应用的 AppID（与小程序 AppID 不同）",
    ),
    SettingDefinition(
        "wechat_web_secret", "网站应用 AppSecret", "password", "auth_wechat", "微信登录", "",
        secret=True, public=False,
        help_text="用于扫码回调换取 access_token / openid / unionid",
    ),
    SettingDefinition(
        "wechat_web_redirect_uri", "网站应用回调地址", "url", "auth_wechat", "微信登录", "", public=False,
        help_text="必须与开放平台「授权回调域」完全一致；留空则用 site_base_url + /api/v1/auth/wechat/web/callback",
    ),
    SettingDefinition(
        "sms_provider", "短信服务商", "select", "auth_sms", "短信通道", "mock", public=False,
        options=[
            {"value": "mock", "label": "Mock（仅本地联调）"},
            {"value": "tencent", "label": "腾讯云 SMS"},
            {"value": "aliyun", "label": "阿里云 SMS"},
        ],
        help_text="生产环境请选择已开通的服务商；Mock 不会真正发短信",
    ),
    SettingDefinition(
        "sms_code_ttl_seconds", "验证码有效期（秒）", "number", "auth_sms", "短信通道", "300", public=False,
        help_text="默认 300 秒（5 分钟）",
    ),
    SettingDefinition(
        "sms_send_interval_seconds", "重发间隔（秒）", "number", "auth_sms", "短信通道", "60", public=False,
        help_text="同一手机号两次发送的最小间隔",
    ),
    SettingDefinition(
        "sms_daily_limit_per_phone", "单号码日发送上限", "number", "auth_sms", "短信通道", "10", public=False,
        help_text="自然日；防止短信轰炸与 SMS pumping 话费损失",
    ),
    SettingDefinition(
        "sms_daily_limit_per_ip", "单 IP 日发送上限", "number", "auth_sms", "短信通道", "20", public=False,
        help_text="自然日；匿名接口的兜底防刷",
    ),
    SettingDefinition(
        "sms_mock_code", "Mock 固定验证码", "text", "auth_sms", "短信通道", "123456", public=False,
        help_text="仅 sms_provider=mock 时生效；生产环境请勿使用 Mock",
    ),
    SettingDefinition(
        "sms_tencent_secret_id", "腾讯云 SecretId", "password", "auth_sms", "短信通道", "",
        secret=True, public=False,
    ),
    SettingDefinition(
        "sms_tencent_secret_key", "腾讯云 SecretKey", "password", "auth_sms", "短信通道", "",
        secret=True, public=False,
    ),
    SettingDefinition(
        "sms_tencent_sdk_app_id", "腾讯云短信 SDKAppID", "text", "auth_sms", "短信通道", "", public=False,
        help_text="短信控制台的 SdkAppId（数字）",
    ),
    SettingDefinition(
        "sms_tencent_sign_name", "腾讯云短信签名", "text", "auth_sms", "短信通道", "", public=False,
    ),
    SettingDefinition(
        "sms_tencent_template_id", "腾讯云短信模板 ID", "text", "auth_sms", "短信通道", "", public=False,
    ),
    SettingDefinition(
        "sms_tencent_region", "腾讯云短信地域", "text", "auth_sms", "短信通道", "ap-guangzhou", public=False,
    ),
    SettingDefinition(
        "sms_tencent_template_params", "腾讯云模板参数 JSON", "text", "auth_sms", "短信通道",
        '["{code}","5"]', public=False,
        help_text='模板参数数组，{code} 会替换为验证码；例如 ["{code}","5"]',
    ),
    SettingDefinition(
        "sms_aliyun_access_key_id", "阿里云 AccessKeyId", "text", "auth_sms", "短信通道", "", public=False,
    ),
    SettingDefinition(
        "sms_aliyun_access_key_secret", "阿里云 AccessKeySecret", "password", "auth_sms", "短信通道", "",
        secret=True, public=False,
    ),
    SettingDefinition(
        "sms_aliyun_sign_name", "阿里云短信签名", "text", "auth_sms", "短信通道", "", public=False,
    ),
    SettingDefinition(
        "sms_aliyun_template_code", "阿里云模板 CODE", "text", "auth_sms", "短信通道", "", public=False,
    ),
    SettingDefinition(
        "sms_aliyun_region", "阿里云短信地域", "text", "auth_sms", "短信通道", "cn-hangzhou", public=False,
    ),
    SettingDefinition(
        "sms_aliyun_template_param_key", "阿里云模板参数名", "text", "auth_sms", "短信通道", "code", public=False,
        help_text="模板变量名，默认 code；发送时传 {该字段: 验证码}",
    ),

    # 支付与渠道（2026-10-27）：积分充值 / VIP 套餐
    SettingDefinition(
        "payment_enabled", "支付总开关", "bool", "payment_general", "支付开关与订单", "0", public=False,
        help_text="关闭后下单接口直接拒绝，前端不展示充值入口",
    ),
    SettingDefinition(
        "payment_mock_enabled", "启用 Mock 支付", "bool", "payment_general", "支付开关与订单", "1", public=False,
        help_text="仅用于本地/验收环境的模拟支付；生产环境请关闭",
    ),
    SettingDefinition(
        "payment_order_expire_minutes", "订单有效期（分钟）", "number", "payment_general", "支付开关与订单", "30", public=False,
    ),
    SettingDefinition(
        "wechat_pay_enabled", "微信支付（Native 扫码）", "bool", "payment_wechat", "微信支付", "0", public=False,
    ),
    SettingDefinition(
        "wechat_pay_mch_id", "微信支付商户号", "text", "payment_wechat", "微信支付", "", public=False,
    ),
    SettingDefinition(
        "wechat_pay_appid", "微信支付 AppID", "text", "payment_wechat", "微信支付", "", public=False,
        help_text="绑定当前商户号的 AppID（公众号/开放平台/小程序）",
    ),
    SettingDefinition(
        "wechat_pay_serial_no", "商户证书序列号", "text", "payment_wechat", "微信支付", "", public=False,
    ),
    SettingDefinition(
        "wechat_pay_api_v3_key", "微信支付 APIv3 密钥", "password", "payment_wechat", "微信支付", "",
        secret=True, public=False,
        help_text="32 位；用于解密回调 resource",
    ),
    SettingDefinition(
        "wechat_pay_private_key", "微信支付商户私钥（PEM）", "secret_text", "payment_wechat", "微信支付", "",
        secret=True, public=False,
        help_text="apiclient_key.pem 全文；加密存储，页面只显示是否已配置",
    ),
    SettingDefinition(
        "wechat_pay_platform_public_key", "微信支付平台公钥（PEM）", "textarea", "payment_wechat", "微信支付", "",
        public=False,
        help_text="用于验证回调签名；从商户平台下载的平台证书/公钥中提取",
    ),
    SettingDefinition(
        "wechat_pay_notify_url", "微信支付回调地址", "url", "payment_wechat", "微信支付", "", public=False,
        help_text="必须为 HTTPS 公网地址；留空则用 site_base_url + /api/v1/payments/notify/wechat",
    ),
    SettingDefinition(
        "alipay_enabled", "支付宝（电脑网站支付）", "bool", "payment_alipay", "支付宝", "0", public=False,
    ),
    SettingDefinition(
        "alipay_app_id", "支付宝 AppID", "text", "payment_alipay", "支付宝", "", public=False,
    ),
    SettingDefinition(
        "alipay_private_key", "支付宝应用私钥（PEM）", "secret_text", "payment_alipay", "支付宝", "",
        secret=True, public=False,
        help_text="应用私钥全文；加密存储，页面只显示是否已配置",
    ),
    SettingDefinition(
        "alipay_public_key", "支付宝公钥（PEM）", "textarea", "payment_alipay", "支付宝", "", public=False,
        help_text="用于验证异步通知签名；不是应用公钥",
    ),
    SettingDefinition(
        "alipay_notify_url", "支付宝异步通知地址", "url", "payment_alipay", "支付宝", "", public=False,
        help_text="留空则用 site_base_url + /api/v1/payments/notify/alipay",
    ),
    SettingDefinition(
        "alipay_return_url", "支付宝同步跳回地址", "url", "payment_alipay", "支付宝", "", public=False,
        help_text="支付完成后浏览器跳回的前端页面；留空则用 site_base_url + /recharge/result",
    ),
    SettingDefinition(
        "alipay_gateway", "支付宝网关", "select", "payment_alipay", "支付宝",
        "https://openapi.alipay.com/gateway.do", public=False,
        options=[
            {"value": "https://openapi.alipay.com/gateway.do", "label": "生产环境"},
            {"value": "https://openapi-sandbox.dl.alipaydev.com/gateway.do", "label": "沙箱环境"},
        ],
    ),

    # 邮件通知
    SettingDefinition(
        "smtp_host", "SMTP 服务器", "text", "mail", "SMTP 通道",
        settings.SMTP_HOST, public=False,
        help_text="留空则注册验证码等邮件功能不可用",
    ),
    SettingDefinition(
        "smtp_port", "SMTP 端口", "number", "mail", "SMTP 通道",
        str(settings.SMTP_PORT), public=False,
        help_text="587 = STARTTLS（推荐）；465 = SSL，需把下面的「使用 STARTTLS」关掉",
    ),
    SettingDefinition(
        "smtp_username", "SMTP 账号", "text", "mail", "SMTP 通道",
        settings.SMTP_USERNAME, public=False,
    ),
    SettingDefinition(
        "smtp_password", "SMTP 授权码", "password", "mail", "SMTP 通道",
        settings.SMTP_PASSWORD, secret=True, public=False,
        help_text="填邮箱的授权码，不是登录密码",
    ),
    SettingDefinition(
        "smtp_use_tls", "使用 STARTTLS", "bool", "mail", "SMTP 通道",
        "1" if settings.SMTP_USE_TLS else "0", public=False,
        help_text="587 端口开启；465 端口关闭",
    ),
    SettingDefinition(
        "smtp_from_email", "发件人地址", "text", "mail", "SMTP 通道",
        settings.SMTP_FROM_EMAIL, public=False,
        help_text="留空则使用 SMTP 账号作为发件人",
    ),
    SettingDefinition(
        "smtp_sender_name", "发件人显示名", "text", "mail", "SMTP 通道",
        settings.SMTP_SENDER_NAME, public=False,
    ),
]

def _validate_group_declarations() -> None:
    """导入期校验分组声明与定义一致。

    四件事：① 定义用的 group 必须在 SETTING_GROUPS 里（否则后台整组不渲染）；
    ② group_label 必须与声明的 label 一致（否则同一组出现两个标题）；
    ③ 声明的分组必须真的有设置项（空分组会在后台留一个空 tab）；
    ④ surface 必须是已知值 —— 写错时该组既不进系统设置、也不进登录管理，
       **整组设置从界面上消失**且没有任何报错。
    """
    declared = {g.key: g for g in SETTING_GROUPS}
    if len(declared) != len(SETTING_GROUPS):  # pragma: no cover - 只在重复登记时触发
        raise RuntimeError("SETTING_GROUPS 里有重复的分组 key")

    known_surfaces = {
        SURFACE_SETTINGS,
        SURFACE_LOGIN,
        SURFACE_LEGAL,
        SURFACE_PAYMENT,
        SURFACE_MODEL,
        SURFACE_MAIL,
    }
    bad_surface = [g.key for g in SETTING_GROUPS if g.surface not in known_surfaces]
    if bad_surface:  # pragma: no cover
        raise RuntimeError(
            f"以下分组的 surface 不在 {sorted(known_surfaces)} 里，整组设置不会出现在任何页面："
            f"{bad_surface}"
        )

    used: set[str] = set()
    for defn in SETTING_DEFINITIONS:
        group = declared.get(defn.group)
        if group is None:  # pragma: no cover
            raise RuntimeError(
                f"设置项 {defn.key} 用了未声明的分组 {defn.group!r}："
                "后台只会渲染 SETTING_GROUPS 里的分组，该组设置会整组消失"
            )
        if defn.group_label != group.label:  # pragma: no cover
            raise RuntimeError(
                f"设置项 {defn.key} 的 group_label={defn.group_label!r} 与分组声明 "
                f"{group.label!r} 不一致（同一分组会出现两个标题）"
            )
        used.add(defn.group)

    empty = [g.key for g in SETTING_GROUPS if g.key not in used]
    if empty:  # pragma: no cover
        raise RuntimeError(f"以下分组没有任何设置项，会在后台留下空 tab：{empty}")


_validate_group_declarations()

#: 分组 key → 归属界面（读写过滤都靠它，避免两处各写一份白名单）
_GROUP_SURFACE: dict[str, str] = {g.key: g.surface for g in SETTING_GROUPS}


def keys_for_surface(surface: str) -> set[str]:
    """某个管理界面负责的设置键集合（写接口据此做越界拦截）。"""
    return {d.key for d in SETTING_DEFINITIONS if _GROUP_SURFACE[d.group] == surface}


def groups_payload(surface: str | None = None) -> list[dict]:
    """分组列表（顺序即 tab 顺序），供管理端渲染。

    :param surface: 只返回该界面的分组；None = 全部（一致性校验 / 运维脚本用）
    """
    return [
        asdict(group)
        for group in SETTING_GROUPS
        if surface is None or group.surface == surface
    ]


def definitions_payload(surface: str | None = None) -> list[dict]:
    """设置项定义列表（管理后台渲染表单用）

    :param surface: 只返回该界面的设置项；None = 全部

    ⚠️ secret 项的 `default` 必须清空：它来自 `settings.X`，也就是 .env 里的**明文密钥**。
    `values` 走的是另一条「掩码」通道，但 definitions 这条通道此前把 COS SecretKey /
    SMTP 授权码 / 小程序 AppSecret 原样发给前端；而内置只读角色 viewer 拥有全部 `*:view`
    （含 setting:view），等于一个只读管理员就能读到全站长期凭证。
    只保留 `has_default` 布尔，让前端仍能提示「服务器已配置」而不暴露值。
    """
    payload: list[dict] = []
    for defn in SETTING_DEFINITIONS:
        if surface is not None and _GROUP_SURFACE[defn.group] != surface:
            continue
        item = asdict(defn)
        if defn.secret:
            item["default"] = ""
            item["has_default"] = bool(defn.default)
        payload.append(item)
    return payload
