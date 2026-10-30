"""认证服务 - 注册/登录/Token 管理"""

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.security import (
    hash_password_async,
    verify_password_async,
    create_token_pair,
    decode_token,
    get_token_version,
    bump_token_version,
    blacklist_access_token,
    store_refresh_token,
    load_refresh_token,
)
from app.core.session_store import (
    create_session,
    new_session_id,
    revoke_all_sessions,
    revoke_session,
    touch_session,
)
from app.core.exceptions import AppException, AuthenticationError
from app.models.user import User
from app.schemas.auth import RegisterRequest, LoginRequest
from app.utils.verification import verify_code, mask_account, is_phone
from app.redis import log_redis_degraded, redis_client
from app.services.timer_service import get_free_time_status
from app.services.audit_service import cleanup_auth_data_on_reactivation
from app.services.user_settings_service import cleanup_personal_data_on_reactivation
from app.services.invite_service import (
    get_invite_settings,
    find_inviter_by_code,
    bind_invite_and_reward,
)
from app.services.totp_service import is_totp_enabled, verify_login_totp

# 密码登录账号级失败锁定：全局限流仅按 IP，防不了分布式对单账号的爆破；
# 复用验证码模块「失败 N 次锁 M 秒」的成熟模式
LOGIN_FAIL_LIMIT = 10
LOGIN_LOCK_SECONDS = 900


