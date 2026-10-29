"""用户模型"""

from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, DateTime, Boolean, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class User(Base):
    """用户表"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    account: Mapped[str] = mapped_column(String(100), unique=True, index=True, comment="手机号或邮箱")
    password_hash: Mapped[str] = mapped_column(String(255), comment="bcrypt 加密密码")
    role: Mapped[str] = mapped_column(String(20), default="user", index=True, comment="user/vip/admin")
    nickname: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="昵称")
    bio: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="个人简介")
    free_used_seconds: Mapped[int] = mapped_column(Integer, default=0, comment="免费已用时长(秒)")
    free_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="免费时长起点(注册时间)")
    points: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="积分余额(运行积分工具消耗)")
    vip_expire_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True, comment="VIP过期时间")
    pending_auth_code: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="待审核星球号")
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="头像URL")
    invite_code: Mapped[str | None] = mapped_column(String(16), nullable=True, unique=True, index=True, comment="专属邀请码(访问邀请页时惰性生成)")
    referrer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True, comment="邀请人ID(注册时通过邀请码绑定)")
    # 手机号：与 account 解耦——邮箱账号也能绑定手机号登录；历史「account 即手机号」由迁移回填。
    phone: Mapped[str | None] = mapped_column(
        String(20), nullable=True, unique=True, index=True, comment="手机号(唯一, 可与 account 不同)"
    )
    phone_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="手机号验证通过时间"
    )
    # 微信小程序登录标识（wx.login 的 code 换取的 openid）。
    # 仅由 miniapp 登录/绑定接口写入，Web 端账号恒为 NULL，不影响既有账号体系。
    wechat_openid: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True, comment="微信小程序 openid(仅小程序登录写入)")
    # 微信开放平台 UnionID：同一开放平台下 PC 网站 / 小程序 / 公众号可合并为一个账号。
    # 仅有小程序 openid 时无法跨应用识别，登录查号优先 unionid，其次各自 openid。
    wechat_unionid: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True, comment="微信 UnionID(跨应用账号合并)"
    )
    # PC 网站应用扫码登录的 openid（与小程序 openid 不是同一套 AppID）
    wechat_web_openid: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True, comment="微信开放平台网站应用 openid"
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
            "free_used_seconds": self.free_used_seconds,
            "points": self.points or 0,
            "vip_expire_time": self.vip_expire_time.isoformat() if self.vip_expire_time else None,
            "pending_auth_code": self.pending_auth_code,
            "avatar_url": self.avatar_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        return {k: v for k, v in data.items() if k not in exclude}
