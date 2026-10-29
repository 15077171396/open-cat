"""工具服务"""

import asyncio
import time
from datetime import datetime, timezone
import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from loguru import logger

from app.models.tool import Tool
from app.models.download import ToolUsageLog
from app.models.user import User
from app.services.timer_service import check_and_consume, is_vip_effective
from app.services.point_service import record_transaction, TYPE_CONSUME
from app.services.ai.factory import get_provider
from app.services import ai_usage_service
from app.config import settings
from app.core.exceptions import AppException, FreeTimeExhaustedError, NotFoundError
from app.core.content_types import CONTENT_TYPES
from app.services.detail_shape import build_detail_base
from app.redis import log_redis_degraded, redis_client
from app.services.search import TOOL_SEARCH_COLS, apply_keyword_filter, fetch_page
from app.services.category_service import is_known_category
from app.services.content_cache import get_cached_list, list_cache_key, set_cached_list

# AI 工具输入规模上限：防超长 payload 透传给计费 AI API
_MAX_INPUT_FIELDS = 20
_MAX_INPUT_VALUE_CHARS = 2000


async def get_tools(db: AsyncSession, page: int = 1, page_size: int = 8, keyword: str = None, category: str = None) -> dict:
    """获取工具列表"""
    # 列表缓存：只缓存无关键词的浏览路径（搜索结果长尾、key 基数无上限）。
    # 分类必须是登记过的值，否则任意随机分类都会生成新 key（C-05）。
    cache_key = (
        None
        if keyword or not await is_known_category(db, "tool", category)
        else list_cache_key("tools", {"category": category, "page": page, "page_size": page_size})
    )
    if cache_key:
        cached = await get_cached_list(cache_key)
        if cached is not None:
            return cached
    conditions = [Tool.is_published == True]  # noqa: E712
    if category and category != "all":
        conditions.append(Tool.category == category)

    # 关键词检索收敛进 search 统一入口（public/admin 两侧统一，加检索列只改一处）；
    # ⚠️ 必须在其它条件都进 conditions 之后调用：它返回的 total 就是当时条件集的命中数
    total = await apply_keyword_filter(db, Tool, conditions, TOOL_SEARCH_COLS, keyword)
    if not total:
        # 无命中就别再发分页查询：搜错词/无结果是最常见场景，而那条查询同样要做
        # 一次全表 LIKE 扫描（第五轮审查 PERF-01）
        payload = {"items": [], "total": 0, "page": page, "page_size": page_size}
        if cache_key:
            await set_cached_list(cache_key, payload)
        return payload

    query = (
        select(Tool)
        .options(load_only(
            Tool.id, Tool.name, Tool.description, Tool.category,
            Tool.tool_type, Tool.usage_count, Tool.access_mode, Tool.points_cost,
        ))
        .where(*conditions)
        # 主键是随机 10 位数（防枚举爬取），ORDER BY id DESC 顺序等于随机，
        # 改为 created_at 倒序并以 id 兜底
        .order_by(Tool.created_at.desc(), Tool.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    tools = await fetch_page(db, query, keyword=keyword)

    items = [
        {
            "id": t.id, "name": t.name, "description": t.description,
            "category": t.category, "tool_type": t.tool_type,
            "usage_count": t.usage_count,
            "access_mode": t.access_mode or "auth",
            "points_cost": t.points_cost or 0,
        }
        for t in tools
    ]
    payload = {"items": items, "total": total, "page": page, "page_size": page_size}
    if cache_key:
        await set_cached_list(cache_key, payload)
    return payload


async def get_tool_detail(db: AsyncSession, tool_id: int) -> dict:
    """获取工具详情"""
    # is_published 过滤：与列表口径一致，下架工具不能凭 ID 继续被查看
    tool = (
        await db.execute(
            select(Tool).where(Tool.id == tool_id, Tool.is_published == True)  # noqa: E712
        )
    ).scalar_one_or_none()
    if not tool:
        raise NotFoundError("工具不存在")

    # 公共字段由 build_detail_base 统一产出（EXT-04）：工具没有封面/作者/SEO 列，
    # 这些键会以 None 出现（省略会让前端多一条「有没有这个键」的类型分支）。
    # 浏览量也返回 None —— Tool 表没有 view_count 列，用 usage_count 表达使用量。
    return build_detail_base(
        CONTENT_TYPES["tool"],
        tool,
        extra={
            "tool_type": tool.tool_type,
            # ai_config（含提示词模板）不对外返回：核心业务资产且前端仅需 form_schema
            "form_schema": tool.form_schema,
            "usage_count": tool.usage_count,
            "access_mode": tool.access_mode or "auth",
            "points_cost": tool.points_cost or 0,
        },
    )


async def _charge_points(
    db: AsyncSession, user: User, tool: Tool, *, record_tx: bool = True
) -> int:
    """积分模式扣费：数据库原子扣减，余额不足抛 403；VIP/admin 免费。
    AI 路径传 record_tx=False 仅扣减不写流水——扣费需先于外部 AI 调用独立提交（避免长事务持锁），
    流水在调用成功后补记，失败由 run_tool 的补偿逻辑退还。"""
    cost = tool.points_cost or 0
    # VIP 按到期时间即时判定（防过期 VIP 权益滞留），与 timer_service 口径一致
    if cost <= 0 or is_vip_effective(user):
        return 0
    # 原子扣减（WHERE points >= cost）：并发请求下防超扣/负余额
    result = await db.execute(
        update(User)
        .where(User.id == user.id, User.points >= cost)
        .values(points=User.points - cost)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        raise AppException(
            code=403,
            message=f"积分不足：本工具每次运行消耗 {cost} 积分，当前余额 {user.points or 0} 积分",
            data={"insufficient_points": True, "need_points": cost},
        )
    # 扣减成功后回读真实余额再写流水：并发充值/扣减场景下内存快照可能陈旧，
    # 直接用「内存值-cost」会记出与真实余额不符甚至为负的 balance_after
    await db.refresh(user)
    if record_tx:
        # 写积分流水：与扣减同一事务，随调用方提交
        record_transaction(
            db,
            user_id=user.id,
            points=-cost,
            balance_after=user.points or 0,
            type=TYPE_CONSUME,
            description=f"运行工具：{tool.name}",
        )
    return cost


async def run_tool(db: AsyncSession, user: User, tool_id: int, input_data: dict) -> dict:
    """运行工具。AI 工具采用三段式事务：①短事务校验+扣费/时长并提交（AI 调用最坏 ~90s，
    同事务会占住行锁与连接池拖垮全站）；②AI 调用（不占 DB 连接）；③成功补记流水/日志/计数，
    失败补偿退还扣费并回退时长（对用户等价于整体回滚）。前端计算工具保持单事务语义。"""
    # is_published 过滤：下架工具不能凭 ID 运行（AI 工具运行产生真实计费调用）
    tool = (
        await db.execute(
            select(Tool).where(Tool.id == tool_id, Tool.is_published == True)  # noqa: E712
        )
    ).scalar_one_or_none()
    if not tool:
        raise NotFoundError("工具不存在")

    # 输入规模校验 (先于限额与计时，纯校验失败不产生任何消耗)
    if tool.tool_type == "ai":
        if len(input_data) > _MAX_INPUT_FIELDS:
            raise AppException(code=400, message=f"输入字段最多 {_MAX_INPUT_FIELDS} 个")
        for k, v in input_data.items():
            if isinstance(v, str) and len(v) > _MAX_INPUT_VALUE_CHARS:
                raise AppException(
                    code=400, message=f"字段 {k} 长度不能超过 {_MAX_INPUT_VALUE_CHARS} 字符"
                )

    # 每日 AI 调用限制：原子 incr-then-check（先 get 再 incr 在并发下会同时通过检查导致超限）
    daily_key = None
    # 全站成本护栏占位凭据：非 None 表示已占用一个全站名额，
    # 调用失败/被用户级限额拒绝时必须归还，否则计数会把正常调用逐步顶到上限
    global_ticket = None
    if tool.tool_type == "ai":
        # 全站额度检查放在**单用户限额之前**：站点级已熔断时不应再消耗用户的
        # 个人额度（用户会看到 429，但个人计数不该被这笔没跑成的调用占掉）
        global_ticket = await ai_usage_service.reserve_quota()

        daily_key = f"ai_daily:{user.id}:{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        try:
            count = await redis_client.incr(daily_key)
            if count == 1:
                await redis_client.expire(daily_key, 86400)  # 当日首次调用设置过期
            if count > settings.AI_DAILY_LIMIT_PER_USER:
                await redis_client.decr(daily_key)  # 预占回退
                # 用户级超限同样要归还全站占位（否则被拒请求会持续抬高全站计数）
                await ai_usage_service.release_quota(global_ticket)
                global_ticket = None
                raise AppException(
                    code=429,
                    message=f"今日 AI 调用次数已达上限 ({settings.AI_DAILY_LIMIT_PER_USER} 次/天)",
                )
        except AppException:
            raise
        except Exception:
            # Redis 故障时跳过每日限额（fail-open，与限流中间件非敏感端点策略一致），
            # 缓存/配额层故障不应放大为工具接口整体 500
            daily_key = None
            log_redis_degraded("tool.daily_limit_check", "Redis 不可用，AI 每日限额检查降级跳过")

    result_text = ""
    access_mode = tool.access_mode or "auth"
    # 阶段一状态（供失败补偿判定；补偿路径 rollback 会使 ORM 对象过期，
    # 故所需的标量值均在阶段一前后以本地变量捕获，不在补偿中访问 ORM 属性）
    user_id = user.id                            # 冗余捕获：补偿 UPDATE 定位用
    charged_cost = 0                             # 阶段一已提交扣减的积分数
    charged_balance = 0                          # 扣减后余额（阶段三补记流水用）
    stats_before = user.free_used_seconds or 0   # auth 模式时长统计快照
    stats_increased = False                      # 阶段一是否累加了时长统计
    phase1_committed = False                     # 阶段一是否已提交（仅此时失败才需补偿）
    try:
        if tool.tool_type == "ai" and tool.ai_config:
            # AI 工具使用权限：free=免费 / auth=消耗免费时长(耗尽需认证) / points=扣积分(失败补偿退还)
            if access_mode == "auth":
                timer_result = await check_and_consume(user, db, 10)
                if timer_result["is_exhausted"]:
                    await db.commit()  # 先保存时长消耗，再抛出异常
                    raise FreeTimeExhaustedError()
            elif access_mode == "points":
                # 仅扣减不写流水：流水在 AI 成功后补记，失败由补偿退还
                charged_cost = await _charge_points(db, user, tool, record_tx=False)
                charged_balance = user.points or 0

            # 阶段一提交：扣费/时长统计先行落库，释放 users 行锁与连接池连接
            await db.commit()
            phase1_committed = True
            stats_increased = (user.free_used_seconds or 0) != stats_before

            # 阶段二：AI 调用（无数据库连接占用）
            provider = await get_provider(tool.ai_config.get("model"))
            prompt_template = tool.ai_config.get("prompt_template", "{input}")

            # 按 form_schema 校验输入，仅允许已定义字段 (防 prompt 模板注入)
            if tool.form_schema and isinstance(tool.form_schema, dict):
                allowed_keys = {f["name"] for f in tool.form_schema.get("fields", []) if "name" in f}
                input_data = {k: v for k, v in input_data.items() if k in allowed_keys}

            try:
                prompt = prompt_template.format(**input_data)
            except KeyError as e:
                raise AppException(code=400, message=f"缺少必填字段: {e}")

            messages = [{"role": "user", "content": prompt}]
            # 总耗时预算：重试叠加超时最坏 ≈93s，2 worker 下两个慢请求就能让全站排队；
            # 整个调用（含重试）受 AI_TOTAL_BUDGET_SECONDS 硬约束，按剩余预算截断，超预算快速失败
            deadline = time.monotonic() + settings.AI_TOTAL_BUDGET_SECONDS
            # 本次调用的用量出参（重试时会覆盖成最后一次成功响应的值，符合预期）
            usage_sink: dict = {}
            try:
                for attempt in range(settings.AI_RETRY_COUNT + 1):
                    try:
                        remaining = max(1.0, deadline - time.monotonic())
                        # ⚠️ usage_out 必须按引用传出：wait_for 在 Python ≤3.11 会把协程包成新 Task，
                        # 而 provider 是进程级单例，靠读 provider.last_usage 记账时并发调用会互相覆盖（用量记到别人头上）
                        result_text = await asyncio.wait_for(
                            provider.chat(messages, usage_out=usage_sink), timeout=remaining
                        )
                        break
                    except (httpx.HTTPError, asyncio.TimeoutError):
                        wait = 2 ** attempt
                        # 仅在「还有预算容纳一次退避」时才重试，否则直接失败
                        if (
                            attempt < settings.AI_RETRY_COUNT
                            and time.monotonic() + wait < deadline
                        ):
                            logger.warning(
                                f"AI 调用重试 {attempt + 1}/{settings.AI_RETRY_COUNT}, 等待 {wait}s"
                            )
                            await asyncio.sleep(wait)
                            continue
                        if attempt < settings.AI_RETRY_COUNT:
                            logger.warning(
                                f"AI 调用放弃重试：已接近总预算 {settings.AI_TOTAL_BUDGET_SECONDS}s"
                                f"（剩余 {deadline - time.monotonic():.1f}s，退避需 {wait}s）"
                            )
                        raise
            except (httpx.HTTPError, asyncio.TimeoutError) as e:
                logger.error(f"AI 工具运行失败: {e}")
                raise AppException(code=503, message="AI 服务暂时不可用，请稍后再试")

            # 记账本次真实 token 用量。统计失败不影响业务（record_provider_usage 内部已吞掉 Redis 异常）
            await ai_usage_service.record_provider_usage(provider, usage=usage_sink)

            # 阶段三（成功）：补记扣费流水 + 使用日志 + 自增次数 (每日计数已在开头原子预占)
            if charged_cost > 0:
                record_transaction(
                    db,
                    user_id=user.id,
                    points=-charged_cost,
                    balance_after=charged_balance,
                    type=TYPE_CONSUME,
                    description=f"运行工具：{tool.name}",
                )
            db.add(ToolUsageLog(user_id=user.id, tool_id=tool_id, duration_seconds=10))
            # 单条 UPDATE 自增（PERF）：ORM 读-改-写在并发下丢失更新，
            # 与 tutorial/resource 计数写法保持一致
            await db.execute(
                update(Tool)
                .where(Tool.id == tool_id)
                .values(usage_count=Tool.usage_count + 1)
            )
        else:
            # 纯前端计算工具（如图片压缩）：本地处理零服务成本，free/auth 不拦截不扣费；
            # 积分模式由前端工作台在每次操作前调用本接口完成按次扣费
            if access_mode == "points":
                await _charge_points(db, user, tool)
                await db.execute(
                    update(Tool)
                    .where(Tool.id == tool_id)
                    .values(usage_count=Tool.usage_count + 1)
                )
            result_text = "该工具为前端计算类型，请在页面上直接使用。"
    except Exception:
        # 本次请求未成功完成 AI 调用，回退每日计数预占，保持"仅成功调用计数"语义。
        # 回退本身也可能因 Redis 故障失败——必须吞掉以保留原始业务异常，
        # 否则 Redis 错误会掩盖真正的失败原因（BUG）
        if daily_key:
            try:
                await redis_client.decr(daily_key)
            except Exception:
                log_redis_degraded("tool.rollback_daily_count", "回退每日 AI 计数失败（Redis 不可用）")
        # 归还全站额度占位：本次 AI 调用未成功，不应占用全站配额；
        # 放在用户级回退之后、补偿之前，且自身不抛异常，保证不掩盖真正要抛的业务异常
        await ai_usage_service.release_quota(global_ticket)
        # 失败补偿：退还阶段一已提交的扣费并回退时长统计，等价于旧实现的整体回滚。
        # 补偿失败仅记日志不掩盖原始异常（此时用户已被扣费，需人工核查流水）
        if phase1_committed:
            try:
                # 会话可能因阶段三写入/提交失败处于失败态：先回滚未决写入，
                # 再在新事务中执行补偿（会话干净时 rollback 为 no-op）
                await db.rollback()
                if charged_cost > 0:
                    await db.execute(
                        update(User)
                        .where(User.id == user_id)
                        .values(points=User.points + charged_cost)
                        .execution_options(synchronize_session=False)
                    )
                if stats_increased:
                    # 属性赋值不触发加载，flush 按对象身份键定位行
                    user.free_used_seconds = stats_before
                await db.commit()
            except Exception:
                logger.error(
                    f"AI 工具失败补偿退还异常（用户 {user_id}，待退还 {charged_cost} 积分），"
                    "请人工核查积分流水"
                )
        raise

    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送），
    # 前端工具路径「扣费流水/计数自增」与 AI 路径阶段三「流水/使用日志/计数」在此原子提交
    await db.commit()
    return {"result": result_text, "tool_name": tool.name}
