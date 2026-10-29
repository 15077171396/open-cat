"""认证审核服务 - 知识星球认证流程"""

from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from loguru import logger

from app.models.auth_request import (
    AUTH_REQUEST_STATUSES,
    AUTH_REQUEST_STATUS_LABELS,
    PENDING_KEY,
    VIP_DURATION_LABELS,
    AuthRequest,
)
from app.models.user import User
from app.core.exceptions import AppException, NotFoundError
from app.services import moderation_service
from app.services.admin_audit_service import record_admin_action
from app.utils.sanitize import sanitize_plain_text, scrub_log_value


# VIP 时长映射
VIP_DURATIONS = {
    "1month": timedelta(days=30),
    "3month": timedelta(days=90),
    "1year": timedelta(days=365),
}


async def cleanup_auth_data_on_deactivation(db: AsyncSession, user: User) -> None:
    """账号注销/被删除时清理认证相关状态

    - 清除用户待审核星球号标记
    - 删除 pending 状态的认证申请，避免已注销账号的申请残留在管理端审核队列
    - 保留已终态(approved/rejected)的历史记录，供数据看板统计与审计
    """
    user.pending_auth_code = None
    await db.execute(
        delete(AuthRequest).where(
            AuthRequest.user_id == user.id,
            AuthRequest.status == "pending",
        )
    )


async def cleanup_auth_data_on_reactivation(db: AsyncSession, user: User) -> None:
    """账号复活（注销后重新注册）时清理上一账号周期的认证数据

    重新注册视为全新账号：清除待审核标记与 VIP 过期时间，并删除全部
    历史认证申请，避免查询到上一周期的认证状态/星球号/驳回原因，
    也避免残留的 pending 申请阻止用户重新提交认证。
    """
    user.pending_auth_code = None
    user.vip_expire_time = None
    await db.execute(delete(AuthRequest).where(AuthRequest.user_id == user.id))


async def submit_auth_request(db: AsyncSession, user: User, star_code: str) -> dict:
    """提交知识星球认证申请"""
    # 检查是否已有待审核的申请
    result = await db.execute(
        select(AuthRequest).where(
            AuthRequest.user_id == user.id,
            AuthRequest.status == "pending",
        )
    )
    if result.scalar_one_or_none():
        raise AppException(code=400, message="您已有待审核的认证申请，请等待管理员处理")

    # 检查是否已是 VIP
    if user.role == "vip":
        raise AppException(code=400, message="您已是认证用户，无需重复认证")

    # 管理员不参与认证：审批通过会把 users.role 从 admin 覆盖掉（即使审批侧已做兜底，
    # 也不该让这类申请进入待审队列，否则运营会看到一条「批了就锁死后台」的申请）
    if user.role == "admin":
        raise AppException(code=400, message="管理员账号无需提交认证申请")

    # 星球号是纯用户可控自由文本，此前既不过 sanitize 也不过敏感词
    # （台账 P0-5）：先剥标签再筛查，否则它就是一个可嵌违规内容的字段
    star_code = sanitize_plain_text((star_code or "").strip())
    screen = moderation_service.screen_text(star_code)
    if screen.hits:
        await moderation_service.log_hit(
            db, content_type="star_code", user_id=user.id,
            text=star_code, hits=screen.hits, action=screen.action,
            target_id=str(user.id),
        )
        if not screen.passed:
            await db.commit()
            raise AppException(code=400, message="星球号包含不当信息，请检查后重新填写")
        star_code = screen.text

    # 创建申请
    # pending_key 参与 (user_id, pending_key) 唯一约束：先查后插在并发下必然能钻过去
    # （两个请求同时查到「无 pending」），由数据库唯一约束做最终裁判（P1-8）。
    auth_req = AuthRequest(
        user_id=user.id,
        star_code=star_code,
        status="pending",
        pending_key=PENDING_KEY,
    )
    db.add(auth_req)
    # ⚠️ 先把 user_id 取成局部变量：flush 失败后 SQLAlchemy 会把会话内所有 ORM
    # 实例标记为过期，此时再访问 ``user.id`` 会触发惰性加载并抛 PendingRollbackError，
    # 把下面本来要抛的友好 400 错误吞成 500。except 块里只碰局部变量。
    user_id = user.id
    try:
        # SAVEPOINT 包裹：撞唯一约束时仅回滚本条插入，会话不进入失败态
        # （直接 rollback 会回滚整个请求事务并使 ORM 实例过期）
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        # 并发提交：另一请求刚插入了 pending（或存量数据中已有一条）。
        # 降级为友好提示而非 500。user.pending_auth_code 尚未改写，无需回滚补偿。
        logger.info(f"认证申请并发/重复提交被唯一约束拦下: user_id={user_id}")
        raise AppException(code=400, message="您已有待审核的认证申请，请等待管理员处理")

    # 更新用户待审核状态（放在约束校验之后：抢锁失败的请求不该改写用户状态）
    user.pending_auth_code = star_code
    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()

    # star_code 用户可控：不净化则一条含 \n 的输入能伪造任意日志行写进 docker logs
    # （文件 sink 是 JSON、不受影响；console sink 是纯文本）—— P2-50⑤
    logger.info(f"用户 {user.id} 提交知识星球认证: {scrub_log_value(star_code)}")

    return {"status": "pending", "message": "认证申请已提交，请等待管理员审核"}


