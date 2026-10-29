"""积分兑换码服务：用户兑换 / 管理端创建·查询·作废。

每个写函数返回前显式 commit，「兑换记录 + 名额核减 + 余额变动 + 积分流水」单事务原子提交，
失败整体回滚。并发安全：名额核销用原子 UPDATE（行锁先到先得，绝不超发）；
同用户重复兑换由 (code_id, user_id) 唯一约束 + savepoint 兜底。
"""

import secrets
from datetime import datetime, timezone

from sqlalchemy import select, func, update, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.redemption_code import RedemptionCode, RedemptionRecord
from app.core.exceptions import AppException, NotFoundError
from app.services.point_service import record_transaction, TYPE_REDEEM, MAX_POINTS_BALANCE
from app.services.admin_audit_service import record_admin_action, summarize_ids
from app.utils.sanitize import escape_like

# 兑换码字符集：去除易混淆字符 I O 0 1，格式 XXXX-XXXX-XXXX-XXXX
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_GROUP_LEN = 4
_CODE_GROUPS = 4

MAX_POINTS_PER_CODE = 1_000_000
MAX_USES_PER_CODE = 100_000
MAX_BATCH_COUNT = 100


def _generate_code() -> str:
    """生成随机兑换码（secrets 密码学安全随机）"""
    groups = (
        "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_GROUP_LEN))
        for _ in range(_CODE_GROUPS)
    )
    return "-".join(groups)


