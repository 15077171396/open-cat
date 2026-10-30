"""管理后台 - 用户管理服务（从 admin.py 抽取的 ORM 操作，响应格式与原路由一致；
list/detail 均返回 has_pending_auth_code 布尔而非裸字段；删除为软删除；时间统一 UTC）"""

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import delete, select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.data_scope import DataScope, apply_scope
from app.core.security import bump_token_version
from app.models.user import User
from app.models.auth_request import AuthRequest
from app.models.rbac import Department, Role, UserRole
from app.core.exceptions import AppException
from app.services.timer_service import get_free_time_status
from app.services.point_service import adjust_points
from app.services import setting_service
from app.services.admin_audit_service import record_admin_action, summarize_ids
from app.services.rbac_service import invalidate_user_rbac_cache
from app.utils.sanitize import escape_like


async def _batch_rbac_info(db: AsyncSession, user_ids: list[int]) -> tuple[dict, dict]:
    """批量加载用户 RBAC 角色与部门（避免列表页 N+1）。

    返回 (roles_map: uid -> [role dicts], dept_map: dept_id -> name)
    """
    roles_map: dict[int, list[dict]] = {uid: [] for uid in user_ids}
    dept_map: dict[int, str] = {}
    if not user_ids:
        return roles_map, dept_map
    role_rows = (
        await db.execute(
            select(Role, UserRole.user_id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id.in_(user_ids))
            .order_by(Role.id.asc())
        )
    ).all()
    for role, uid in role_rows:
        roles_map.setdefault(uid, []).append(
            {"id": role.id, "code": role.code, "name": role.name}
        )
    dept_rows = (
        await db.execute(select(Department.id, Department.name))
    ).all()
    dept_map = {d.id: d.name for d in dept_rows}
    return roles_map, dept_map


def _free_time_fields(user: User) -> dict:
    """管理后台展示口径的免费时长字段。

    「已用时长」按自然时间倒计时消耗（now - free_started_at，封顶 3 小时），
    与前台剩余时长口径一致（timer_service 统一实现）；
    VIP/admin 不限时：free_remaining_seconds 返回 None，已用仅展示实际学习统计。
    """
    if user.role in ("vip", "admin"):
        return {
            "free_used_seconds": user.free_used_seconds,
            "free_remaining_seconds": None,
        }
    status = get_free_time_status(user)
    return {
        "free_used_seconds": settings.FREE_TIME_LIMIT - status["free_remaining_seconds"],
        "free_remaining_seconds": status["free_remaining_seconds"],
    }


