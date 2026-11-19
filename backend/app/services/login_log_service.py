"""登录日志服务。

写入用「独立会话 + degrade_guard 吞异常」：登录日志是安全遥测，失败记录恰恰
是最需要留下的那一条（失败时业务事务已回滚）；写日志失败绝不影响登录主流程
（与 search_log_service 同向）。账号一律 mask_account 脱敏后入库，查询按脱敏后
精确匹配。保留期 90 天，由 scheduler/tasks.py::cleanup_login_logs 每小时清理。
SQL 纪律：只用内联字面 SQL + 具名占位符，可选筛选统一用
「:param IS NULL OR col = :param」惯用法，不拼接任何 SQL 片段。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.degrade import degrade_guard
from app.core.exceptions import AppException
from app.core.request_context import get_client_ip, get_request_id, get_user_agent
from app.database import async_session
from app.models.login_log import LOGIN_LOG_STATUSES
from app.utils.time import utcnow
from app.utils.verification import mask_account

LOGIN_LOG_RETENTION_DAYS = 90
_UA_MAX = 255
_MSG_MAX = 255
_ACCOUNT_MAX = 64
_IP_MAX = 64


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _mask(value: str | None) -> str:
    return (mask_account(value) or "") if value else ""


def _strip(value: str | None, limit: int) -> str | None:
    cleaned = (value or "").strip()
    return cleaned[:limit] if cleaned else None


async def record(
    *,
    status: str,
    user_id: int | None = None,
    account: str = "",
    message: str = "",
    ip: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
) -> None:
    """记录一条登录事件（绝不抛异常、绝不影响登录主流程）。"""
    if status not in LOGIN_LOG_STATUSES:
        status = "failed"
    row = {
        "user_id": user_id,
        "account": (mask_account(account) or "")[:_ACCOUNT_MAX] if account else "",
        "status": status,
        "message": (message or "")[:_MSG_MAX],
        "ip": _strip(ip, _IP_MAX) or (get_client_ip() or "")[:_IP_MAX] or None,
        "user_agent": _strip(user_agent, _UA_MAX) or (get_user_agent() or "")[:_UA_MAX] or None,
        "request_id": (request_id or get_request_id() or "")[:36] or None,
    }
    with degrade_guard("login_log.record", detail="登录日志写入失败（不影响登录本身）"):
        async with async_session() as session:
            await session.execute(
                text(
                    "INSERT INTO login_logs "
                    "(user_id, account, status, message, ip, user_agent, request_id, created_at) "
                    "VALUES (:user_id, :account, :status, :message, :ip, :user_agent, :request_id, UTC_TIMESTAMP())"
                ),
                row,
            )
            await session.commit()


async def list_logs(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    account: str | None = None,
    user_id: int | None = None,
    status: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> dict:
    """登录日志列表（时间倒序 + id 兜底稳定排序）。筛选条件见上方 IS NULL 惯用法。"""
    masked = _mask(account)
    if status and status != "all" and status not in LOGIN_LOG_STATUSES:
        raise AppException(code=400, message="状态筛选值无效")
    params = {
        "account": masked or None,
        "user_id": user_id,
        "status": status if status and status != "all" else None,
        "start_at": _naive(created_after),
        "end_at": _naive(created_before),
        "limit": int(page_size),
        "offset": (int(page) - 1) * int(page_size),
    }
    total = (await db.execute(text(
        "SELECT COUNT(*) FROM login_logs WHERE "
        "(:account IS NULL OR account = :account) "
        "AND (:user_id IS NULL OR user_id = :user_id) "
        "AND (:status IS NULL OR status = :status) "
        "AND (:start_at IS NULL OR created_at >= :start_at) "
        "AND (:end_at IS NULL OR created_at <= :end_at)"
    ), params)).scalar() or 0
    rows = (await db.execute(text(
        "SELECT id, user_id, account, status, message, ip, user_agent, request_id, created_at "
        "FROM login_logs WHERE "
        "(:account IS NULL OR account = :account) "
        "AND (:user_id IS NULL OR user_id = :user_id) "
        "AND (:status IS NULL OR status = :status) "
        "AND (:start_at IS NULL OR created_at >= :start_at) "
        "AND (:end_at IS NULL OR created_at <= :end_at) "
        "ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset"
    ), params)).all()

    items = []
    for row in rows:
        m = row._mapping
        items.append({
            "id": int(m["id"]),
            "user_id": m["user_id"],
            "account": m["account"] or "",
            "status": m["status"] or "",
            "message": m["message"] or "",
            "ip": m["ip"],
            "user_agent": m["user_agent"],
            "request_id": m["request_id"],
            "created_at": m["created_at"].isoformat() if m["created_at"] else None,
        })
    return {"items": items, "total": int(total), "page": int(page), "page_size": int(page_size)}


async def delete_logs(db: AsyncSession, ids: list[int] | None) -> int:
    """按 ID 批量删除（执行不提交，由调用方 commit；逐条绑定参数）。"""
    deleted = 0
    for raw in ids or []:
        try:
            log_id = int(raw)
        except (TypeError, ValueError):
            continue
        if log_id <= 0:
            continue
        result = await db.execute(
            text("DELETE FROM login_logs WHERE id = :log_id"), {"log_id": log_id}
        )
        deleted += int(result.rowcount or 0)
    return deleted


async def delete_logs_with_audit(db: AsyncSession, admin, ids: list[int] | None) -> int:
    """删除 + 审计 + 提交（事务边界在 service 层，API 层只做参数校验）。

    删除登录日志 = 抹掉安全线索，与审计同事务提交（回滚即不留审计行）。
    """
    from app.services.admin_audit_service import record_admin_action, summarize_ids

    deleted = await delete_logs(db, ids)
    if deleted:
        record_admin_action(
            db,
            admin,
            "login_log_delete",
            target_type="login_log",
            summary=f"删除登录日志 {deleted} 条",
            detail={"ids": summarize_ids([int(i) for i in ids or [] if str(i).isdigit()])},
        )
        await db.commit()
    return deleted


async def count_failed_since(db: AsyncSession, since: datetime) -> int:
    """近一段时间登录失败数（指标快照用；表不存在等异常由调用方降级处理）。"""
    row = (await db.execute(text(
        "SELECT COUNT(*) FROM login_logs WHERE status = 'failed' AND created_at >= :since"
    ), {"since": _naive(since)})).scalar()
    return int(row or 0)


async def cleanup_expired(db: AsyncSession, keep_days: int = LOGIN_LOG_RETENTION_DAYS) -> int:
    """删除超过保留期的登录日志（执行不提交，由调用方 commit）。"""
    days = max(1, int(keep_days))
    cutoff = utcnow().replace(tzinfo=None) - timedelta(days=days)
    with degrade_guard("login_log.cleanup", detail="登录日志清理失败"):
        result = await db.execute(
            text("DELETE FROM login_logs WHERE created_at < :cutoff"), {"cutoff": cutoff}
        )
        return int(result.rowcount or 0)
