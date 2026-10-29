"""管理操作审计服务：record_admin_action 写审计行（不提交，随业务事务同生共死，须在 commit 之前调用）；
list_audit_logs 后台查询。⚠️ 审计写入绝不能拖垮业务：拿不到 admin id 时只记 warning 跳过、不抛异常。"""

from datetime import datetime, timedelta, timezone
import json
from typing import Optional

from loguru import logger
from sqlalchemy import delete, func, or_, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_context import get_client_ip, get_request_id, get_user_agent
from app.core.exceptions import AppException
from app.models.admin_audit_log import (
    AUDIT_ACTIONS,
    AUDIT_ACTION_RISK,
    AUDIT_RISKS,
    AUDIT_RISK_DEFAULT,
    AUDIT_TARGET_TYPE_LABELS,
    AdminAuditLog,
)
from app.models.user import User
from app.utils.sanitize import escape_like

# detail 里 id 列表最多存多少个（批量操作可能几百个，全存会把审计表撑起来）
MAX_DETAIL_IDS = 50

# 审计保留期 365 天：审计表只增不减，没有保留策略等于把存储成本与泄露面无限放大
AUDIT_LOG_RETENTION_DAYS = 365

# 来源字段截断（与模型列宽一致；UA 可能极长，超长直接截断）
_IP_MAX = 45
_USER_AGENT_MAX = 500
_REQUEST_ID_MAX = 64


def summarize_ids(ids: list[int]) -> list[int]:
    """截断 id 列表，超出部分在末尾用 -1 占位提示「还有更多」。

    detail 只用于辅助定位，不需要完整名单；真正要完整名单时可按时间回查业务日志。
    """
    if len(ids) <= MAX_DETAIL_IDS:
        return list(ids)
    return list(ids[:MAX_DETAIL_IDS]) + [-1]


# 字段级新旧值：只有字段名答不了「从谁改成谁」，须记录新旧值
AUDIT_VALUE_MAX = 120

# 命中即整体不落值（连「旧值长度」都不落，避免通过长度旁泄）。
# 本项目这几项都已是密文或掩码，但仍按「明文敏感字段」口径处理，
# 以防将来有新增字段沿用本 helper。
_SENSITIVE_KEY_PARTS = ("password", "passwd", "secret", "token", "api_key", "apikey", "private_key", "code")

# 读不到基线时的占位。刻意不用 None —— 那些字段的合法旧值就经常是 NULL，
# 把「没记到」写成 NULL 等于伪造出一条「从空值改起」的历史。
_NO_BASELINE = "(未记录)"


def _audit_value(value) -> object:
    """把任意列值压成可入库的小值：超长截断，非标量转 str。"""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    else:
        value = str(value)
    if len(value) > AUDIT_VALUE_MAX:
        value = value[:AUDIT_VALUE_MAX] + "…"
    return value


def audit_changes(obj, updates: dict) -> dict:
    """`{字段: [旧值, 新值]}`，供 `record_admin_action(detail=...)` 使用。

    旧值取自 SQLAlchemy 的 attribute history —— 因此**在赋值之后调用也拿得到**，
    不必在各个写操作前插快照行（8 个调用点各插一行、插错位置就静默记成新值当旧值，
    这个风险比 helper 本身大）。

    ⚠️ 依赖「对象是从会话里读出来的」（这些 service 都是 `select(...)` 后直接改属性）。
    内存仿真会话（tests/fake_db）里的对象没有被会话托管，取不到基线，
    旧值会落成 `_NO_BASELINE` —— 这是刻意的显式缺口，不是静默丢数据；
    真实数据库路径由 tests/test_admin_audit.py 里的单元测试用 `set_committed_value` 验证。
    """
    state = sa_inspect(obj, raiseerr=False)
    changes: dict = {}
    for field in updates:
        if any(part in field.lower() for part in _SENSITIVE_KEY_PARTS):
            changes[field] = ["***", "***"]
            continue
        deleted: tuple = ()
        added: tuple = ()
        unchanged: tuple = ()
        if state is not None:
            try:
                history = state.attrs[field].history
                deleted, added, unchanged = history.deleted, history.added, history.unchanged
            except Exception:  # noqa: BLE001 审计取值失败不该拖垮业务写入
                logger.warning(f"audit_changes: 读取 {type(obj).__name__}.{field} 的历史失败")
        new_value = added[-1] if added else (unchanged[-1] if unchanged else getattr(obj, field, None))
        changes[field] = [
            _audit_value(deleted[-1]) if deleted else _NO_BASELINE,
            _audit_value(new_value),
        ]
    return changes