async def list_users(
    db: AsyncSession,
    page: int,
    page_size: int,
    role: str = None,
    keyword: str = None,
    account_status: str = "active",
    scope: DataScope | None = None,
) -> dict:
    """用户列表（含星球认证状态，批量查 AuthRequest 避免 N+1）

    ARCH-012: 默认过滤已软删除用户（deleted_at IS NULL）

    :param account_status: 账号状态筛选
        - ``active``（默认）：只看正常账号，与历史行为一致
        - ``deleted``：只看已注销/被删除的账号（列表项带 ``deleted_at``，可在此恢复）
        - ``all``：全部，已注销的行同样带 ``deleted_at`` 供前端打标记
        非法取值由接口层 ``Literal`` 拦成 422，不在这里兜底 —— 静默降级会让
        「筛选已注销却看到正常账号」变成没人能发现的错。
    """
    conditions = []
    if account_status == "deleted":
        conditions.append(User.deleted_at.is_not(None))
    elif account_status == "all":
        pass
    else:
        conditions.append(User.deleted_at.is_(None))
    if role:
        conditions.append(User.role == role)
    if keyword:
        conditions.append(
            User.account.contains(escape_like(keyword), escape="\\")
        )
    # 数据范围：运营只看本部门、客服只看自己（super_admin 与 all 范围不加条件）
    apply_scope(conditions, scope, owner_col=User.id, dept_col=User.department_id)

    # PERF-012: 直接 count，无 subquery
    total = (
        await db.execute(select(func.count(User.id)).where(*conditions))
    ).scalar()
    # 已注销视图按注销时间倒序（「最近被删的」最需要被看见）；其余按注册时间倒序
    order_by = (
        (User.deleted_at.desc(), User.id.desc())
        if account_status == "deleted"
        else (User.created_at.desc(), User.id.desc())
    )
    query = (
        select(User)
        .where(*conditions)
        .order_by(*order_by)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    users = (await db.execute(query)).scalars().all()

    # 批量查询认证记录，避免 N+1
    user_ids = [u.id for u in users]
    auth_map: dict = {}
    if user_ids:
        auth_rows = (
            await db.execute(
                select(AuthRequest)
                .where(AuthRequest.user_id.in_(user_ids))
                .order_by(AuthRequest.created_at.desc())
            )
        ).scalars().all()
        for ar in auth_rows:
            uid = ar.user_id
            existing = auth_map.get(uid)
            # 优先 approved；同状态下保留最新（已按时间倒序，先到的是最新）
            if existing is None:
                auth_map[uid] = ar
            elif ar.status == "approved" and existing.status != "approved":
                auth_map[uid] = ar

    def _user_base(u: User) -> dict:
        return {
            "id": u.id,
            "account": u.account,
            "role": u.role,
            "points": u.points or 0,
            "vip_expire_time": u.vip_expire_time.isoformat() if u.vip_expire_time else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "avatar_url": u.avatar_url,
            # 非空 = 已注销/被删除（account_status=deleted|all 时才有值）
            "deleted_at": u.deleted_at.isoformat() if u.deleted_at else None,
        }

    # 批量加载 RBAC 角色与部门（PERF：避免列表页逐行查询）
    roles_map, dept_map = await _batch_rbac_info(db, user_ids)

    items = [
        {
            **_user_base(u),
            **_free_time_fields(u),
            # SEC-014: 不返回裸 pending_auth_code
            "has_pending_auth_code": bool(u.pending_auth_code),
            "auth_status": auth_map[u.id].status if u.id in auth_map else None,
            "star_code": (
                auth_map[u.id].star_code
                if u.id in auth_map and auth_map[u.id].status == "approved"
                else None
            ),
            # RBAC：管理后台角色与部门归属
            "rbac_roles": roles_map.get(u.id, []),
            "department_id": u.department_id,
            "department_name": dept_map.get(u.department_id) if u.department_id else None,
        }
        for u in users
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_user(db: AsyncSession, user_id: int, scope: DataScope | None = None) -> dict:
    """用户详情（含认证历史）

    ARCH-012: 排除已软删除用户

    `scope` 非全量时，范围外的用户一律按「不存在」返回 —— 列表已过滤、详情不拦，
    等于给了个可以逐个猜 id 的后门。
    """
    conditions = [User.id == user_id, User.deleted_at.is_(None)]
    apply_scope(conditions, scope, owner_col=User.id, dept_col=User.department_id)
    user = (
        await db.execute(select(User).where(*conditions))
    ).scalar_one_or_none()
    if not user:
        raise AppException(code=404, message="用户不存在")

    auth_history = (
        await db.execute(
            select(AuthRequest)
            .where(AuthRequest.user_id == user_id)
            .order_by(AuthRequest.created_at.desc())
            .limit(20)
        )
    ).scalars().all()

    # RBAC：管理后台角色与部门归属（编辑弹窗回显用）
    roles_map, dept_map = await _batch_rbac_info(db, [user.id])
    department_name = dept_map.get(user.department_id) if user.department_id else None

    return {
        "id": user.id,
        "account": user.account,
        "role": user.role,
        "points": user.points or 0,
        **_free_time_fields(user),
        "vip_expire_time": user.vip_expire_time.isoformat() if user.vip_expire_time else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "avatar_url": user.avatar_url,
        # SEC-014: 不返回裸 pending_auth_code
        "has_pending_auth_code": bool(user.pending_auth_code),
        "rbac_roles": roles_map.get(user.id, []),
        "department_id": user.department_id,
        "department_name": department_name,
        "auth_history": [
            {
                "id": ar.id,
                "star_code": ar.star_code,
                "status": ar.status,
                "vip_duration": ar.vip_duration,
                "reject_reason": ar.reject_reason,
                "created_at": ar.created_at.isoformat() if ar.created_at else None,
                "reviewed_at": ar.reviewed_at.isoformat() if ar.reviewed_at else None,
            }
            for ar in auth_history
        ],
    }


async def update_user(db: AsyncSession, admin: User, user_id: int, updates: dict) -> None:
    """更新用户信息（角色 / VIP 到期 / 头像 / 重置免费时长 / 积分余额）

    updates 字段全部可选：role/vip_expire_time/avatar_url/reset_free_seconds/points
    """
    if user_id == admin.id and updates.get("role") is not None and updates["role"] != "admin":
        raise AppException(code=400, message="不能取消自己的管理员角色")

    # 与 get_user 口径一致：过滤已软删除用户，防止对注销账号改角色/VIP 留下脏状态
    # SELECT ... FOR UPDATE 行锁：本函数是「读内存值 → 绝对值写回」，与 tool_service 的原子
    # UPDATE 并发时会丢失更新；行锁与原子 UPDATE 互斥，保证积分/角色/VIP 调整账实一致
    user = (
        await db.execute(
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not user:
        raise AppException(code=404, message="用户不存在")

    role = updates.get("role")
    role_changed = False
    if role is not None:
        if role not in ("user", "vip", "admin"):
            raise AppException(code=400, message="角色无效")
        if role != user.role:
            if (
                role == "admin"
                and not user.totp_enabled
                and await setting_service.get_bool_setting(db, "admin_require_2fa")
            ):
                raise AppException(
                    code=400,
                    message="管理员强制二次验证已开启：请先让该用户绑定 TOTP，再提升为管理员",
                )
            # 敏感操作审计：角色提权/降级必须留痕（日志易失且不可检索，admin_audit_logs 兜底）
            logger.bind(
                event="admin_role_change", admin_id=admin.id,
                target_user_id=user.id, old_role=user.role, new_role=role,
            ).info(f"管理员变更用户角色: {user.role} -> {role}")
            record_admin_action(
                db,
                admin,
                "user_update_role",
                target_type="user",
                target_id=user.id,
                summary=f"变更用户 {user.id} 角色: {user.role} -> {role}",
                detail={"changes": {"role": [user.role, role]}},
            )
            role_changed = True
            # 降级必须连带收回 RBAC 角色绑定：users.role 与 user_roles 是两套并存的授权来源，
            # 只降前者会留下「业务上已不是管理员、仍持细粒度权限码」的账号；
            # 权限缓存 TTL 300s，须主动失效。此处 user.role 仍是旧值，判断安全。
            if user.role == "admin" and role != "admin":
                await db.execute(delete(UserRole).where(UserRole.user_id == user.id))
                await invalidate_user_rbac_cache(user.id)
        user.role = role

    vip_expire_time = updates.get("vip_expire_time")
    if vip_expire_time is not None:
        _old_vip = user.vip_expire_time
        # 空字符串表示清除 VIP
        if vip_expire_time == "":
            user.vip_expire_time = None
        else:
            try:
                # 兼容带/不带时区的 ISO 字符串
                dt = datetime.fromisoformat(vip_expire_time.replace("Z", "+00:00"))
            except ValueError:
                raise AppException(code=400, message="VIP 到期时间格式无效")
            # 带 +08:00 等非 UTC 偏移的输入须归一化为 UTC 再以 naive 入库，
            # 否则被「naive=UTC」读取端解释后 VIP 实际到期时间偏差 8 小时
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc)
            user.vip_expire_time = dt.replace(tzinfo=None)
        # 审计：VIP 到期时间直接决定付费权益，延长/清除都需可追溯
        record_admin_action(
            db,
            admin,
            "user_update_vip",
            target_type="user",
            target_id=user.id,
            summary=(
                f"清除用户 {user.id} 的会员到期时间"
                if user.vip_expire_time is None
                else f"设置用户 {user.id} 会员到期时间: {user.vip_expire_time.isoformat()}"
            ),
            detail={
                "changes": {
                    "vip_expire_time": [
                        _old_vip.isoformat() if _old_vip else None,
                        user.vip_expire_time.isoformat() if user.vip_expire_time else None,
                    ]
                }
            },
        )

    avatar_url = updates.get("avatar_url")
    if avatar_url is not None:
        user.avatar_url = avatar_url if avatar_url != "" else None

    if updates.get("reset_free_seconds"):
        # 重置 = 重新赠送 3 小时：重设自然时间起点，并清零实际学习统计
        user.free_used_seconds = 0
        user.free_started_at = datetime.now(timezone.utc)

    # 积分调整：新参数 points_action(set/grant/deduct)+points_value+points_note 优先；
    # 兼容旧参数 points（直接设置为该值），两者均写流水
    points_action = updates.get("points_action")
    if points_action is not None:
        points_value = updates.get("points_value")
        if points_value is None:
            raise AppException(code=400, message="积分调整方式存在时必须提供积分数量")
        await adjust_points(db, admin, user, points_action, int(points_value), updates.get("points_note"))
    else:
        points = updates.get("points")
        if points is not None:
            await adjust_points(db, admin, user, "set", int(points), updates.get("points_note"))

    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    # 角色发生变更时吊销该用户全部存量 access_token：
    # 权限判定虽按库中实时角色进行，但吊销可即时终结被降级账号的已有会话
    if role_changed:
        await bump_token_version(user_id)


async def delete_user(db: AsyncSession, admin: User, user_id: int) -> None:
    """删除用户（软删除）：仅标记 deleted_at 并清理认证相关状态，与用户注销逻辑一致"""
    from app.services.audit_service import cleanup_auth_data_on_deactivation

    if user_id == admin.id:
        raise AppException(code=400, message="不能删除自己")
    # 仅允许删除正常状态用户（重复软删除无意义，与 get_user/update_user 口径一致）
    user = (
        await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if not user:
        raise AppException(code=404, message="用户不存在")
    user.deleted_at = datetime.now(timezone.utc)
    await cleanup_auth_data_on_deactivation(db, user)
    # 敏感操作审计：删号必须可追溯（账号快照进审计行，事后才能回答「删的是谁」）
    logger.bind(
        event="admin_delete_user", admin_id=admin.id,
        target_user_id=user.id, account=user.account,
    ).info("管理员软删除用户")
    record_admin_action(
        db,
        admin,
        "user_delete",
        target_type="user",
        target_id=user.id,
        summary=f"删除用户 {user.id}（{user.account}）",
        detail={"target_account": user.account},
    )
    await db.commit()
    # 删号即吊销其全部存量 access_token（deleted_at 过滤已挡后续请求，纵深防御）
    await bump_token_version(user.id)


async def restore_user(db: AsyncSession, admin: User, user_id: int) -> dict:
    """恢复已注销/被删除的用户（清 deleted_at，数据原样保留）

    「原样恢复」的边界（**别指望它把注销流程清掉的东西变回来**）：
    - 保留：积分余额、VIP 到期时间与 role、收藏/学习/下载/消息/工具日志等全部关联数据；
    - 不恢复：注销时已清理的 `pending_auth_code` 与 pending 认证申请、
      已吊销的 token（用户必须重新登录）。
    与「用户自助注册复活」的区别：复活会清空个人数据并重置 VIP/免费时长，
    这里全部保留 —— 恢复的语义是「撤销删除」，不是「重开一个新账号」。

    ⚠️ 不 bump_token_version：注销/删除时已经 bump 过一次，用户手上不可能持有
    当前版本的 access_token，再 bump 一次是空操作。
    """
    user = (
        await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_not(None)))
    ).scalar_one_or_none()
    if not user:
        # 正常账号不需要恢复；已物理不存在的账号也走这里（都返回同一句话，
        # 不给「这个 id 是否存在」的探测口子）
        raise AppException(code=404, message="该用户未被注销，无需恢复")
    user.deleted_at = None
    # 审计：恢复账号等于把访问权还给一个已被处置的对象，与删除同级敏感
    logger.bind(
        event="admin_restore_user", admin_id=admin.id,
        target_user_id=user.id, account=user.account,
    ).info("管理员恢复已注销用户")
    record_admin_action(
        db,
        admin,
        "user_restore",
        target_type="user",
        target_id=user.id,
        summary=f"恢复用户 {user.id}（{user.account}）",
        detail={"target_account": user.account},
    )
    await db.commit()
    return {"id": user.id, "account": user.account}


async def batch_delete_users(db: AsyncSession, admin: User, user_ids: list[int]) -> dict:
    """批量软删除用户（口径与单条 delete_user 一致）

    部分 id 已不存在/已注销时不报错，按实际存在的删除并返回数量。
    """
    from app.services.audit_service import cleanup_auth_data_on_deactivation

    if admin.id in user_ids:
        raise AppException(code=400, message="不能删除自己")
    users = (
        await db.execute(
            select(User).where(User.id.in_(user_ids), User.deleted_at.is_(None))
        )
    ).scalars().all()
    if not users:
        raise AppException(code=404, message="用户不存在")
    now = datetime.now(timezone.utc)
    for user in users:
        user.deleted_at = now
        await cleanup_auth_data_on_deactivation(db, user)
        logger.bind(
            event="admin_delete_user", admin_id=admin.id,
            target_user_id=user.id, account=user.account,
        ).info("管理员软删除用户（批量）")
    # 审计（台账 P2-9）：批量删除只写**一条**审计行（明细在 detail 里存截断后的 id 列表）——
    # 一次勾选几百个账号时逐条写审计会把表撑起来，且「批量」这个动作本身才是要追溯的事实
    record_admin_action(
        db,
        admin,
        "user_batch_delete",
        target_type="user",
        target_id=None,
        summary=f"批量删除 {len(users)} 个用户",
        detail={
            "count": len(users),
            "ids": summarize_ids([u.id for u in users]),
        },
    )
    await db.commit()
    # 批量删号同样吊销全部存量 access_token
    for user in users:
        await bump_token_version(user.id)
    return {"deleted": len(users)}


async def create_user(
    db: AsyncSession, admin: User, data: dict, *, commit: bool = True
) -> dict:
    """管理员直接创建用户（免验证码/邀请码，默认普通角色）

    data 字段：account/password/role

    :param commit: 是否在此提交事务。默认 True（单独建号时自洽）；
        「建号 + RBAC 分配」这类需要**多步原子**的调用方应传 False，
        由调用方在最后统一 commit —— 否则中途失败会留下「账号已落库但
        后续步骤未执行」的僵尸账号（见 admin_users.admin_create_user 注释）。
    """
    from app.core.security import hash_password_async

    role = data.get("role") or "user"
    if role == "admin" and await setting_service.get_bool_setting(db, "admin_require_2fa"):
        raise AppException(
            code=400,
            message="管理员强制二次验证已开启：请先创建普通账号并完成 TOTP 绑定，再提升为管理员",
        )
    if role == "admin":
        # ⚠️ 创建 admin 角色账号 = 授予整个后台的访问权，语义上等价于「把某个已有用户的角色改成
        # admin」，因此除了路由层的 `user:create`，还必须持有 `user:edit`（与 PUT /admin/users/{id}
        # 用的是同一个权限码）。第三轮增量审查 2026-10-11 P1-47：此前 `POST /admin/users` 只由
        # `user:create` 把关，而把**已有**用户升级为 admin 走的是 `user:edit` —— 两扇门只锁了一扇，
        # 只持有 `user:create` 的运营可直接造出管理员账号。super_admin 在 `_assert_any_permission`
        # 里一票放行故不受影响；内置 content_admin / viewer 都没有 `user:create`，所以实际零影响，
        # 只会拦住「自定义角色只给了 user:create」这一种配置。
        from app.services.rbac_service import get_user_rbac, is_super_admin

        rbac = await get_user_rbac(db, admin.id)
        if not await is_super_admin(rbac) and "user:edit" not in set(rbac.get("perms", [])):
            raise AppException(
                code=403, message="创建管理员账号需要「编辑用户」权限，请联系超级管理员"
            )

    account = data["account"].strip()
    existing = (
        await db.execute(select(User).where(User.account == account))
    ).scalar_one_or_none()
    if existing:
        # 已注销账号保留唯一占位，不允许管理员复用（与注册侧复活逻辑不同：
        # 管理员创建无法确认账号所有权，复用会泄露上一账号周期数据）。
        # data 里带上 reason/user_id：前端据此把「409 报错」变成「引导去已注销列表恢复」，
        # 而不是让管理员看到一句话就无路可走。
        if existing.deleted_at:
            raise AppException(
                code=409,
                message="该账号已被注销，不能复用",
                data={
                    "reason": "account_deleted",
                    "user_id": existing.id,
                    "account": existing.account,
                },
            )
        raise AppException(code=409, message="该账号已存在")

    user = User(
        account=account,
        password_hash=await hash_password_async(data["password"]),
        role=role,
        free_used_seconds=0,
        # 新用户注册起 3 小时自然时间倒计时，与注册口径一致
        free_started_at=datetime.now(timezone.utc),
    )
    db.add(user)
    # SAVEPOINT 包裹 flush：并发创建同一账号时撞唯一约束降级为幂等业务错误
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise AppException(code=409, message="该账号已存在")
    await db.refresh(user)  # 加载 server_default 生成的字段
    # 审计（台账 P2-9）：管理员建号是免验证码的旁路入口，
    # 必须能回答「这个账号是谁建的、初始给了什么角色」
    record_admin_action(
        db,
        admin,
        "user_create",
        target_type="user",
        target_id=user.id,
        summary=f"创建用户 {user.id}（{user.account}）",
        detail={"target_account": user.account, "role": user.role},
    )
    if commit:
        await db.commit()
    logger.bind(
        event="admin_create_user", admin_id=admin.id,
        target_user_id=user.id, account=user.account,
    ).info("管理员创建用户")
    return {"id": user.id}