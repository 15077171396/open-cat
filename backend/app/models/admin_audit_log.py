"""管理操作审计日志：认证审批、提权、积分调整等操作须可追溯（logger 日志易失、不可检索、不进备份）。

设计取舍：
- 不设外键：审计必须在操作者/目标被删后依然存在（外键会拒绝删除或级联删掉审计行）；
- 冗余存操作者账号快照（admin_account）：账号可改名/注销，只存 id 事后无法回答「当时是谁」；
- 只存摘要不存全文：detail 只放安全的结构化字段，绝不放密码/token（审计表本身也是泄露面）；
- 与业务同事务：业务回滚则审计行一起消失——「动作没发生」就不该留审计行。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id

# 受审计的管理动作码 → 中文标签（后台筛选下拉与表格展示共用同一份事实来源）
AUDIT_ACTIONS: dict[str, str] = {
    # 实名认证（直接决定 VIP 权益）
    "auth_approve": "通过实名认证",
    "auth_reject": "驳回实名认证",
    # 用户（提权 / 权益 / 资金）
    "user_create": "创建用户",
    "user_update_role": "变更用户角色",
    "user_update_vip": "调整会员到期",
    "user_adjust_points": "调整用户积分",
    "user_delete": "删除用户",
    "user_batch_delete": "批量删除用户",
    # 恢复已注销账号 = 把访问权还给一个已被处置的账号，敏感度与删除同级
    "user_restore": "恢复用户",
    # 积分兑换码（积分入金口）
    "redeem_code_create": "生成兑换码",
    "redeem_code_disable": "作废兑换码",
    # 权限体系
    "role_permission_update": "变更角色权限",
    "user_rbac_assign": "变更用户 RBAC 角色",
    # 权限体系 —— 角色与部门本身（第五轮审查 R5-03：此前只有「权限点变更」有审计，
    # 「建一个带全权的角色 / 删掉一个还挂着用户的角色」反而不留痕，而 P0-7 立的正是提权面）
    "role_create": "新建角色",
    "role_update": "编辑角色",
    "role_delete": "删除角色",
    "department_create": "新建部门",
    "department_update": "编辑部门",
    "department_delete": "删除部门",
    # UGC 处置（备案站点要求「谁删的、谁放回来的」可回溯；评论泛化后覆盖面含教程/节点/资源）
    "comment_delete": "删除内容评论",
    "comment_restore": "恢复内容评论",
    "report_handle": "处理内容举报",
    "moderation_review": "复核敏感词留痕",
    # 公告（直接决定全站首屏展示内容，误删/误撤影响面最大）
    "announcement_create": "新建公告",
    "announcement_update": "编辑公告",
    "announcement_delete": "删除公告",
    "announcement_publish": "发布/撤下公告",
    # 支付套餐（改价/下架直接影响收款）
    "payment_product_create": "新建充值套餐",
    "payment_product_update": "编辑充值套餐",
    "payment_product_delete": "删除充值套餐",
    # 问题反馈（用户提交的 UGC 处置）
    "feedback_update": "处理问题反馈",
    "feedback_delete": "删除问题反馈",
    # 站内消息（2026-10-28）：群发系统通知是**面向全体用户**的触达能力，
    # 发错一次就是全站可见事故，必须能回答「谁在什么时候发了什么、发给多少人」
    "message_send": "发送系统通知",
    # 内容新增与编辑（此前只有删除有审计，「谁改的」无法回答）
    "content_create": "新建内容",
    "content_update": "编辑内容",
    # 内容上架/下架（工具、教程的可见性开关 —— 影响全站首屏与付费权益入口）
    "content_publish": "上架/下架内容",
    # 内容删除（教程/资源已改软删除 → 进回收站可恢复；扩展/节点仍是硬删除）
    "content_delete": "删除内容",
    # 从回收站恢复（把已下架内容放回管理视野，敏感度与删除同级）
    "content_restore": "恢复内容",
    # 系统设置（可改 AI 供应商密钥与 base_url、邀请奖励额度、对外公示的法律文档 HTML）
    # ⚠️ detail 只放**键名**：值里含 secret 项，审计表按 365 天保留且可被 audit:view 检索
    "settings_update": "更新系统设置",
    # 登录管理（2026-10-27）：开关登录方式 / 改短信与微信凭据，直接决定「用户能不能登录」。
    # 与 settings_update 分列，是为了让审计页能单独筛出登录配置的变更历史
    "login_setting_update": "更新登录配置",
    # 法律文档（2026-10-27）：隐私政策/用户协议/免责声明全站可见，属于对外承诺的文本，
    # 改动必须能追溯到人（合规场景下这是第一手证据）
    "legal_doc_update": "更新法律文档",
    # 支付渠道（2026-10-27）：改商户号/私钥/开关直接决定「钱能不能进来」，
    # 与订单、套餐这类业务数据分开记，便于资金问题复盘
    "payment_setting_update": "更新支付配置",
    # 邮件通道（2026-10-27）：SMTP 配错 = 全站注册与找回密码失效（用户只看到「发送失败」）
    "mail_setting_update": "更新邮件通道",
    # 发送测试邮件是一次**向外动作**：成败都记，才能回答「谁在什么时候往外发过信」
    # （反复试错把发信域名打进对方黑名单时，这条是唯一线索）
    "mail_test_send": "发送测试邮件",
    # AI 模型管理（2026-10-27）：改 Key/端点会改变全站 AI 调用去向与成本
    # 测试连接：不改配置，但会用库里的 Key 真的发一次请求
    "ai_model_test": "测试 AI 模型连接",
    "ai_model_create": "新建 AI 模型",
    "ai_model_update": "编辑 AI 模型",
    "ai_model_delete": "删除 AI 模型",
    # 定时任务（2026-10-27）：启停/改周期会改变系统维护行为的全部走向 ——
    # 关掉「VIP 过期降级」等于让过期用户继续吃付费权益，关掉「浏览量落库」会让浏览数停更
    "scheduled_job_update": "调整定时任务",
    # 手动执行一次：不改配置，但会产生数据变更（清理/推送/降级），必须能回答「谁点的」
    "scheduled_job_run": "手动执行定时任务",
    # AI 写作提示词（2026-10-28）：系统提示词与模板决定全站写作助手的输出行为，
    # 与 ai_model_* 同属「改一次、全站生效」的配置面；重置与更新分列，便于单独筛出回滚操作
    "ai_writing_prompt_update": "更新 AI 写作提示词",
    "ai_writing_prompt_reset": "恢复 AI 写作提示词默认值",
    # 图库导入（2026-10-29）：把第三方图片转存进站内正文，需能回答「谁导入了什么、授权是什么」
    "image_search_import": "导入图库图片",
}

# 目标类型（target_type）取值
# ⚠️ 这份清单同时是后台「按目标类型筛选」的下拉来源，写入口用了没登记的值 → 那条记录筛不出来。
# 第五轮审查 R5-03 补上此前已在使用却漏登记的 `setting`，以及评论/举报/留痕/部门四类。
AUDIT_TARGET_TYPES = (
    "user", "auth_request", "redeem_code", "role", "department", "tutorial", "resource", "plugin", "node",
    "announcement", "feedback", "setting", "comment", "report", "moderation_log", "ai_model",
    "ai_writing_prompt",
    "payment_product",
    # 第五轮审查 EXT-08：admin_tool_service 早就在写 target_type="tool"，但这里没登记，
    # 导致审计页目标列显示裸代码 `tool`（而 test_audit_taxonomy 因两张表同源派生而恒绿）
    "tool",
    # 定时任务：target_id 为空（job_id 是字符串，不是整型主键），任务名写在 summary 里
    "scheduled_job",
    # 法律文档：一次可能改多份，target_id 留空，键名在 detail 里
    "legal_doc",
    # 图库导入：target_id 是 image_assets.id
    "image_asset",
)

# 目标类型 → 中文名。**必须与 AUDIT_TARGET_TYPES 一一对应**（tests/test_audit_taxonomy.py 有守卫）：
# 后端存的是代码标识，界面要给中文；若这份映射漏项，界面就会直接显示 `setting` 这类裸代码。
# 放在后端而不是前端常量，是为了让「写入端登记了新类型」与「界面能显示它」同一处落地。
AUDIT_TARGET_TYPE_LABELS: dict[str, str] = {
    "user": "用户",
    "auth_request": "认证申请",
    "redeem_code": "兑换码",
    "role": "角色",
    "department": "部门",
    "tutorial": "教程",
    "resource": "资源",
    "plugin": "扩展",
    "node": "节点",
    "announcement": "公告",
    "payment_product": "充值套餐",
    "feedback": "问题反馈",
    "setting": "系统设置",
    "ai_model": "AI 模型",
    "ai_writing_prompt": "AI 写作提示词",
    "comment": "评论",
    "report": "举报",
    "moderation_log": "机审留痕",
    "tool": "工具",
    "scheduled_job": "定时任务",
    "legal_doc": "法律文档",
    "image_asset": "图片素材",
}

# ---- 风险分级 ----
# 审计页按等级配色与筛选。**每个动作码都必须归类**：漏归类会让新的高危动作在界面上
# 显示成「常规」（蓝），等于把最该被看见的操作藏起来 —— 这是典型的「静默变糟」。
# ⚠️ 新增动作码时必须同步本表，tests/test_audit_taxonomy.py 会断言两者完全对齐。

AUDIT_RISK_DANGER = "danger"  # 不可逆 / 提权 / 直接决定全站可见内容
AUDIT_RISK_MONEY = "money"    # 资金与权益（积分、会员、兑换码、实名）
AUDIT_RISK_NORMAL = "normal"  # 常规增删改

AUDIT_RISKS: tuple[str, ...] = (AUDIT_RISK_DANGER, AUDIT_RISK_MONEY, AUDIT_RISK_NORMAL)

AUDIT_RISK_LABELS: dict[str, str] = {
    AUDIT_RISK_DANGER: "高危",
    AUDIT_RISK_MONEY: "资金权益",
    AUDIT_RISK_NORMAL: "常规",
}

AUDIT_ACTION_RISK: dict[str, str] = {
    # 实名（直接决定 VIP 权益）
    "auth_approve": AUDIT_RISK_MONEY,
    "auth_reject": AUDIT_RISK_MONEY,
    # 用户
    "user_create": AUDIT_RISK_NORMAL,
    "user_update_role": AUDIT_RISK_DANGER,
    "user_update_vip": AUDIT_RISK_MONEY,
    "user_adjust_points": AUDIT_RISK_MONEY,
    "user_delete": AUDIT_RISK_DANGER,
    "user_batch_delete": AUDIT_RISK_DANGER,
    "user_restore": AUDIT_RISK_DANGER,
    # 兑换码（积分入金口）
    "redeem_code_create": AUDIT_RISK_MONEY,
    "redeem_code_disable": AUDIT_RISK_MONEY,
    # 权限体系：角色/部门/权限点的新增与删除都算提权面（R5-03）
    "role_permission_update": AUDIT_RISK_DANGER,
    "user_rbac_assign": AUDIT_RISK_DANGER,
    "role_create": AUDIT_RISK_DANGER,
    "role_update": AUDIT_RISK_DANGER,
    "role_delete": AUDIT_RISK_DANGER,
    "department_create": AUDIT_RISK_NORMAL,
    "department_update": AUDIT_RISK_NORMAL,
    "department_delete": AUDIT_RISK_NORMAL,
    # UGC 处置
    "comment_delete": AUDIT_RISK_NORMAL,
    "comment_restore": AUDIT_RISK_NORMAL,
    "report_handle": AUDIT_RISK_NORMAL,
    "moderation_review": AUDIT_RISK_NORMAL,
    # 公告（直接决定全站首屏；删除影响面最大）
    "announcement_create": AUDIT_RISK_NORMAL,
    "announcement_update": AUDIT_RISK_NORMAL,
    "announcement_delete": AUDIT_RISK_DANGER,
    "announcement_publish": AUDIT_RISK_NORMAL,
    # 支付套餐：改价/下架直接影响收入与用户权益
    "payment_product_create": AUDIT_RISK_DANGER,
    "payment_product_update": AUDIT_RISK_DANGER,
    "payment_product_delete": AUDIT_RISK_DANGER,
    # 问题反馈
    "feedback_update": AUDIT_RISK_NORMAL,
    "feedback_delete": AUDIT_RISK_NORMAL,
    # 站内消息：群发是面向全体用户的触达，发错一次就是全站可见事故 —— 与公告同级
    "message_send": AUDIT_RISK_DANGER,
    # 内容
    "content_create": AUDIT_RISK_NORMAL,
    "content_update": AUDIT_RISK_NORMAL,
    "content_publish": AUDIT_RISK_NORMAL,
    "content_delete": AUDIT_RISK_DANGER,
    # 恢复 = 把已下架内容放回可见面，与删除同级敏感（同样直接决定全站可见内容）
    "content_restore": AUDIT_RISK_DANGER,
    # 系统设置：可改 AI 供应商密钥与 base_url、邀请奖励额度、对外公示的法律文档 HTML
    "settings_update": AUDIT_RISK_DANGER,
    # 登录配置：关掉某个渠道会让一批用户再也登不进来；填错短信/微信凭据则全员登录失败
    "login_setting_update": AUDIT_RISK_DANGER,
    # 法律文档：对外公示的合规文本，改错等于对外的承诺变了（同属高危）
    "legal_doc_update": AUDIT_RISK_DANGER,
    # 支付配置：填错私钥/密钥会让支付静默失败；误开 Mock 会「不付钱直接到账」
    "payment_setting_update": AUDIT_RISK_MONEY,
    # 邮件通道：填错 SMTP = 全站注册与找回密码失效，且后端只记一条 warning，
    # 用户侧看到的是「发送失败」——按高危记，才能查出是哪次改动之后开始的
    "mail_setting_update": AUDIT_RISK_DANGER,
    # 测试邮件：不改配置，但是一次外向动作（会把本站发信 IP/域名暴露给收件方）
    "mail_test_send": AUDIT_RISK_NORMAL,
    # AI 模型：新建影响可用模型集合；改/删会切换全站实际调用的上游与成本
    "ai_model_test": AUDIT_RISK_NORMAL,
    "ai_model_create": AUDIT_RISK_DANGER,
    "ai_model_update": AUDIT_RISK_DANGER,
    "ai_model_delete": AUDIT_RISK_DANGER,
    # 定时任务：停掉/改周期会静默改变全站维护行为（权益降级、数据清理、收录推送），
    # 且不会有任何报错 —— 按高危记，便于事后回溯「是哪天开始不降级 VIP 的」
    "scheduled_job_update": AUDIT_RISK_DANGER,
    # 手动执行一次：不改配置，影响面与一次正常调度相同
    "scheduled_job_run": AUDIT_RISK_NORMAL,
    # AI 写作提示词：改提示词/模板会改变全站写作输出，但不碰密钥与资金 —— 与 ai_model_test 同按常规记
    "ai_writing_prompt_update": AUDIT_RISK_NORMAL,
    # 重置回内置默认值同样是配置写入，影响面与更新一致
    "ai_writing_prompt_reset": AUDIT_RISK_NORMAL,
    # 图库导入：不改配置，但会把外部图片与许可证信息落库
    "image_search_import": AUDIT_RISK_NORMAL,
}

# 未登记动作码的兜底等级：宁可标成「常规」也不报错 —— 审计不该反过来阻断业务写入
AUDIT_RISK_DEFAULT = AUDIT_RISK_NORMAL


class AdminAuditLog(Base):
    """管理端敏感操作审计记录

    索引设计（对应后台三种固定查询形态）：
    - ``(admin_id, created_at)``：按操作者回溯「某管理员都做了什么」；
    - ``(action, created_at)``：按动作类型排查「最近的提权操作」；
    - ``(target_type, target_id)``：按目标回溯「这个用户被谁改过」。
    """

    __tablename__ = "admin_audit_logs"
    __table_args__ = (
        Index("ix_aal_admin_created", "admin_id", "created_at"),
        Index("ix_aal_action_created", "action", "created_at"),
        Index("ix_aal_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, default=generate_id
    )
    # 操作者（⚠️ 无外键：管理员被删后审计必须仍在）
    admin_id: Mapped[int] = mapped_column(BigInteger, comment="操作者用户ID")
    admin_account: Mapped[str] = mapped_column(
        String(120), default="", comment="操作者账号快照(改名/注销后仍可追溯)"
    )
    action: Mapped[str] = mapped_column(String(50), comment="动作码(见 AUDIT_ACTIONS)")
    target_type: Mapped[str] = mapped_column(
        String(30), default="", comment="目标类型(user/tutorial/...)，批量操作为空"
    )
    # 目标 ID（⚠️ 无外键：目标被删后审计必须仍在；批量操作留空，明细在 detail）
    target_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, default=None, comment="目标ID(批量操作为空)"
    )
    summary: Mapped[str] = mapped_column(String(500), comment="人类可读摘要")
    # 结构化补充：只放安全字段（变更前后值、数量等），不放密码/token/完整资料
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="结构化明细(仅安全字段)")
    # 来源字段（第三轮增量审查 P1-49③ / 台账 P2-19）
    # 共享或被劫持的管理会话无法归因到来源 IP —— 只存「谁做了什么」无法回答
    # 「是不是同一个人的两台设备」。三者均由 request_context 中间件从本次请求
    # 提取，无请求上下文（脚本/测试）时为 NULL。
    ip: Mapped[str | None] = mapped_column(
        String(45), nullable=True, default=None, comment="来源IP(IPv6最长45字符)"
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(500), nullable=True, default=None, comment="User-Agent(截断500字符)"
    )
    request_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None, comment="请求关联ID(与日志/nginx对齐)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="操作时间"
    )
