"""个人访问令牌服务：签发 / 列表 / 吊销 / 内容命名空间认证（2026-11-20）。

认证路径只有一条：内容 API 依赖拿 Bearer oc_pat_ 前缀令牌，SHA-256 全文查唯一索引，
逐项检查吊销 / 过期 / 作用域，fail-closed。不存在 PAT 换 JWT、PAT 进入 admin 路由的通道。
查询统一「字面语句 + 绑定参数」惯用法（与 login_log_service 同款口径）。
"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.degrade import degrade_guard
from app.core.exceptions import AppException
from app.database import async_session
from app.models.api_token import (
    PAT_PREFIX,
    PAT_SCOPES,
    PAT_SCOPE_CONTENT_READ,
    PersonalAccessToken,
)
from app.models.user import User
from app.services.admin_audit_service import record_admin_action
from app.utils.time import utcnow

# last_used_at 回写节流：认证热路径不逐请求写库（每 token 每 5 分钟至多一次）。
# 多 worker 下各算各的 —— 节流是 best-effort，不是精确审计。
_LAST_USED_WRITE_INTERVAL = 300.0
_last_used_written: dict[int, float] = {}

# 字面语句：值一律走绑定参数（:digest / :token_id / :last_used_at），不拼接任何 SQL 片段
_SQL_TOKEN_BY_HASH = text(
    "SELECT id, user_id, name, token_prefix, scopes, expires_at, revoked_at "
    "FROM api_tokens WHERE token_hash = :digest"
)
_SQL_TOUCH = text("UPDATE api_tokens SET last_used_at = :last_used_at WHERE id = :token_id")


def _hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def validate_scopes(scopes: list[str] | None) -> list[str]:
    """校验请求的作用域白名单子集；未知作用域直接拒绝（不静默丢弃）"""
    checked = list(scopes or [PAT_SCOPE_CONTENT_READ])
    unknown = [s for s in checked if s not in PAT_SCOPES]
    if unknown:
        raise AppException(code=400, message=f"未知的作用域：{', '.join(unknown)}")
    return list(dict.fromkeys(checked))


async def create_token(
    db: AsyncSession,
    admin: User,
    name: str,
    scopes: list[str] | None,
    expires_days: int | None,
) -> dict:
    """签发新令牌：明文只在本次响应里出现一次，库里只留 SHA-256 摘要与展示前缀。"""
    checked = validate_scopes(scopes)
    plain = f"{PAT_PREFIX}{secrets.token_hex(20)}"
    expires_at = None
    if expires_days and int(expires_days) > 0:
        expires_at = utcnow().replace(tzinfo=None) + timedelta(days=int(expires_days))
    row = PersonalAccessToken(
        user_id=admin.id,
        name=name.strip()[:100] or "未命名令牌",
        token_prefix=plain[:12],
        token_hash=_hash_token(plain),
        scopes=checked,
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()
    record_admin_action(
        db,
        admin,
        "api_token_create",
        target_type="api_token",
        target_id=row.id,
        summary=f"签发访问令牌「{row.name}」",
        detail={"scopes": checked, "expires_days": expires_days or 0, "prefix": row.token_prefix},
    )
    await db.commit()
    return {
        "id": row.id,
        "token": plain,
        "prefix": row.token_prefix,
        "scopes": checked,
        "expires_at": expires_at,
    }


async def list_tokens(db: AsyncSession) -> list[dict]:
    """令牌清单（只有展示字段，不含摘要；最多 200 条，创建时间倒序）。"""
    now = utcnow().replace(tzinfo=None)
    result = await db.execute(
        select(PersonalAccessToken)
        .order_by(PersonalAccessToken.created_at.desc())
        .limit(200)
    )
    rows = result.scalars().all()
    items = []
    for row in rows:
        items.append(
            {
                "id": row.id,
                "name": row.name,
                "prefix": row.token_prefix,
                "scopes": list(row.scopes or []),
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
                "revoked": row.revoked_at is not None,
                "expired": bool(row.expires_at and row.expires_at < now),
            }
        )
    return items


async def revoke_token(db: AsyncSession, admin: User, token_id: int) -> bool:
    """吊销（幂等）：已吊销返回 False；置 revoked_at 并与审计同事务提交。"""
    row = await db.get(PersonalAccessToken, token_id)
    if row is None:
        raise AppException(code=404, message="令牌不存在")
    if row.revoked_at is not None:
        return False
    row.revoked_at = utcnow().replace(tzinfo=None)
    record_admin_action(
        db,
        admin,
        "api_token_revoke",
        target_type="api_token",
        target_id=row.id,
        summary=f"吊销访问令牌「{row.name}」",
        detail={"prefix": row.token_prefix},
    )
    await db.commit()
    return True


async def authenticate(plain_token: str, db: AsyncSession) -> dict | None:
    """内容 API 认证入口：非 PAT 前缀返回 None（交给 JWT 通道），命中前缀则 fail-closed。

    明文全文只在此函数内出现一次（哈希前），库里与日志只有摘要 / 前缀。
    """
    if not plain_token.startswith(PAT_PREFIX):
        return None
    digest = _hash_token(plain_token)
    row = (await db.execute(_SQL_TOKEN_BY_HASH, {"digest": digest})).mappings().first()
    if row is None:
        raise AppException(code=401, message="访问令牌无效")
    now = utcnow().replace(tzinfo=None)
    if row["revoked_at"] is not None:
        raise AppException(code=401, message="访问令牌已被吊销")
    if row["expires_at"] is not None and row["expires_at"] < now:
        raise AppException(code=401, message="令牌已过期")
    scopes = list(row["scopes"] or [])
    if PAT_SCOPE_CONTENT_READ not in scopes:
        raise AppException(code=401, message="访问令牌缺少 content:read 作用域")
    token_id = int(row["id"])
    # last_used_at 回写（节流 + best-effort）：独立会话 + degrade_guard，失败只记
    # [降级-放行] 日志，绝不影响本次认证结果（与 login_log_service.record 同向）
    monotonic_now = time.monotonic()
    last_written = _last_used_written.get(token_id)
    if last_written is None or monotonic_now - last_written >= _LAST_USED_WRITE_INTERVAL:
        with degrade_guard("api_token.touch", detail="last_used_at 回写失败（不影响本次认证）"):
            async with async_session() as session:
                await session.execute(_SQL_TOUCH, {"last_used_at": now, "token_id": token_id})
                await session.commit()
                _last_used_written[token_id] = monotonic_now
    return {
        "id": token_id,
        "user_id": row["user_id"],
        "name": row["name"],
        "token_prefix": row["token_prefix"],
        "scopes": scopes,
        "expires_at": row["expires_at"],
    }