def build_user_profile(user: User) -> dict:
    """构建包含计算字段(auth_status, free_remaining_seconds)的用户信息字典，
    供 /auth/login、/auth/register、/auth/me 复用，保证登录后立即拿到与 /auth/me 一致的数据
    （剩余时长按「注册起 3 小时自然时间倒计时」口径，timer_service 统一实现）"""
    remaining = get_free_time_status(user)["free_remaining_seconds"]
    if user.role == "vip" or user.role == "admin":
        auth_status = "approved"
    elif user.pending_auth_code:
        auth_status = "pending"
    else:
        auth_status = "none"
    # 老数据「account 即手机号」由迁移回填 phone；运行期再兜底一次，避免迁移窗口内读不到
    phone = user.phone or (user.account if is_phone(user.account) else None)
    return {
        "id": user.id,
        "account": user.account,
        "role": user.role,
        "nickname": user.nickname,
        "bio": user.bio,
        # 账号体系为单字段 account（换绑邮箱实为改 account）：含 @ 视为邮箱账号，
        # email 字段返回该值供前端展示；手机号账号返回 None
        "email": user.account if "@" in user.account else None,
        "phone": phone,
        "phone_masked": mask_account(phone) if phone else None,
        "has_phone": bool(phone),
        "wechat_bound": bool(user.wechat_unionid or user.wechat_openid or user.wechat_web_openid),
        "free_used_seconds": user.free_used_seconds,
        "free_remaining_seconds": remaining,
        "points": user.points or 0,
        "vip_expire_time": user.vip_expire_time.isoformat() if user.vip_expire_time else None,
        "pending_auth_code": user.pending_auth_code,
        "has_pending_auth_code": bool(user.pending_auth_code),
        "auth_status": auth_status,
        "avatar_url": user.avatar_url,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


async def register(req: RegisterRequest, db: AsyncSession) -> dict:
    """用户注册"""
    # 邀请码预校验（先于验证码）：无效时直接 400 让用户改正，此时验证码尚未消费、无需重发；
    # 功能关闭时静默忽略邀请码，不阻断注册
    inviter = None
    invite_reward = 0
    if req.invite_code and req.invite_code.strip():
        enabled, invite_reward = await get_invite_settings()
        if enabled:
            inviter = await find_inviter_by_code(db, req.invite_code)
            if not inviter:
                raise AppException(code=400, message="邀请码无效，请检查后重新填写")

    # 先校验验证码：旧顺序「先查账号存在性再验码」让攻击者无需有效验证码即可批量探测账号（枚举 Oracle）；
    # 验码成功即消费，注册因 409 失败需重新获取（防枚举的标准取舍）
    if not await verify_code(req.account, req.code):
        raise AppException(code=400, message="验证码错误或已过期")

    # 检查账号是否已存在
    result = await db.execute(select(User).where(User.account == req.account))
    existing = result.scalar_one_or_none()
    if existing and existing.deleted_at is None:
        raise AppException(code=409, message="该账号已注册")

    if existing:
        # 账号已软删除 → 复活（清 deleted_at、重置密码），避免唯一约束冲突报 500
        existing.deleted_at = None
        existing.password_hash = await hash_password_async(req.password)
        existing.role = "user"
        existing.free_used_seconds = 0
        existing.free_started_at = datetime.now(timezone.utc)  # 重新计算 3 小时免费倒计时
        # 清理上一账号周期的认证数据（待审核标记/VIP/认证申请记录），认证状态从零开始
        await cleanup_auth_data_on_reactivation(db, existing)
        # 清空上一账号周期的个人数据（收藏/学习/下载/消息/工具日志），重置昵称/头像/简介
        await cleanup_personal_data_on_reactivation(db, existing)
        user = existing
        # 复活账号不算新用户：不重新绑定邀请关系、不重复发奖
        #（防「注销-复活换邀请码」刷分；原邀请关系保留）
        inviter = None
    else:
        # 创建用户
        user = User(
            account=req.account,
            password_hash=await hash_password_async(req.password),
            role="user",
            free_used_seconds=0,
            free_started_at=datetime.now(timezone.utc),  # 注册起 3 小时自然时间倒计时
        )
        db.add(user)

    # SAVEPOINT 包裹 flush：并发注册同一新账号时第二个 INSERT 撞 account 唯一约束，
    # 原先未捕获落入全局 500；降级为幂等业务错误
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        # ⚠️ 不能一律说「该账号已注册」：主键是 10 位**随机数**（generate_id），
        # 极小概率撞上已有行，此时账号其实没注册过 —— 用户换什么密码都注册不上，
        # 客服也无法从这句话定位。二次确认账号是否真的存在，再决定文案。
        if await _account_exists(db, req.account):
            raise AppException(code=409, message="该账号已注册")
        logger.bind(event="register_pk_conflict").warning(
            f"注册落库冲突但账号不存在（疑似随机主键碰撞）: account={mask_account(req.account)} err={exc.orig}"
        )
        raise AppException(code=409, message="注册失败，请重新提交")
    await db.refresh(user)  # 加载 server_default 生成的字段

    # 全新用户且带有效邀请人：绑定邀请关系并给邀请人发放积分奖励
    # （关系/余额/流水与注册同一事务，失败整体回滚）
    if inviter:
        await bind_invite_and_reward(db, inviter, user, invite_reward)

    # ⚠️ 必须显式提交：get_db 的 teardown commit 在响应发送之后才执行，
    # 「客户端已拿到 access_token」与「账号真正落库」之间存在窗口——
    # 紧接着的 /auth/me 可能读不到未提交行而 401，或 teardown 未执行导致账号从未创建
    # 但 refresh_token 已写进 Redis。位置在发奖之后、签发 token 之前。
    await db.commit()

    # 账号属 PII，日志统一脱敏 (ARCH-014)
    logger.info(f"新用户注册: {mask_account(req.account)} (id={user.id})")

    # 生成 Token（嵌入当前令牌版本号，支持后续版本号吊销）
    tokens = create_token_pair(user.id, user.role, ver=await get_token_version(user.id))

    # 存 refresh_token（Redis 故障时仅记降级日志，不阻断已签发的 token）
    await store_refresh_token(user.id, tokens["refresh_token"])

    return {
        **tokens,
        "user": build_user_profile(user),
    }


# 虚构密码哈希：账号不存在时也执行一次等代价校验，抹平响应时间差（防账号枚举时序侧信道）
_dummy_password_hash: str | None = None


async def _account_exists(db: AsyncSession, account: str) -> bool:
    """账号是否真的存在（含软删）。

    只给「落库撞 IntegrityError 之后」用：区分「并发注册同一账号」与「随机主键碰撞」。
    刻意不判 `deleted_at` —— 已注销的账号同样占用唯一约束，不能当成"不存在"。
    """
    result = await db.execute(select(User).where(User.account == account))
    return result.scalar_one_or_none() is not None


def _lock_scope(account: str, client_ip: str | None) -> str:
    """失败计数与锁定的作用域键：按 (IP, 账号) 而非只按账号——只按账号时，攻击者
    每 15 分钟发 10 次假密码就能把受害者永久锁在门外且无法自救。
    代价是跨大量 IP 的慢速撞库无账号级硬锁（正解是失败后要求人机挑战，暂缓）；
    单 IP 维度仍由敏感端点限流兜底。"""
    return f"{account}|{client_ip}" if client_ip else account


async def _record_login_failure(account: str, client_ip: str | None = None) -> None:
    """登录失败计数；达上限后锁定该 (IP, 账号) 一段时间（Redis 故障时跳过，不阻断登录主流程）"""
    scope = _lock_scope(account, client_ip)
    fail_key = f"login_fail:{scope}"
    lock_key = f"login_lock:{scope}"
    try:
        fails = await redis_client.incr(fail_key)
        await redis_client.expire(fail_key, LOGIN_LOCK_SECONDS)
        if fails >= LOGIN_FAIL_LIMIT:
            await redis_client.set(lock_key, "1", ex=LOGIN_LOCK_SECONDS)
            logger.bind(event="login_locked", account=mask_account(account)).warning(
                f"同一来源连续登录失败 {fails} 次，临时锁定 {LOGIN_LOCK_SECONDS // 60} 分钟"
            )
    except Exception:
        log_redis_degraded("auth._record_login_failure", "登录失败计数写入失败（Redis 不可用，跳过锁定）")


async def _is_login_locked(account: str, client_ip: str | None = None) -> bool:
    """该 (IP, 账号) 是否因连续登录失败被临时锁定（Redis 故障时降级放行）"""
    try:
        return bool(await redis_client.exists(f"login_lock:{_lock_scope(account, client_ip)}"))
    except Exception:
        # 降级放行是刻意决定（兜底还有密码强度与限流），但必须留痕：
        # 否则 Redis 长期不可用时锁定机制整体停摆且日志一片干净
        log_redis_degraded("auth._is_login_locked", "登录锁定状态查询失败，本次按未锁定放行")
        return False


# TOTP 失败必须用**独立计数**：`verify_account_password` 成功时会清空密码失败计数，
# 若共用同一计数器，攻击者「密码正确 + 验证码乱猜」会每次都从 0 开始，永远锁不住。
TOTP_FAIL_LIMIT = 5
TOTP_LOCK_SECONDS = 900


async def _record_totp_failure(account: str, client_ip: str | None = None) -> None:
    """二次验证码失败计数（独立键，达上限后锁定同样时长）"""
    scope = _lock_scope(account, client_ip)
    fail_key = f"totp_fail:{scope}"
    lock_key = f"totp_lock:{scope}"
    try:
        fails = await redis_client.incr(fail_key)
        await redis_client.expire(fail_key, TOTP_LOCK_SECONDS)
        if fails >= TOTP_FAIL_LIMIT:
            await redis_client.set(lock_key, "1", ex=TOTP_LOCK_SECONDS)
            logger.bind(event="totp_locked", account=mask_account(account)).warning(
                f"同一来源连续 {fails} 次二次验证失败，临时锁定 {TOTP_LOCK_SECONDS // 60} 分钟"
            )
    except Exception:
        log_redis_degraded("auth._record_totp_failure", "二次验证失败计数写入失败（Redis 不可用，跳过锁定）")


async def _is_totp_locked(account: str, client_ip: str | None = None) -> bool:
    """二次验证是否处于锁定期（Redis 故障时降级放行，仍有密码这一道门）"""
    try:
        return bool(await redis_client.exists(f"totp_lock:{_lock_scope(account, client_ip)}"))
    except Exception:
        log_redis_degraded("auth._is_totp_locked", "二次验证锁定状态查询失败，本次按未锁定放行")
        return False


async def _clear_totp_failures(account: str, client_ip: str | None = None) -> None:
    scope = _lock_scope(account, client_ip)
    try:
        await redis_client.delete(f"totp_fail:{scope}", f"totp_lock:{scope}")
    except Exception:
        log_redis_degraded("auth._clear_totp_failures", "清除二次验证失败计数失败（Redis 不可用）")


async def _clear_login_failures(account: str, client_ip: str | None = None) -> None:
    """登录成功后清除失败计数与锁定（Redis 故障时仅记日志，不阻断登录主流程）"""
    scope = _lock_scope(account, client_ip)
    try:
        await redis_client.delete(f"login_fail:{scope}", f"login_lock:{scope}")
    except Exception:
        # 静默吞掉会残留失败计数，后续失败可能在不足上限时被提前锁定，
        # 需留痕便于排查（与 _record_login_failure/_is_login_locked 降级风格一致）
        log_redis_degraded("auth._clear_login_failures", "清除登录失败计数失败（Redis 不可用）")


async def verify_account_password(
    db: AsyncSession, account: str, password: str, *, client_ip: str | None = None
) -> User:
    """账号密码校验的唯一入口：锁定检查 + 时序均衡 + 失败计数 + 成功清零；
    三条登录路径（Web 登录、小程序登录/绑定）都必须走这里，防爆破与时序保护不缺位"""
    if await _is_login_locked(account, client_ip):
        raise AuthenticationError(
            f"登录失败次数过多，请 {LOGIN_LOCK_SECONDS // 60} 分钟后再试"
        )

    result = await db.execute(
        select(User).where(User.account == account, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()

    global _dummy_password_hash
    if not user:
        # 账号不存在时对 dummy hash 做一次真实 bcrypt 校验再统一报错，
        # 使响应时间与账号存在时一致，防止通过时序差异枚举注册账号
        if _dummy_password_hash is None:
            _dummy_password_hash = await hash_password_async("timing-equalizer-dummy")
        await verify_password_async(password, _dummy_password_hash)
        await _record_login_failure(account, client_ip)
        raise AuthenticationError("账号或密码错误")
    if not await verify_password_async(password, user.password_hash):
        await _record_login_failure(account, client_ip)
        raise AuthenticationError("账号或密码错误")

    await _clear_login_failures(account, client_ip)
    return user


async def enforce_totp_if_enabled(
    db: AsyncSession,
    user: User,
    *,
    account: str,
    totp_code: str | None,
    client_ip: str | None,
) -> None:
    """启用 TOTP 的账号必须再验一次动态码（Web 密码登录与手机号登录共用）。

    手机号验证码只能证明「控制该号码」，不能替代第二条因子；
    不在这里统一收口，新登录路径就会静默绕过 TOTP。
    """
    if not is_totp_enabled(user):
        return
    if await _is_totp_locked(account, client_ip):
        raise AuthenticationError(
            f"二次验证失败次数过多，请 {TOTP_LOCK_SECONDS // 60} 分钟后再试"
        )
    if not totp_code:
        raise AppException(
            code=401,
            message="该账号已开启二次验证，请输入验证码",
            data={"totp_required": True},
        )
    if not await verify_login_totp(db, user, totp_code):
        await _record_totp_failure(account, client_ip)
        raise AppException(
            code=401,
            message="二次验证码不正确",
            data={"totp_required": True},
        )
    await _clear_totp_failures(account, client_ip)


async def issue_login_tokens(
    db: AsyncSession,
    user: User,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> dict:
    """签发登录 Token 对；Web / 手机号 / 微信 / 小程序共用同一口径。

    每次登录分配一个新的会话 id（sid）并登记到会话簿：
    refresh 按 sid 隔离，多设备互不覆盖；用户才能在「登录设备」里看到并单独踢掉某一台。
    """
    if await check_vip_expiry(user):
        await db.commit()
    sid = new_session_id()
    tokens = create_token_pair(user.id, user.role, ver=await get_token_version(user.id), sid=sid)
    await store_refresh_token(user.id, tokens["refresh_token"], sid=sid)
    access_payload = decode_token(tokens["access_token"]) or {}
    await create_session(
        user.id, sid, access_payload.get("jti", ""), user_agent=user_agent, client_ip=client_ip
    )
    return {**tokens, "user": build_user_profile(user)}


async def login(
    req: LoginRequest,
    db: AsyncSession,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> dict:
    """用户登录（密码 + 可选的 TOTP 二次验证）

    二次验证是**第二道门**，不是替代品：密码仍然要正确，且验证码错误会计入
    独立的失败计数（5 次锁 15 分钟）—— 只靠密码那次计数锁不住「密码已泄露后猜码」。
    """
    # 账号级失败锁定 + 时序均衡，统一在 verify_account_password 内实现
    user = await verify_account_password(db, req.account, req.password, client_ip=client_ip)
    await enforce_totp_if_enabled(
        db, user, account=req.account, totp_code=req.totp_code, client_ip=client_ip
    )
    logger.info(f"用户登录: {mask_account(req.account)} (id={user.id})")
    return await issue_login_tokens(db, user, client_ip=client_ip, user_agent=user_agent)


async def refresh_token(refresh_token_str: str, db: AsyncSession) -> dict:
    """刷新 Token"""
    payload = decode_token(refresh_token_str)
    if not payload or payload.get("type") != "refresh":
        raise AuthenticationError("refresh_token 无效")

    user_id = payload.get("sub")
    if not user_id:
        raise AuthenticationError("refresh_token 无效")

    # 检查 Redis 中的 refresh_token 是否匹配
    # （读路径 fail-closed：Redis 故障时抛 503 而非放行，避免撤销机制失效）
    # sid 存在时只认该会话的那一枚：会话被踢掉后 key 已删除，这里自然返回 None → 401
    sid = payload.get("sid")
    stored_token = await load_refresh_token(user_id, sid)
    if not stored_token or stored_token != refresh_token_str:
        raise AuthenticationError("refresh_token 已失效，请重新登录")

    # 查询用户
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        raise AuthenticationError("Invalid refresh token")

    result = await db.execute(
        select(User).where(User.id == user_id_int, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise AuthenticationError("用户不存在")

    # 检查 VIP 过期（降级显式落库，理由同 login）
    if await check_vip_expiry(user):
        await db.commit()

    # 生成新 Token 对（嵌入当前令牌版本号；沿用同一 sid，会话不变只是换了令牌）
    tokens = create_token_pair(user.id, user.role, ver=await get_token_version(user.id), sid=sid)
    # 轮换写入（Redis 故障时仅记降级日志）
    await store_refresh_token(user.id, tokens["refresh_token"], sid=sid)
    if sid:
        # 续期会话并更新 access jti：单会话吊销时要靠它把当前这枚 access token 拉黑
        access_payload = decode_token(tokens["access_token"]) or {}
        await touch_session(user.id, sid, access_payload.get("jti"))

    return tokens


def _session_id_from_token(token: str | None) -> str | None:
    """从 access_token 里取会话 id（本次改造前的旧 token 没有 sid，返回 None）"""
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        return None
    return payload.get("sid")


async def logout(user_id: int, access_token: str | None = None):
    """登出 - 吊销当前会话。

    做了三件事：把当前 access_token 拉黑（否则剩余有效期内仍可调接口）、
    删掉该会话的 refresh_token（否则 7 天内还能续期）、从会话簿里移除这条记录。
    内部均已做 Redis 故障降级（仅记日志，不阻断登出）。
    """
    if access_token:
        await blacklist_access_token(access_token)

    sid = _session_id_from_token(access_token)
    if sid:
        # 只踢当前这一台：其他设备的 refresh 键各自独立，不受影响
        await revoke_session(user_id, sid)
        logger.info(f"用户登出: id={user_id} sid={sid[:8]}…")
        return

    # 兼容旧 token（无 sid）：退回删除「每用户一枚」的老键
    try:
        await redis_client.delete(f"refresh_token:{user_id}")
    except Exception:
        log_redis_degraded("auth.logout", f"登出清理 refresh_token 失败（Redis 不可用）: id={user_id}")
    logger.info(f"用户登出: id={user_id}")


async def logout_all(user_id: int, access_token: str | None = None) -> None:
    """退出全部设备：清空会话簿 + 提升令牌版本号（所有已签发 access/refresh 立即失效）。

    两件事都要做，缺一不可：
    - 清会话簿 / 删各会话 refresh 键 → 各设备的 refresh 立刻失效；
    - 提升令牌版本号 → 手上还没过期的 access_token 也一并作废。
    """
    if access_token:
        await blacklist_access_token(access_token)
    revoked = await revoke_all_sessions(user_id)
    await bump_token_version(user_id)
    try:
        # 兼容旧 token：本次改造前的老键也一并清掉
        await redis_client.delete(f"refresh_token:{user_id}")
    except Exception:
        log_redis_degraded("auth.logout_all", f"退出全部设备时清理 refresh_token 失败: id={user_id}")
    logger.bind(event="logout_all", user_id=user_id, sessions=len(revoked)).info(
        f"用户 {user_id} 退出全部设备（{len(revoked)} 个会话）"
    )


async def check_vip_expiry(user: User) -> bool:
    """检查 VIP 是否过期，过期则降级；返回是否发生了降级（调用方需 commit）"""
    if user.role == "vip" and user.vip_expire_time:
        expire = user.vip_expire_time
        # MySQL DateTime 列返回 naive datetime，统一视为 UTC
        if expire.tzinfo is None:
            expire = expire.replace(tzinfo=timezone.utc)
        if expire < datetime.now(timezone.utc):
            user.role = "user"
            user.vip_expire_time = None
            logger.info(f"VIP 过期降级: id={user.id}")
            return True
    return False
