"""用户模型"""

from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime, Boolean, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class User(Base):
    """用户表"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    account: Mapped[str] = mapped_column(String(100), unique=True, index=True, comment="手机号或邮箱")
    password_hash: Mapped[str] = mapped_column(String(255), comment="bcrypt 加密密码")
    role: Mapped[str] = mapped_column(String(20), default="user", index=True, comment="user/admin")
    nickname: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="昵称")
    bio: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="个人简介")
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="头像URL")
    # 手机号：与 account 解耦——邮箱账号也能绑定手机号；历史「account 即手机号」由迁移回填。
    phone: Mapped[str | None] = mapped_column(
        String(20), nullable=True, unique=True, index=True, comment="手机号(唯一, 可与 account 不同)"
    )
    phone_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="手机号验证通过时间"
    )
    # 部门归属（RBAC 组织架构展示用，不参与权限判定）
    department_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True, comment="部门ID(组织归属)")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="注册时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None, index=True, comment="软删除时间(注销标记, NULL=正常)")
    # ---- TOTP 二次验证（2026-10-27）----
    # 密钥**加密后**落库（core/crypto.py，SECRET_KEY 派生）：拖库者拿到密文也算不出验证码。
    totp_secret: Mapped[str | None] = mapped_column(
        String(255), nullable=True, default=None, comment="TOTP 密钥(加密存储, NULL=未绑定)"
    )
    # 与 secret 分开：绑定时先写 secret 但 enabled=0，用户用 App 验证通过才置 1，
    # 避免「扫了码但没验证」把账号锁死（密钥没存对就再也登录不进去）
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", comment="是否启用二次验证(1=登录需验证码)"
    )
    # 恢复码只存 sha256（一次性）：手机丢了还能靠它登录，否则账号永久锁死
    totp_recovery_hashes: Mapped[list | None] = mapped_column(
        JSON, nullable=True, default=None, comment="恢复码哈希列表(一次性, NULL=无)"
    )

    def to_dict(self, exclude: set = None) -> dict:
        """转为字典 (排除敏感字段)"""
        exclude = exclude or set()
        data = {
            "id": self.id,
            "account": self.account,
            "role": self.role,
            "nickname": self.nickname,
            "bio": self.bio,
            "avatar_url": self.avatar_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        return {k: v for k, v in data.items() if k not in exclude}
