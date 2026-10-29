"""内容审核服务：敏感词过滤 + 命中留痕。设计取舍：
1. 词库可运营：内置词库只做兜底基线，站点方经 MODERATION_EXTRA_WORDS 追加（调整词库无需发版）；
2. 归一化后再匹配：全角/半角/大小写/干扰符先抹掉，「加 微 信」类拆分绕过同样命中；
3. 处置三选一（reject 拒绝 / mask 打码入库 / review 待人工复核），无论哪种都写留痕；
4. 未命中不写日志（全量留痕会随 UGC 量膨胀、淹没有效信号）。
⚠️ 图片审核不在范围内：头像走举报通道人工兜底。
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata

from loguru import logger
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AppException
from app.models.moderation import (
    CONTENT_TYPE_LABELS,
    ContentModerationLog,
    EXCERPT_MAX_LENGTH,
    MAX_MODERATION_LOGS_PER_USER_PER_DAY,
    MODERATION_ACTION_LABELS,
    MODERATION_ACTIONS,
    MODERATION_STATUSES,
    MODERATION_STATUS_LABELS,
)
from app.utils.sanitize import sanitize_plain_text
from app.utils.time import utcnow

# 默认词库（兜底基线，勿当作完整方案）
#
# 只收录三类**通用社区治理**词：广告引流 / 黑产交易 / 人身攻击。
# 不收录任何需要站点方自行界定的品类——那些应通过 MODERATION_EXTRA_WORDS
# 或管理端词库维护追加，改配置即可生效，不必发版。
DEFAULT_WORDS: tuple[str, ...] = (
    # 广告引流：把站内流量导到私域，是最常见的 UGC 污染
    "加微信", "微信同号", "微信号", "加微", "加qq", "qq同号", "加q群",
    "扫码加", "私聊我", "进群", "拉你进群", "兼职日结", "日入过万", "躺赚",
    # 黑产交易
    "博彩", "赌博", "开户返利", "刷单", "刷赞", "刷量", "买卖账号", "套现",
    "办证", "代开发票", "代做", "代写", "代考",
    # 人身攻击（仅收录无歧义的辱骂，避免误伤正常表达）
    "傻逼", "沙比", "脑残", "智障", "狗东西",
)

# 归一化后只保留：数字、小写字母、中日韩统一表意文字
_NON_CONTENT_RE = re.compile(r"[^0-9a-z一-鿿㐀-䶿]")


def normalize(text: str) -> str:
    """归一化：是全角转半角 → 转小写 → 剔除所有非内容字符。

    剔除空格/标点后，「加 微 信」「加-微-信」与「加微信」归一到同一串，
    拆分绕过失效。
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    return _NON_CONTENT_RE.sub("", text)