def record_admin_action(
    db: AsyncSession,
    admin: User | None,
    action: str,
    *,
    target_type: str = "",
    target_id: Optional[int] = None,
    summary: str = "",
    detail: Optional[dict] = None,
) -> None:
    """把一条管理操作写入审计表（**只 add，不 commit**）。

    :param action: 动作码，必须在 :data:`AUDIT_ACTIONS` 中登记（未登记会打 warning
        但仍记录 —— 漏登记只影响后台筛选下拉，不该让业务操作失败）。
    """
    admin_id = getattr(admin, "id", None)
    if admin_id is None:
        # 拿不到操作者就无法归因，记 warning 后跳过（审计不能反过来阻断业务）
        logger.warning(f"审计跳过（无法识别操作者）: action={action} summary={summary}")
        return

    if action not in AUDIT_ACTIONS:
        logger.warning(f"未登记的管理动作码被写入审计: {action}（请补进 AUDIT_ACTIONS）")

    db.add(
        AdminAuditLog(
            admin_id=admin_id,
            # 账号快照：账号可改名/注销，只存 id 事后无法回答「当时是谁」
            admin_account=(getattr(admin, "account", "") or "")[:120],
            action=action,
            target_type=target_type or "",
            target_id=target_id,
            summary=(summary or "")[:500],
            detail=detail or None,
            # 来源字段（P1-49③）：从请求上下文取，无请求上下文（脚本/测试）为 NULL。
            # 共享/被劫持会话的归因依据 —— 「同一个人两台设备」只能靠 IP+UA 区分。
            ip=(get_client_ip() or "").strip()[:_IP_MAX] or None,
            user_agent=(get_user_agent() or "")[:_USER_AGENT_MAX] or None,
            request_id=(get_request_id() or "")[:_REQUEST_ID_MAX] or None,
        )
    )


async def cleanup_expired_audit_logs(
    db: AsyncSession, *, retention_days: int = AUDIT_LOG_RETENTION_DAYS
) -> int:
    """删除超过保留期的审计行，返回删除条数（**只执行不提交**，由调用方 commit）。

    与 ``record_admin_action`` 同一事务约定：service 不拥有事务，
    调用方（scheduler）负责提交 —— 清理失败时整体回滚，不会删一半留一半。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = await db.execute(delete(AdminAuditLog).where(AdminAuditLog.created_at < cutoff))
    return int(result.rowcount or 0)


async def list_audit_logs(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    action: Optional[str] = None,
    admin_id: Optional[int] = None,
    target_type: Optional[str] = None,
    keyword: Optional[str] = None,
    risk: Optional[str] = None,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> dict:
    """审计日志列表（按时间倒序）

    - ``keyword`` 匹配摘要与操作者账号 —— 这两种是排查时最常用的入口
      （「谁把张三提权了」「某管理员最近干了什么」）。
    - ``risk`` 按风险等级筛选。**等级不是列，是 `action → 等级` 的映射**，
      所以这里翻译成「该等级对应的动作码集合」再下推到 SQL —— 否则只能取回来再用
      Python 过滤，那样 `total` 与分页就全错了。
    - ``start_at`` / ``end_at`` 按发生时间，**闭区间且必须是 naive UTC**
      （由路由层用 `utils.time.parse_iso_to_naive_utc` 换算，别在这里猜时区）。
    """
    conditions = []
    if action and action != "all":
        conditions.append(AdminAuditLog.action == action)
    if admin_id:
        conditions.append(AdminAuditLog.admin_id == admin_id)
    if target_type and target_type != "all":
        conditions.append(AdminAuditLog.target_type == target_type)
    if risk and risk != "all":
        if risk not in AUDIT_RISKS:
            raise AppException(code=400, message="风险等级筛选值无效")
        conditions.append(
            AdminAuditLog.action.in_(
                [code for code, level in AUDIT_ACTION_RISK.items() if level == risk]
            )
        )
    if start_at is not None:
        conditions.append(AdminAuditLog.created_at >= start_at)
    if end_at is not None:
        conditions.append(AdminAuditLog.created_at <= end_at)
    if keyword:
        kw = escape_like(keyword)
        conditions.append(
            or_(
                AdminAuditLog.summary.contains(kw, escape="\\"),
                AdminAuditLog.admin_account.contains(kw, escape="\\"),
            )
        )

    total = (
        await db.execute(select(func.count(AdminAuditLog.id)).where(*conditions))
    ).scalar()

    rows = (
        await db.execute(
            select(AdminAuditLog)
            .where(*conditions)
            # 稳定排序：同秒内多条记录时用 id 兜底，避免翻页重复/漏项
            .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    items = [
        {
            "id": row.id,
            "admin_id": row.admin_id,
            "admin_account": row.admin_account,
            "action": row.action,
            "action_label": AUDIT_ACTIONS.get(row.action, row.action),
            # 风险等级由后端给出：前端若自己维护一份「动作码 → 等级」的表，
            # 新增高危动作时会静默显示为「常规」，等于把最该被看见的操作藏起来
            "risk_level": AUDIT_ACTION_RISK.get(row.action, AUDIT_RISK_DEFAULT),
            "target_type": row.target_type,
            "target_type_label": AUDIT_TARGET_TYPE_LABELS.get(row.target_type, row.target_type),
            "target_id": row.target_id,
            "summary": row.summary,
            "detail": row.detail,
            # 来源字段（P1-49③）：历史行为 NULL，前端按缺失展示
            "ip": row.ip,
            "user_agent": row.user_agent,
            "request_id": row.request_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}
