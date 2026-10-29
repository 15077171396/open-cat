"""积分服务：流水记录 / 查询 / 管理端调整

事务语义：所有写操作仅 add/flush，由 get_db 统一提交或回滚，
保证「余额变动」与「流水记录」原子一致（工具扣费失败自动回滚流水）。
"""

from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.user import User
from app.models.point_transaction import PointTransaction
from app.core.exceptions import AppException
from app.services.admin_audit_service import record_admin_action
from app.utils.sanitize import escape_like

# 流水类型常量
TYPE_CONSUME = "consume"        # 工具消耗
TYPE_ADMIN_SET = "admin_set"    # 管理员设置
TYPE_ADMIN_GRANT = "admin_grant"  # 管理员充值
TYPE_ADMIN_DEDUCT = "admin_deduct"  # 管理员扣减
TYPE_REDEEM = "redeem"          # 兑换码充值
TYPE_INVITE = "invite"          # 邀请好友注册奖励
TYPE_RECHARGE = "recharge"      # 在线支付充值

VALID_TYPES = (TYPE_CONSUME, TYPE_ADMIN_SET, TYPE_ADMIN_GRANT, TYPE_ADMIN_DEDUCT, TYPE_REDEEM, TYPE_INVITE, TYPE_RECHARGE)

# 收支方向（按 points 正负过滤，与 type 正交）：
# 注意 admin_set 的变动值可正可负，所以「方向」不能由 type 推导，只能看 points 本身。
DIRECTION_INCOME = "income"
DIRECTION_EXPENSE = "expense"
VALID_DIRECTIONS = (DIRECTION_INCOME, DIRECTION_EXPENSE)

_ADJUST_ACTIONS = ("set", "grant", "deduct")

# 积分余额上限（单一事实来源）：管理员调整与兑换码充值共用同一条红线，防止换条入金口绕过
MAX_POINTS_BALANCE = 10_000_000


def record_transaction(
    db: AsyncSession,
    *,
    user_id: int,
    points: int,
    balance_after: int,
    type: str,
    description: str | None = None,
    operator_id: int | None = None,
) -> None:
    """写入一条积分流水（同步 add，随当前事务提交/回滚）"""
    db.add(PointTransaction(
        user_id=user_id,
        points=points,
        balance_after=balance_after,
        type=type,
        description=description,
        operator_id=operator_id,
    ))


def _tx_to_dict(tx: PointTransaction) -> dict:
    return {
        "id": tx.id,
        "user_id": tx.user_id,
        "points": tx.points,
        "balance_after": tx.balance_after,
        "type": tx.type,
        "description": tx.description,
        "operator_id": tx.operator_id,
        "created_at": tx.created_at.isoformat() if tx.created_at else None,
    }