async def get_auth_status(db: AsyncSession, user: User) -> dict:
    """查询认证状态"""
    if user.role in ("vip", "admin"):
        return {
            "status": "approved",
            "vip_expire_time": user.vip_expire_time.isoformat() if user.vip_expire_time else None,
        }

    # 查询最近的申请
    result = await db.execute(
        select(AuthRequest)
        .where(AuthRequest.user_id == user.id)
        .order_by(AuthRequest.created_at.desc())
        .limit(1)
    )
    auth_req = result.scalar_one_or_none()

    if not auth_req:
        return {"status": "none"}

    return {
        "status": auth_req.status,
        "star_code": auth_req.star_code,
        "created_at": auth_req.created_at.isoformat() if auth_req.created_at else None,
        "reject_reason": auth_req.reject_reason,
    }


async def approve_auth_request(
    db: AsyncSession, admin: User, auth_request_id: int, vip_duration: str
) -> dict:
    """管理员通过认证

    并发安全（第二轮增量审查 P1）：本函数是「先查状态 → 再改状态」的读改写序列，
    此前**无行锁** —— 两个管理员同时点「通过」时都会读到 pending 并各自写入，
    使 VIP 到期时间被后提交者覆盖（用户实际权益取决于提交顺序），
    还会产生两条审计行但只有一条生效。此处加 `FOR UPDATE` 串行化审批。
    """
    result = await db.execute(
        select(AuthRequest)
        .where(AuthRequest.id == auth_request_id)
        .with_for_update()
    )
    auth_req = result.scalar_one_or_none()
    if not auth_req:
        raise NotFoundError("认证申请不存在")

    if auth_req.status != "pending":
        raise AppException(code=400, message="该申请已处理")

    duration = VIP_DURATIONS.get(vip_duration)
    if not duration:
        raise AppException(code=400, message="无效的 VIP 时长")

    # 先取用户：VIP 到期时间要在其**现有剩余时长基础上叠加**，
    # 而不是直接覆盖 —— 否则已持有长期 VIP 的用户再次认证（如换绑星球号）
    # 反而被**缩短**（第二轮增量审查 P2）
    result = await db.execute(
        select(User).where(User.id == auth_req.user_id, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    base = now
    if user and user.vip_expire_time:
        current = user.vip_expire_time
        # MySQL DateTime 列返回 naive，统一视为 UTC（与 check_vip_expiry 同口径）
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if current > now:
            base = current
    vip_expire_time = base + duration

    # 更新申请状态：pending_key 置 NULL 才释放唯一约束，用户此后可重新提交
    auth_req.status = "approved"
    auth_req.pending_key = None
    auth_req.vip_duration = vip_duration
    auth_req.reviewed_by = admin.id
    auth_req.reviewed_at = now

    if user:
        # 管理员账号不参与「认证 → vip」的角色改写：`users.role` 决定能否进后台，
        # 把 admin 覆盖成 vip 会让 require_admin 立即失效（审批一个超管的认证申请 = 锁死后台）。
        # 管理员本来就不该提交认证申请，这里做兜底：只延长 VIP 到期时间，不动角色。
        if user.role != "admin":
            user.role = "vip"
        user.vip_expire_time = vip_expire_time
        user.pending_auth_code = None

    # 审计（台账 P2-9）：认证审批直接决定 VIP 权益，此前只有日志、无持久化记录，
    # 多管理员场景下无法回答「是谁批的」。放在 commit **之前**，与业务同事务 ——
    # 业务回滚则审计行一起消失（「动作没发生」就不该留审计行）。
    record_admin_action(
        db,
        admin,
        "auth_approve",
        target_type="auth_request",
        target_id=auth_req.id,
        summary=f"通过用户 {auth_req.user_id} 的实名认证（VIP {vip_duration}）",
        detail={"target_user_id": auth_req.user_id, "vip_duration": vip_duration},
    )

    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    logger.info(f"管理员 {admin.id} 通过用户 {auth_req.user_id} 的认证申请")

    return {"message": "认证已通过", "vip_expire_time": vip_expire_time.isoformat()}


async def reject_auth_request(
    db: AsyncSession, admin: User, auth_request_id: int, reason: str = None
) -> dict:
    """管理员驳回认证（并发安全：与 approve 同理，加行锁防重复审批）"""
    result = await db.execute(
        select(AuthRequest)
        .where(AuthRequest.id == auth_request_id)
        .with_for_update()
    )
    auth_req = result.scalar_one_or_none()
    if not auth_req:
        raise NotFoundError("认证申请不存在")

    if auth_req.status != "pending":
        raise AppException(code=400, message="该申请已处理")

    # pending_key 置 NULL 才释放唯一约束，否则用户驳回后无法再次提交认证
    auth_req.status = "rejected"
    auth_req.pending_key = None
    auth_req.reviewed_by = admin.id
    auth_req.reviewed_at = datetime.now(timezone.utc)
    auth_req.reject_reason = reason

    # 清除用户待审核状态
    result = await db.execute(
        select(User).where(User.id == auth_req.user_id, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()
    if user:
        user.pending_auth_code = None

    record_admin_action(
        db,
        admin,
        "auth_reject",
        target_type="auth_request",
        target_id=auth_req.id,
        summary=f"驳回用户 {auth_req.user_id} 的实名认证",
        detail={
            "target_user_id": auth_req.user_id,
            "reason": (reason or "")[:200],
        },
    )

    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    logger.info(f"管理员 {admin.id} 驳回用户 {auth_req.user_id} 的认证申请")

    return {"message": "认证已驳回"}


async def list_auth_requests(
    db: AsyncSession, page: int = 1, page_size: int = 20, status: str | None = None
) -> dict:
    """认证申请列表（按提交时间正序 = 先到先审）

    - `status`：pending / approved / rejected；不传或 "all" 表示全部。
      **改版前本函数硬编码 `status == "pending"`**，于是审批过的记录在后台彻底消失 ——
      谁审的、给了多久、驳回原因是什么，全都查不到（这三列一直存在但没出口）。
    - 审核人用**别名 + outer join** 解析账号：待审核时 `reviewed_by` 为 NULL、
      管理员账号被删后也解析不到 —— 两种情况都不能把整条记录丢掉，故 outer 而非 inner。
    - count 查询刻意不加这个 join：它不影响行数，加上只是白费。
    """
    conditions = []
    if status and status != "all":
        if status not in AUTH_REQUEST_STATUSES:
            raise AppException(code=400, message="无效的申请状态")
        conditions.append(AuthRequest.status == status)

    # 直接对主表 count, 避免 subquery 包裹 (PERF)
    total = (
        await db.execute(select(func.count(AuthRequest.id)).where(*conditions))
    ).scalar()

    reviewer = aliased(User)
    query = (
        select(AuthRequest, User.account, reviewer.account)
        .outerjoin(User, AuthRequest.user_id == User.id)
        .outerjoin(reviewer, reviewer.id == AuthRequest.reviewed_by)
        .where(*conditions)
        .order_by(AuthRequest.created_at.asc(), AuthRequest.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(query)).all()

    items = [
        {
            "id": req.id,
            "user_id": req.user_id,
            "account": account,
            "star_code": req.star_code,
            "status": req.status,
            "status_label": AUTH_REQUEST_STATUS_LABELS.get(req.status, req.status),
            "vip_duration": req.vip_duration,
            "vip_duration_label": VIP_DURATION_LABELS.get(req.vip_duration, req.vip_duration),
            "reviewed_by": req.reviewed_by,
            "reviewer_account": reviewer_account,
            "reviewed_at": req.reviewed_at.isoformat() if req.reviewed_at else None,
            "reject_reason": req.reject_reason,
            "created_at": req.created_at.isoformat() if req.created_at else None,
        }
        for req, account, reviewer_account in rows
    ]

    return {"items": items, "total": total, "page": page, "page_size": page_size}
