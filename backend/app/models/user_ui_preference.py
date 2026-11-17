"""用户界面偏好与锁屏口令（每用户单条）。

为什么单独建表而不是塞进 `users`：这是「外观/本机保护」的自服务数据，与账号身份
（密码、角色、令牌版本）生命周期不同 —— 单独成表可以按用户整行 upsert，也不会让
用户表在每次改主题色时被无谓更新。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserUiPreference(Base):
    """`preferences` 存白名单校验后的界面偏好 JSON；锁屏口令只存 bcrypt 摘要。"""

    __tablename__ = "user_ui_preferences"

    user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="用户 ID"
    )
    preferences: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="界面偏好 JSON（仅白名单字段）"
    )
    lock_password_hash: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="锁屏口令 bcrypt 摘要（绝不返回/记日志）"
    )
    lock_password_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="锁屏口令最近更新时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
