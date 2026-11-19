"""登录日志模型：一次登录尝试（成功/失败）或登出事件的一行记录。

为什么要有它：审计表只记**管理员的后台操作**，普通用户的登录行为（撞库、
密码错、锁定）没有任何留痕 —— 排查「账号是不是被人试过」时只能翻应用日志，
而应用日志会滚动丢失。RuoYi / yudao 等主流后台都把登录日志作为标配。

隐私取舍：
- 账号经 `mask_account` 脱敏后再入库（手机号/邮箱只留头尾），失败尝试里
  混着大量攻击者的随机串，明文落库没有价值还放大泄露面；
- 不存密码派生物、不存 token —— 本表只回答「谁在什么时候从哪来、成没成」。

保留期 90 天，由 `scheduler/tasks.py::cleanup_login_logs` **每小时**清理。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id

# 事件状态：登录成功 / 登录失败 / 登出。失败原因（密码错、锁定、验证码错…）放 message
LOGIN_LOG_STATUSES = ("success", "failed", "logout")

LOGIN_LOG_STATUS_LABELS: dict[str, str] = {
    "success": "登录成功",
    "failed": "登录失败",
    "logout": "登出",
}


class LoginLog(Base):
    """一次登录/登出事件"""

    __tablename__ = "login_logs"
    __table_args__ = (
        # 「某账号最近的登录史」是排查盗号的第一入口：先按账号定位再按时间排序
        Index("ix_login_logs_account_created", "account", "created_at"),
        # 「某用户的登录史」（用户中心/管理端按人下钻）
        Index("ix_login_logs_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="用户ID（失败尝试可能查无此人，为空）")
    account: Mapped[str] = mapped_column(String(64), default="", comment="登录账号（mask_account 脱敏后）")
    status: Mapped[str] = mapped_column(String(16), default="failed", comment="success/failed/logout")
    message: Mapped[str] = mapped_column(String(255), default="", comment="结果说明（失败原因等）")
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="客户端 IP")
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="User-Agent（截断）")
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True, comment="请求追踪 ID（与应用日志串线索）")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True, comment="事件时间（UTC）"
    )
