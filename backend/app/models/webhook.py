"""Webhook 端点模型：内容事件的 HTTP 出站订阅（2026-11-20，开放 API 第二块）。

定位：PAT 解决「机器怎么调进来」，Webhook 解决「站内发生了事怎么推出去」。
内容发布/下架推给渲染站与搜索索引器，是自建站最常见的集成需求 —— 现在只能靠
渲染站轮询内容 API（浪费且滞后）。

可靠性取舍（v1 刻意从简）：
- 投递是**尽力而为**：3 次尝试、指数退避（0.5s/2s），最终失败只记录状态并
  累加 failure_streak —— 不进队列、不补投。把「必达」做进 v1 会把 webhooks
  变成第二个消息队列项目（对标文档 §五「明确不抄」的教训）；
- 失败不可怕，**看得见**就行：端点行的 last_status/last_failure 让运维在
  后台一眼看到哪个订阅断了。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id

# 事件白名单：新事件加进来即可（emit 端逐个接），不需要迁移
WEBHOOK_EVENT_TUTORIAL_PUBLISHED = "tutorial.published"
WEBHOOK_EVENT_TUTORIAL_UNPUBLISHED = "tutorial.unpublished"
WEBHOOK_EVENTS = (
    WEBHOOK_EVENT_TUTORIAL_PUBLISHED,
    WEBHOOK_EVENT_TUTORIAL_UNPUBLISHED,
)
WEBHOOK_EVENT_LABELS: dict[str, str] = {
    WEBHOOK_EVENT_TUTORIAL_PUBLISHED: "教程发布",
    WEBHOOK_EVENT_TUTORIAL_UNPUBLISHED: "教程下架",
}

WEBHOOK_DELIVERY_STATUSES = ("success", "failed")
# 投递超时（秒）：订阅方挂了不能拖住发布操作
WEBHOOK_TIMEOUT_SECONDS = 10
# 重试节奏：0.5s / 2s（第三次即最后一次尝试）
WEBHOOK_RETRY_DELAYS = (0.5, 2.0)


class WebhookEndpoint(Base):
    """一个出站订阅：URL + 事件子集 + 签名密钥"""

    __tablename__ = "webhooks"
    __table_args__ = (
        Index("ix_webhooks_active", "active", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    name: Mapped[str] = mapped_column(String(100), comment="用途备注（展示用）")
    url: Mapped[str] = mapped_column(String(500), comment="回调地址（仅 https；保存时校验）")
    # 签名密钥：Fernet 加密落库（core/crypto），泄露面收敛到「有库无钥」的环境
    secret_encrypted: Mapped[str] = mapped_column(String(500), comment="Fernet 加密的签名密钥（enc:v2: 版本化）")
    events: Mapped[list] = mapped_column(JSON, default=list, comment="订阅的事件名子集（WEBHOOK_EVENTS）")
    active: Mapped[bool] = mapped_column(default=True, comment="停用后不再投递，但保留配置")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # 投递状态（最近一次）：success / failed / None=从未投递
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_event: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="最近投递的事件名")
    last_delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="最近失败原因（截断）")
    failure_streak: Mapped[int] = mapped_column(default=0, comment="连续失败次数（成功即清零）")