def _utcnow_naive() -> datetime:
    """当前 naive UTC 时间。

    ⚠️ 为什么不用 ``app.utils.time.utcnow()``：那个返回 **aware** 时间，而本模块的
    ``expires_at`` 来自 MySQL ``DATETIME`` 列（naive）—— aware 与 naive 直接比较会抛
    ``TypeError``。故此处统一用 **naive UTC**，与 DB 列口径一致。

    为什么必须改成 UTC（第二轮增量审查 2026-10-10 P1）：
    此前本模块的 ``_parse_expires_at``（api/admin.py）与这里的比较**都用本地时间**，
    内部自洽但写入 DB 的 ``expires_at`` 是**本地时间**，而其它时间列
    （``func.now()`` / ``datetime.now(timezone.utc)``）是 **UTC** —— 跨模块不一致。
    生产 compose 设了 ``TZ=UTC`` 所以恰好无感，**本地开发（东八区）则出现 8 小时偏差**，
    属「开发环境与生产行为不一致」的隐患。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _eff_status(code: RedemptionCode) -> str:
    """计算展示状态：disabled > expired > exhausted > active"""
    if code.status == "disabled":
        return "disabled"
    if code.expires_at and code.expires_at < _utcnow_naive():
        return "expired"
    if (code.used_count or 0) >= code.max_uses:
        return "exhausted"
    return "active"


def mask_code(code: str | None) -> str:
    """兑换码掩码：保留末组，够运营区分是哪一张码，不足以被冒用。

    为什么列表要掩码（第四轮全量审查 P1-54）：`GET /admin/redemption-codes` 只由
    `redeem_code:view` 把关，而内置「只读管理员 viewer」被授予**除 audit:view 外的
    全部** `*:view` 权限。明文返回时，一个只读账号（或泄露的 viewer 凭据）可以
    列出全部未兑换码，再走普通用户端点 `POST /users/points/redeem` 把整批活动码
    兑到自己账上（单码上限 100 万积分）—— 全程不持任何写权限、不产生审计行，
    与该角色「不可做任何写操作」的契约相违。能创建码的人本来就持有
    `redeem_code:create`，仍可看明文，运营分发路径不受影响。
    """
    if not code:
        return ""
    groups = code.split("-")
    if len(groups) > 1:
        return "-".join(["****"] * (len(groups) - 1) + [groups[-1]])
    if len(code) <= 4:
        return "****"
    return f"****{code[-4:]}"


def _code_to_dict(code: RedemptionCode, *, reveal: bool = True) -> dict:
    return {
        "id": code.id,
        "code": code.code if reveal else mask_code(code.code),
        "points": code.points,
        "max_uses": code.max_uses,
        "used_count": code.used_count or 0,
        "status": code.status,
        # 服务端时间计算的展示状态（进行中/已兑完/已过期/已作废）
        "eff_status": _eff_status(code),
        "is_single": code.max_uses == 1,
        "expires_at": code.expires_at.isoformat() if code.expires_at else None,
        "note": code.note,
        "created_at": code.created_at.isoformat() if code.created_at else None,
    }


def _record_to_dict(
    record: RedemptionRecord, user_account: str | None = None, *, reveal: bool = True
) -> dict:
    data = {
        "id": record.id,
        "code_id": record.code_id,
        "code": record.code if reveal else mask_code(record.code),
        "user_id": record.user_id,
        "points": record.points,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
    if user_account is not None:
        data["user_account"] = user_account
    return data


# 用户端：兑换

async def redeem_code(db: AsyncSession, user: User, code_str: str) -> dict:
    """用户兑换积分码（并发安全，见模块头注释）"""
    code_str = (code_str or "").strip().upper()
    if not code_str:
        raise AppException(code=400, message="请输入兑换码")
    if len(code_str) > 32:
        raise AppException(code=400, message="兑换码格式不正确")

    code = (
        await db.execute(select(RedemptionCode).where(RedemptionCode.code == code_str))
    ).scalar_one_or_none()
    if not code:
        raise AppException(code=400, message="兑换码不存在，请检查后重新输入")
    if code.status != "active":
        raise AppException(code=400, message="该兑换码已被作废，无法使用")
    if code.expires_at and code.expires_at < _utcnow_naive():
        raise AppException(code=400, message="该兑换码已过期，无法使用")

    # 幂等预检查：同一用户已兑换过直接友好提示（并发请求由唯一约束兜底）。
    # 置于名额检查之前——已兑换用户重复提交时提示"已兑换过"比"名额已满"更准确
    existed = (
        await db.execute(
            select(RedemptionRecord.id).where(
                RedemptionRecord.code_id == code.id,
                RedemptionRecord.user_id == user.id,
            )
        )
    ).first()
    if existed:
        raise AppException(code=400, message="您已兑换过该兑换码，请勿重复兑换")

    if (code.used_count or 0) >= code.max_uses:
        # 快速失败路径；并发下的最终裁决由下方原子 UPDATE 兜底
        raise AppException(code=400, message="该兑换码兑换名额已满")

    # savepoint 包裹兑换记录插入：并发双击/多端同时提交时后到请求撞唯一约束，
    # 仅回滚本段插入，会话不进入失败态
    try:
        async with db.begin_nested():
            db.add(
                RedemptionRecord(
                    code_id=code.id, code=code.code, user_id=user.id, points=code.points
                )
            )
            await db.flush()
    except IntegrityError:
        raise AppException(code=400, message="您已兑换过该兑换码，请勿重复兑换")

    # 原子核销名额（WHERE used_count < max_uses AND status='active'）：
    # 多人抢兑最后几个名额时数据库行锁先到先得，rowcount=0 说明名额已被抢完
    result = await db.execute(
        update(RedemptionCode)
        .where(
            RedemptionCode.id == code.id,
            RedemptionCode.used_count < RedemptionCode.max_uses,
            RedemptionCode.status == "active",
        )
        .values(used_count=RedemptionCode.used_count + 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        raise AppException(code=400, message="该兑换码兑换名额已满，手慢了~")

    # 原子加积分（相对值自增，并发兑换/充值下不丢失更新）
    # 加**余额上限条件**：与管理员调整积分共用 point_service 的同一红线，
    # 否则单张高面额码可把余额顶到远超管理员被允许的上限（第二轮增量审查 P1）。
    # 用条件 UPDATE 而非「先读后判」：并发兑换下也不会越界，rowcount=0 即越界，
    # 整笔兑换随事务回滚（兑换码名额核销也一并回滚）。
    points_result = await db.execute(
        update(User)
        .where(
            User.id == user.id,
            func.coalesce(User.points, 0) + code.points <= MAX_POINTS_BALANCE,
        )
        .values(points=func.coalesce(User.points, 0) + code.points)
        .execution_options(synchronize_session=False)
    )
    if points_result.rowcount == 0:
        raise AppException(code=400, message="兑换后积分将超出上限，请联系客服处理")

    # 回读真实余额再写流水（并发场景下内存快照可能陈旧）
    await db.refresh(user)
    record_transaction(
        db,
        user_id=user.id,
        points=code.points,
        balance_after=user.points or 0,
        type=TYPE_REDEEM,
        description=f"兑换码充值：{code.code}",
    )
    # 显式提交：确保「兑换成功」响应返回前积分已落库（get_db teardown 的
    # commit 晚于响应发送；资金类操作不允许存在成功响应但写入丢失的窗口）
    await db.commit()
    return {
        "points": code.points,
        "balance": user.points or 0,
        "code": code.code,
    }


# 管理端：创建 / 查询 / 作废

async def _generate_unique_codes(db: AsyncSession, count: int) -> list[str]:
    """批量生成不重复的兑换码（单条 IN 查询批量查重，替代逐候选 SELECT 往返；
    撞库重试，防御性上限防死循环）"""
    codes: list[str] = []
    attempts = 0
    while len(codes) < count:
        attempts += 1
        if attempts > count * 50:
            raise AppException(code=500, message="兑换码生成失败，请重试")
        # 一次生成剩余数量的候选（含本地去重），批量排除库中已存在的
        seen = set(codes)
        batch: list[str] = []
        while len(batch) < count - len(codes):
            candidate = _generate_code()
            if candidate not in seen:
                seen.add(candidate)
                batch.append(candidate)
        existed = set(
            (
                await db.execute(
                    select(RedemptionCode.code).where(RedemptionCode.code.in_(batch))
                )
            ).scalars().all()
        )
        codes.extend(c for c in batch if c not in existed)
    return codes


async def create_codes(
    db: AsyncSession,
    admin: User,
    *,
    points: int,
    max_uses: int,
    count: int,
    expires_at: datetime | None = None,
    note: str | None = None,
) -> list[dict]:
    """管理员批量创建兑换码"""
    if points < 1 or points > MAX_POINTS_PER_CODE:
        raise AppException(code=400, message=f"兑换积分必须在 1 - {MAX_POINTS_PER_CODE} 之间")
    if max_uses < 1 or max_uses > MAX_USES_PER_CODE:
        raise AppException(code=400, message=f"兑换人数上限必须在 1 - {MAX_USES_PER_CODE} 之间")
    if count < 1 or count > MAX_BATCH_COUNT:
        raise AppException(code=400, message=f"单次生成数量必须在 1 - {MAX_BATCH_COUNT} 之间")
    if expires_at and expires_at <= _utcnow_naive():
        raise AppException(code=400, message="过期时间必须晚于当前时间")

    codes = await _generate_unique_codes(db, count)
    rows = [
        RedemptionCode(
            code=c,
            points=points,
            max_uses=max_uses,
            expires_at=expires_at,
            note=(note or None) or None,
            created_by=admin.id,
        )
        for c in codes
    ]
    db.add_all(rows)
    await db.flush()
    # 刷新服务端默认值列（used_count/status/created_at 等）：
    # flush 后直接属性访问会触发同步懒加载 IO，在异步上下文抛 MissingGreenlet
    for r in rows:
        await db.refresh(r)
    # 审计（台账 P2-9）：兑换码是积分的**入金口**，生成即等于凭空造出
    # `points * max_uses * count` 的潜在积分债务，必须可追溯。
    # 单行记录整批（明细在 detail），逐条写会把审计表撑起来。
    record_admin_action(
        db,
        admin,
        "redeem_code_create",
        target_type="redeem_code",
        target_id=None,
        summary=f"生成 {len(rows)} 个兑换码（{points} 积分 / 每人上限 {max_uses} 次）",
        detail={
            "count": len(rows),
            "points": points,
            "max_uses": max_uses,
            # 潜在积分债务 = 单码积分 × 单码可兑人次 × 码数量，审计时一眼能看出量级
            "potential_points": points * max_uses * len(rows),
            "note": (note or "")[:200],
        },
    )
    # 显式提交：确保「生成成功」响应返回前兑换码已落库（与 disable_code 对齐，
    # get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    return [_code_to_dict(r) for r in rows]


async def list_codes(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    status: str | None = None,
    reveal: bool = False,
) -> dict:
    """管理端兑换码列表（创建时间倒序；keyword 按兑换码/备注模糊，status 按 eff_status 筛选）

    `reveal` 默认 **False**（安全默认）：明文兑换码只给持有 `redeem_code:create`
    的调用方，详见 `mask_code`（第四轮审查 P1-54）。
    """
    conditions = []
    if keyword:
        kw = escape_like(keyword)
        conditions.append(
            RedemptionCode.code.contains(kw, escape="\\")
            | RedemptionCode.note.contains(kw, escape="\\")
        )
    if status:
        if status not in ("active", "expired", "exhausted", "disabled"):
            raise AppException(code=400, message="状态筛选值无效")
        now = _utcnow_naive()
        if status == "active":
            # 进行中 = 未作废 + 未过期 + 未兑完（均可下推到 SQL）
            conditions.append(
                and_(
                    RedemptionCode.status == "active",
                    RedemptionCode.used_count < RedemptionCode.max_uses,
                    RedemptionCode.expires_at.is_(None) | (RedemptionCode.expires_at > now),
                )
            )
        elif status == "disabled":
            conditions.append(RedemptionCode.status == "disabled")
        elif status == "expired":
            # 已过期：与 _eff_status 优先级一致（过期优先于兑完），排除已兑满的
            conditions.append(
                and_(
                    RedemptionCode.status == "active",
                    RedemptionCode.expires_at.is_not(None),
                    RedemptionCode.expires_at <= now,
                    RedemptionCode.used_count < RedemptionCode.max_uses,
                )
            )
        else:  # exhausted
            # 已兑完：未作废且未过期（已过期的按"已过期"归类）
            conditions.append(
                and_(
                    RedemptionCode.status == "active",
                    RedemptionCode.used_count >= RedemptionCode.max_uses,
                    RedemptionCode.expires_at.is_(None) | (RedemptionCode.expires_at > now),
                )
            )

    total = (
        await db.execute(
            select(func.count(RedemptionCode.id)).where(*conditions)
        )
    ).scalar()
    rows = (
        await db.execute(
            select(RedemptionCode)
            .where(*conditions)
            .order_by(RedemptionCode.created_at.desc(), RedemptionCode.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return {
        "items": [_code_to_dict(r, reveal=reveal) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_code(db: AsyncSession, code_id: int) -> RedemptionCode:
    code = (
        await db.execute(select(RedemptionCode).where(RedemptionCode.id == code_id))
    ).scalar_one_or_none()
    if not code:
        raise NotFoundError("兑换码不存在")
    return code


async def disable_code(db: AsyncSession, code_id: int, admin: User | None = None) -> dict:
    """作废兑换码（已兑换的用户不受影响，仅阻止后续兑换）

    :param admin: 操作者（审计用）。原实现不需要它是因为本函数只读改状态；
        审计必须知道「谁作废的」，故显式传入。
    """
    code = await get_code(db, code_id)
    if code.status == "disabled":
        raise AppException(code=400, message="该兑换码已是作废状态")
    code.status = "disabled"
    record_admin_action(
        db,
        admin,
        "redeem_code_disable",
        target_type="redeem_code",
        target_id=code.id,
        summary=f"作废兑换码 {code.code}",
        detail={"code": code.code, "points": code.points},
    )
    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    return _code_to_dict(code)


async def disable_codes_batch(db: AsyncSession, code_ids: list[int], admin: User) -> dict:
    """批量作废兑换码（已作废的自动跳过，不计入 disabled）。

    与单条 `disable_code` 的差别只有「一次处理一批」：**不做**「已作废就报错」——
    批量场景里混着几条已作废的是常态（重试、重复勾选），为此整批失败会很烦人。
    返回值区分 `disabled` 与 `skipped`，让调用方能如实告诉运营「有几个其实不用作废」。
    """
    rows = (
        await db.execute(select(RedemptionCode).where(RedemptionCode.id.in_(code_ids)))
    ).scalars().all()
    if not rows:
        raise NotFoundError("兑换码不存在")

    disabled = 0
    for code in rows:
        if code.status != "disabled":
            code.status = "disabled"
            disabled += 1
    skipped = len(rows) - disabled
    record_admin_action(
        db,
        admin,
        "redeem_code_disable",
        target_type="redeem_code",
        target_id=None,
        summary=f"批量作废 {disabled} 个兑换码",
        detail={
            "requested": len(code_ids),
            "disabled": disabled,
            "skipped": skipped,
            "ids": summarize_ids(code_ids),
        },
    )
    # 显式提交：确保响应返回前事务已落库（与单条 disable_code 对齐）
    await db.commit()
    return {"disabled": disabled, "skipped": skipped}


async def list_code_records(
    db: AsyncSession, code_id: int, page: int = 1, page_size: int = 20,
    reveal: bool = False,
) -> dict:
    """单个兑换码的兑换记录（含兑换用户账号，时间倒序）

    兑换记录里带着码原文，多人限量码在还有余量时同样可被再次兑换 —— 故与列表
    同口径掩码（第四轮审查 P1-54）。
    """
    code = await get_code(db, code_id)
    base = (
        select(RedemptionRecord, User.account)
        .join(User, User.id == RedemptionRecord.user_id)
        .where(RedemptionRecord.code_id == code.id)
    )
    total = (
        await db.execute(
            select(func.count(RedemptionRecord.id)).where(RedemptionRecord.code_id == code.id)
        )
    ).scalar()
    rows = (
        await db.execute(
            base
            .order_by(RedemptionRecord.created_at.desc(), RedemptionRecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [_record_to_dict(r, account, reveal=reveal) for r, account in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