async def list_user_transactions(
    db: AsyncSession, user_id: int, page: int = 1, page_size: int = 10,
) -> dict:
    """查询单用户积分流水（时间倒序）"""
    conditions = [PointTransaction.user_id == user_id]
    total = (
        await db.execute(
            select(func.count(PointTransaction.id)).where(*conditions)
        )
    ).scalar()
    rows = (
        await db.execute(
            select(PointTransaction)
            .where(*conditions)
            .order_by(PointTransaction.created_at.desc(), PointTransaction.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return {"items": [_tx_to_dict(t) for t in rows], "total": total, "page": page, "page_size": page_size}


async def list_all_transactions(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    keyword: str = None,
    type: str = None,
    direction: str = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> dict:
    """全站积分流水（后台审计）

    - `keyword` 按用户账号模糊筛选；`type` 按流水类型
    - `direction` 按收支方向（income=points>0 / expense=points<0；**变动为 0 的记录两侧都不含**）
    - `start_at` / `end_at` 按变动时间，**闭区间且必须是 naive UTC**
      （由路由层用 `utils.time.parse_iso_to_naive_utc` 从 ISO 字符串换算，别在这里猜时区）

    校验一律在触库前完成：非法 type/direction 直接 400，避免拼错参数时静默返回全量。
    """
    conditions = []
    if type:
        if type not in VALID_TYPES:
            raise AppException(code=400, message="流水类型无效")
        conditions.append(PointTransaction.type == type)
    if direction:
        if direction not in VALID_DIRECTIONS:
            raise AppException(code=400, message="收支方向无效")
        conditions.append(
            PointTransaction.points > 0
            if direction == DIRECTION_INCOME
            else PointTransaction.points < 0
        )
    if start_at is not None:
        conditions.append(PointTransaction.created_at >= start_at)
    if end_at is not None:
        conditions.append(PointTransaction.created_at <= end_at)
    if keyword:
        conditions.append(
            User.account.contains(escape_like(keyword), escape="\\")
        )

    # 操作人用**别名 + outer join**：consume / redeem 这类系统写入的 operator_id 为 NULL，
    # 且管理员账号被删后 id 也解析不到 —— 两种情况都不能把整条流水从对账视图里丢掉
    # （所以是 outer 而不是 inner）。count 查询刻意不加这个 join：它不影响行数，加上只是白费。
    operator = aliased(User)
    base = (
        select(PointTransaction, User.account, operator.account)
        .join(User, User.id == PointTransaction.user_id)
        .outerjoin(operator, operator.id == PointTransaction.operator_id)
    )
    count_base = (
        select(func.count(PointTransaction.id))
        .join(User, User.id == PointTransaction.user_id)
    )
    if conditions:
        base = base.where(*conditions)
        count_base = count_base.where(*conditions)

    total = (await db.execute(count_base)).scalar()
    rows = (
        await db.execute(
            base
            .order_by(PointTransaction.created_at.desc(), PointTransaction.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    items = [
        {**_tx_to_dict(tx), "user_account": account, "operator_account": operator_account}
        for tx, account, operator_account in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def adjust_points(
    db: AsyncSession,
    admin: User,
    user: User,
    action: str,
    value: int,
    note: str = None,
) -> int:
    """管理端调整积分（set 设置 / grant 充值 / deduct 扣减），写流水并返回新余额。

    由 admin_user_service.update_user 调用（用户存在性已校验），
    仅 flush 不 commit，与角色/VIP 等其他字段同事务由调用方提交。

    并发契约：本函数为「读内存值 → 绝对值写回」，调用方必须已通过
    SELECT ... FOR UPDATE 持有该用户行锁（当前由 admin_user_service.update_user
    保证，SEC-017），否则与 tool_service 的原子 UPDATE 并发时会丢失更新。
    """
    if action not in _ADJUST_ACTIONS:
        raise AppException(code=400, message="积分调整方式无效，仅支持 set/grant/deduct")
    if value < 0 or value > MAX_POINTS_BALANCE:
        raise AppException(code=400, message=f"积分数量必须在 0 - {MAX_POINTS_BALANCE} 之间")

    current = user.points or 0
    if action == "set":
        new_balance = value
        delta = new_balance - current
        tx_type = TYPE_ADMIN_SET
        default_desc = "管理员设置积分"
    elif action == "grant":
        new_balance = current + value
        if new_balance > MAX_POINTS_BALANCE:
            raise AppException(code=400, message=f"调整后余额不能超过 {MAX_POINTS_BALANCE}")
        delta = value
        tx_type = TYPE_ADMIN_GRANT
        default_desc = "管理员充值积分"
    else:  # deduct
        if value > current:
            raise AppException(code=400, message=f"扣减失败：当前余额仅 {current} 积分")
        new_balance = current - value
        delta = -value
        tx_type = TYPE_ADMIN_DEDUCT
        default_desc = "管理员扣减积分"

    user.points = new_balance
    record_transaction(
        db,
        user_id=user.id,
        points=delta,
        balance_after=new_balance,
        type=tx_type,
        description=(note or None) or default_desc,
        operator_id=admin.id,
    )

    # 审计（台账 P2-9）：积分调整直接影响资金。积分流水虽记了 operator_id，
    # 但只反映「余额变化结果」——审计行补上**这次操作的请求上下文**
    # （调整方式 / 请求值 / 备注），两者互为补充，不可替代。
    # 本函数只 flush，由 update_user 的 commit 一并落库（审计与业务同事务）。
    record_admin_action(
        db,
        admin,
        "user_adjust_points",
        target_type="user",
        target_id=user.id,
        summary=f"调整用户 {user.id} 积分（{action} {value}）：{current} → {new_balance}",
        detail={
            "action": action,
            "value": value,
            "before": current,
            "after": new_balance,
            "delta": delta,
            "note": (note or "")[:200],
        },
    )

    await db.flush()
    return new_balance
