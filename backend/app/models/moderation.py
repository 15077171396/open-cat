"""内容审核模型，两张表分工不同不要合并：
ContentModerationLog 机器判定留痕（未命中不写日志，防表随 UGC 膨胀）；ContentReport 人工举报（机器漏判兜底）。
两者都只存定位信息 + 文本摘要不复制正文：审核动作应作用于业务表，避免双写不一致。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id

# UGC 内容类型（举报与审核日志共用同一套枚举，便于联表统计）
# "comment" 为台账 P1-9 教程评论新增：评论是公开可见的 UGC，风险等级与昵称/简介同级
CONTENT_TYPES = (
    "feedback", "nickname", "bio", "star_code", "avatar", "tutorial", "resource", "comment",
    "node",
)

# 审核日志的处置动作
MODERATION_ACTIONS = ("reject", "mask", "review")
# 审核日志的人工复核结论（机器已处置的内容，人工可推翻）
MODERATION_STATUSES = ("pending", "approved", "rejected")

# 举报处理状态
REPORT_STATUSES = ("pending", "resolved", "dismissed")
# 举报理由（给用户下拉用，不自由填写，便于统计高频问题）
REPORT_REASONS = ("spam", "abuse", "illegal", "infringement", "other")

# 举报理由展示名（与 REPORT_REASONS 一一对应）
REPORT_REASON_LABELS = {
    "spam": "垃圾广告",
    "abuse": "辱骂攻击",
    "illegal": "违法违规",
    "infringement": "侵权抄袭",
    "other": "其他",
}

# ---- 展示名（界面文案的唯一事实来源）----
# ⚠️ 为什么放在后端而不是管理端常量：这些映射**必须与上面的取值元组逐项对齐**，
# 而管理端是另一门语言、另一份代码，写入端新增取值时它不会报错，只会静默显示成裸代码。
# 真实事故：`CONTENT_TYPES` 加了 "comment" 之后管理端 `CONTENT_TYPE_LABELS` 没跟上，
# 审核台的「内容类型」列直接显示成 `comment`，一路没人发现。
# 现在由 tests/test_moderation_labels.py 断言四张表的键与对应元组完全相等。
CONTENT_TYPE_LABELS = {
    "feedback": "问题反馈",
    "nickname": "昵称",
    "bio": "个人简介",
    "star_code": "星球号",
    "avatar": "头像",
    "tutorial": "教程",
    "resource": "资源",
    "comment": "评论",
    "node": "百科节点",
}

MODERATION_ACTION_LABELS = {
    "reject": "拒绝提交",
    "mask": "打码放行",
    "review": "放行待复核",
}

MODERATION_STATUS_LABELS = {
    "pending": "待复核",
    "approved": "已放行",
    "rejected": "已拦截",
}

REPORT_STATUS_LABELS = {
    "pending": "待处理",
    "resolved": "已处理",
    "dismissed": "已驳回",
}


class ContentModerationLog(Base):
    """敏感词命中留痕（机器判定 + 人工复核）

    excerpt 只存前 200 字的纯文本摘要，供管理员判断是否需要放行；
    不存完整正文，避免违规内容在本表二次留存放大。
    """

    __tablename__ = "content_moderation_logs"
    __table_args__ = (
        Index("ix_cml_status_created", "status", "created_at"),
        Index("ix_cml_type_target", "content_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    content_type: Mapped[str] = mapped_column(String(20), comment="内容类型: feedback/nickname/bio/star_code/avatar")
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="内容定位ID(用户ID/反馈ID等)")
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="内容作者ID")
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True, comment="文本摘要(最多200字)")
    hit_words: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="命中的敏感词(逗号分隔)")
    action: Mapped[str] = mapped_column(String(20), comment="处置动作: reject/mask/review")
    status: Mapped[str] = mapped_column(String(20), default="pending", comment="复核状态: pending/approved/rejected")
    reviewer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="复核管理员ID")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="复核时间")
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="复核备注(如判定为误报)")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="命中时间")


class ContentReport(Base):
    """用户举报

    同一用户对同一内容只允许举报一次（唯一约束），防止刷举报把内容顶下去。
    """

    __tablename__ = "content_reports"
    __table_args__ = (
        Index("ix_cr_status_created", "status", "created_at"),
        Index("ix_cr_type_target", "content_type", "target_id"),
        # 同一用户对同一内容只允许举报一次，防刷举报把内容顶下去。
        # ⚠️ 必须与迁移 0025 保持同步：只写在迁移里、模型不声明的话，
        # alembic autogenerate 会判定为「多余的约束」并生成 DROP，
        # 且 ORM 层无法在 flush 前发现冲突（只能等数据库抛错）。
        UniqueConstraint(
            "reporter_id", "content_type", "target_id", name="uq_report_user_target"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    reporter_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="举报人ID")
    content_type: Mapped[str] = mapped_column(String(20), comment="内容类型")
    target_id: Mapped[str] = mapped_column(String(64), comment="内容定位ID")
    reason: Mapped[str] = mapped_column(String(20), comment="举报理由: spam/abuse/illegal/infringement/other")
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="补充说明")
    status: Mapped[str] = mapped_column(String(20), default="pending", comment="处理状态: pending/resolved/dismissed")
    handler_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="处理管理员ID")
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="处理时间")
    handle_note: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="处理备注(内部)")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="举报时间")


# 单用户举报频次上限（防刷举报），用于举报服务校验
MAX_REPORTS_PER_USER_PER_DAY = 20
# 单用户每日「敏感词命中留痕」条数上界（第五轮审查 R5-11）。
# 留痕在业务被拒时也要落库，而业务侧每日配额数的是业务行数（被拒 = 0 行，永不计数），
# 所以需要这一层独立上界，否则一条固定违规正文循环提交就能无限写留痕、淹掉审核台。
MAX_MODERATION_LOGS_PER_USER_PER_DAY = 20
# 摘要截断长度
EXCERPT_MAX_LENGTH = 200
# 举报补充说明长度
REPORT_DETAIL_MAX_LENGTH = 500