def _build_matcher() -> tuple[re.Pattern[str], dict[str, str]]:
    """构建匹配器：归一化词 → 原始词的映射 + 合并正则（长词优先）。"""
    words = list(DEFAULT_WORDS)
    extra = (settings.MODERATION_EXTRA_WORDS or "").replace("\n", ",")
    words.extend(w.strip() for w in extra.split(",") if w.strip())

    mapping: dict[str, str] = {}
    for word in words:
        key = normalize(word)
        if key:
            mapping.setdefault(key, word)
    if not mapping:
        # 空词库不能让正则退化为匹配空串（会命中一切）
        return re.compile(r"(?!)"), mapping
    pattern = re.compile("|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)))
    return pattern, mapping


_pattern: re.Pattern[str] | None = None
_mapping: dict[str, str] = {}


def _matcher() -> tuple[re.Pattern[str], dict[str, str]]:
    global _pattern, _mapping
    if _pattern is None:
        _pattern, _mapping = _build_matcher()
    return _pattern, _mapping


def reload_words() -> None:
    """重新加载词库（运营改了配置后调用，或单测隔离用）"""
    global _pattern, _mapping
    _pattern, _mapping = _build_matcher()


def scan(text: str) -> list[str]:
    """扫描文本，返回命中的**原始**敏感词列表（去重、保序）。未命中返回空列表。"""
    if not text:
        return []
    pattern, mapping = _matcher()
    normalized = normalize(text)
    hits: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(normalized):
        word = mapping.get(match.group(0))
        if word and word not in seen:
            seen.add(word)
            hits.append(word)
    return hits


def mask_text(text: str, hits: list[str] | None = None) -> str:
    """把命中的词替换为等长星号（用于 action=mask 的软处置）。"""
    hits = hits if hits is not None else scan(text)
    if not hits:
        return text
    masked = text
    for word in hits:
        # 逐个替换原词（不做归一化替换，避免误伤正常文本中的同音字符）
        masked = masked.replace(word, "*" * len(word))
    return masked


class ScreenResult:
    """文本筛查结果"""

    def __init__(self, passed: bool, hits: list[str], text: str, action: str = "reject"):
        self.passed = passed
        self.hits = hits
        self.text = text
        self.action = action

    def __bool__(self) -> bool:
        return self.passed


def screen_text(text: str, *, action: str | None = None) -> ScreenResult:
    """筛查文本并按处置动作处理。

    - 未开启审核 / 未命中 → ``passed=True``，text 原样返回
    - 命中 + ``reject`` → ``passed=False``（调用方应拒绝提交）
    - 命中 + ``mask``   → ``passed=True``，text 已打码
    - 命中 + ``review`` → ``passed=True``，text 原样（由人工复核兜底）

    异常不抛出：审核是「增强」，不能因为词库配置错误就让整个提交链路 500。
    """
    if not text:
        return ScreenResult(True, [], text, action or settings.MODERATION_ACTION)
    try:
        if not settings.MODERATION_ENABLED:
            return ScreenResult(True, [], text, "disabled")
        hits = scan(text)
        if not hits:
            return ScreenResult(True, [], text, "pass")
        act = action or settings.MODERATION_ACTION
        if act not in MODERATION_ACTIONS:
            act = "reject"
        if act == "mask":
            return ScreenResult(True, hits, mask_text(text, hits), act)
        if act == "review":
            return ScreenResult(True, hits, text, act)
        return ScreenResult(False, hits, text, act)
    except Exception as exc:
        # 不静默：词库正则构造失败、配置异常等**真实 bug** 会被这里吞掉，
        # 使审核形同虚设且无任何信号（第二轮增量审查 P2）。
        # 仍按 fail-open 放行（审核是增强能力，不能反过来阻断用户提交），
        # 但必须留下可告警的日志。
        logger.exception(f"敏感词筛查异常，已降级放行（fail-open）: {exc}")
        return ScreenResult(True, [], text)


# 命中留痕

# 「留痕已达今日上界」这条 warning 的进程内去重标记：{(user_id, content_type, date)}。
# 上限 = 用户数 × 内容类型数 × 当日，量级远小于被它拦住的留痕行；跨日由 date 自然失效。
_LOG_CAP_WARNED: set[tuple[int, str, object]] = set()


def _excerpt(text: str | None) -> str | None:
    if not text:
        return None
    # 先剥标签再截断，避免把 HTML 片段带进摘要
    plain = sanitize_plain_text(text) or ""
    plain = plain.strip()
    if len(plain) > EXCERPT_MAX_LENGTH:
        return plain[:EXCERPT_MAX_LENGTH] + "…"
    return plain or None


async def log_hit(
    db: AsyncSession,
    *,
    content_type: str,
    user_id: int | None,
    text: str | None,
    hits: list[str],
    action: str,
    target_id: str | None = None,
) -> ContentModerationLog | None:
    """记录一次敏感词命中。

    仅 add 不 commit：由调用方事务统一提交，保证「业务写入被拒」时
    留痕也随之回滚（reject 场景下留痕仍会提交，因为它在拒绝分支单独提交）。

    ⚠️ 每个用户每类内容有**当日留痕条数上界**（第五轮审查 R5-11）。
    留痕按设计在「业务被拒」时也要落库，而业务侧的每日配额数的是**业务行数**，
    被拒的提交一行都不产生 —— 于是「固定一条命中基线词库的正文循环提交」
    得到的是「评论恒 0 行、配额恒不计数、留痕每请求 +1」，
    按登录用户 300 次/分钟约可日增 40 万行，正好淹掉本节第 4 条设计原则
    （「未命中不写日志，以免淹没有效信号」）要保护的东西：审核台的信噪比。
    超界后**只跳过留痕，不改处置结果**（照样 reject），并恰好打一条 warning
    （只在第一次越界时打，避免留痕洪峰换成日志洪峰）。
    """
    if user_id is not None:
        # naive UTC 自然日起点：与 comment_service/feedback_service 的配额同一口径
        # （MySQL DATETIME 不带时区，全库按 UTC 写入/解读）
        today_start = dt.datetime.now(dt.timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=None
        )
        rows = (
            await db.execute(
                select(func.count(ContentModerationLog.id)).where(
                    ContentModerationLog.user_id == user_id,
                    ContentModerationLog.content_type == content_type,
                    ContentModerationLog.created_at >= today_start,
                )
            )
        ).scalar() or 0
        if rows >= MAX_MODERATION_LOGS_PER_USER_PER_DAY:
            if rows == MAX_MODERATION_LOGS_PER_USER_PER_DAY and (user_id, content_type, today_start.date()) not in _LOG_CAP_WARNED:
                # 只在**第一次**越界时喊一声。留痕行数被拦住了、不会再涨，
                # 所以 `rows == 上界` 会一直成立 —— 不加这个去重就是
                # 把「数据库洪峰」换成「日志洪峰」（第五轮审查对日志量的同一口径）。
                _LOG_CAP_WARNED.add((user_id, content_type, today_start.date()))
                logger.warning(
                    f"用户 {user_id} 的 {content_type} 命中留痕今日已达上界"
                    f"（{MAX_MODERATION_LOGS_PER_USER_PER_DAY} 条），后续命中只拒绝、不再写留痕"
                )
            return None

    log = ContentModerationLog(
        content_type=content_type,
        target_id=(str(target_id) if target_id is not None else None),
        user_id=user_id,
        excerpt=_excerpt(text),
        hit_words=",".join(hits)[:500],
        action=action,
        # reject 属于机器已终态处置；mask/review 需要人工再看一眼
        status="rejected" if action == "reject" else "pending",
    )
    db.add(log)
    return log


async def list_logs(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    content_type: str | None = None,
) -> dict:
    """管理端：命中留痕列表（状态/类型筛选，时间倒序）"""
    conditions = []
    if status:
        if status not in MODERATION_STATUSES:
            raise AppException(code=400, message="无效的审核状态")
        conditions.append(ContentModerationLog.status == status)
    if content_type:
        conditions.append(ContentModerationLog.content_type == content_type)

    total = (
        await db.execute(
            select(func.count(ContentModerationLog.id)).where(*conditions)
        )
    ).scalar()
    rows = (
        await db.execute(
            select(ContentModerationLog)
            .where(*conditions)
            .order_by(ContentModerationLog.created_at.desc(), ContentModerationLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return {
        "items": [_log_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _log_to_dict(log: ContentModerationLog) -> dict:
    return {
        "id": log.id,
        "content_type": log.content_type,
        # 中文名由后端给：管理端另维护一份映射会在新增取值时静默显示成裸代码
        "content_type_label": CONTENT_TYPE_LABELS.get(log.content_type, log.content_type),
        "target_id": log.target_id,
        "user_id": log.user_id,
        "excerpt": log.excerpt,
        "hit_words": (log.hit_words or "").split(",") if log.hit_words else [],
        "action": log.action,
        "action_label": MODERATION_ACTION_LABELS.get(log.action, log.action),
        "status": log.status,
        "status_label": MODERATION_STATUS_LABELS.get(log.status, log.status),
        "reviewer_id": log.reviewer_id,
        "reviewed_at": log.reviewed_at.isoformat() if log.reviewed_at else None,
        "review_note": log.review_note,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


async def review_log(
    db: AsyncSession, log_id: int, reviewer_id: int, status: str, note: str | None = None
) -> dict:
    """人工复核：把机器判定改为 approved（误报放行）/ rejected（维持拦截）"""
    if status not in MODERATION_STATUSES:
        raise AppException(code=400, message="无效的审核状态")
    log = (
        await db.execute(select(ContentModerationLog).where(ContentModerationLog.id == log_id))
    ).scalar_one_or_none()
    if not log:
        raise AppException(code=404, message="审核记录不存在")
    log.status = status
    log.reviewer_id = reviewer_id
    # 统一 UTC：created_at 由 func.now() 在 UTC 会话下写入，
    # 若这里用本地时间会让「命中时间」与「复核时间」差一个时区（国内 8 小时），
    # 运营统计「平均复核时长」会出现固定偏移（第二轮增量审查 P1）
    log.reviewed_at = utcnow()
    log.review_note = (note or None) or None
    await db.commit()
    return _log_to_dict(log)


async def count_pending(db: AsyncSession) -> int:
    """待复核数量（管理端角标用）"""
    return (
        await db.execute(
            select(func.count(ContentModerationLog.id)).where(
                ContentModerationLog.status == "pending"
            )
        )
    ).scalar() or 0
