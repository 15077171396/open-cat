"""个人访问令牌（PAT）模型：给「机器」用的长期凭据（2026-11-20，开放 API 第一块）。

为什么要有它：`/api/v1/content/*` 内容 API 已经独立成命名空间，但外部程序只能
复用**管理员的 JWT/cookie** 来调用 —— 等于把后台钥匙交出去。主流平台（Directus /
Strapi / GitHub / JeecgBoot AK-SK）都用「PAT」解决这个问题：可吊销、可过期、
作用域受限、泄露了不影响管理员登录态。

安全设计：
- **库里只存 SHA-256 摘要 + 展示前缀**，明文 token 只在创建响应里出现一次，
  丢了只能吊销重建 —— 与 GitHub PAT 同一姿势；
- 前缀 `oc_pat_` 让认证层一眼区分「机器凭据」与「人用的 JWT」，互不串道：
  PAT 只被内容命名空间的依赖接受，**永远不会**通过 get_current_user 进入
  /admin 与用户中心（见 app/dependencies.get_api_principal）；
- 作用域（scopes）白名单制：v1 只有 `content:read`，将来加新 scope 不需要迁移。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id

# 作用域白名单：新 scope 加进来即可，不需要迁移（列是 JSON）
PAT_SCOPE_CONTENT_READ = "content:read"
PAT_SCOPES = (PAT_SCOPE_CONTENT_READ,)
PAT_SCOPES_LABELS: dict[str, str] = {
    PAT_SCOPE_CONTENT_READ: "内容 API 只读",
}

# 明文前缀：认证层据此分流（JWT 解码前先看前缀，杜绝拿 PAT 撞 JWT 通道）
PAT_PREFIX = "oc_pat_"


class PersonalAccessToken(Base):
    """一个机器客户端的一把钥匙"""

    __tablename__ = "api_tokens"
    __table_args__ = (
        # 认证热路径：Bearer 摘要 → 唯一行。摘要唯一索引，查找 O(log n)
        Index("uq_api_tokens_token_hash", "token_hash", unique=True),
        Index("ix_api_tokens_owner", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    user_id: Mapped[int] = mapped_column(BigInteger, comment="属主管理员（吊销权限跟随属主）")
    name: Mapped[str] = mapped_column(String(100), comment="用途备注（展示用，如「渲染站渲染器」）")
    # 展示前缀（明文前 12 位含 oc_pat_）：列表里认得出哪把钥匙，又不泄露全文
    token_prefix: Mapped[str] = mapped_column(String(16), comment="明文前缀（含 oc_pat_，共 12 位）")
    token_hash: Mapped[str] = mapped_column(String(64), comment="SHA-256(明文全文) hex —— 认证唯一依据")
    scopes: Mapped[list] = mapped_column(JSON, default=list, comment='作用域白名单子集，如 ["content:read"]')
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="过期时刻（UTC，空=永不过期）")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近一次认证成功时刻")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="吊销时刻（吊销即失效，不可恢复）")
