"""中文全文检索（MySQL 8 ngram）与 LIKE 回退的统一入口：全文优先 + LIKE 兜底，保证召回不劣于改造前。

必须保留 LIKE 回退（实测）：少于 2 字符的词无 token、部分短词被停用词丢弃、「C++」等算子字符使查询变形、
过长查询串被 MySQL errno 191 直接拒绝（500）——全文只能当主路径不能当唯一路径。
运行时降级：结构性失败（缺索引/不支持 parser/查询被拒）→ 熔断本进程直接走 LIKE；
疑似瞬时故障 → 仅本次回退；LIKE 候选出错一律原样上抛（吞掉会把 DB 故障伪装成「列表为空」）。
⚠️ 纯空格等「无可用词元」输入仍返回 LIKE 条件：返回空列表会生成 AND NULL 静默 0 行。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence

from loguru import logger
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import DBAPIError

from app.config import settings
from app.core.content_types import CONTENT_TYPES
from app.core.exceptions import AppException
from app.models.node import Node, Plugin
from app.models.resource import Resource
from app.models.tool import Tool
from app.models.tutorial import Tutorial
from app.utils.sanitize import escape_like

# 模型类 → 内容模块键（用于搜索词日志归属；由内容类型注册表派生，不手写第二份清单）
_MODEL_TO_MODULE: dict = {
    spec.model: module for module, spec in CONTENT_TYPES.items() if spec.model is not None
}


def module_for_model(model) -> str:
    """取模型对应的模块键；未登记返回空串（日志里 module 为空串而不是瞎猜）"""
    return _MODEL_TO_MODULE.get(model, "")


# 全文检索覆盖的列，**顺序必须与迁移中的 FULLTEXT 索引一致**：
# MySQL 要求 MATCH() 列列表与某个 FULLTEXT 索引完全一致，否则报 errno 1191。
TUTORIAL_SEARCH_COLS = (Tutorial.title, Tutorial.description, Tutorial.content)
RESOURCE_SEARCH_COLS = (Resource.name, Resource.description, Resource.content)
# 知识百科 / 扩展（台账 P1-17）：这两个模块此前**完全没进站内搜索**，
# 而它们是 SEO 方案指定的栏目词承载页（「ComfyUI 节点」「ComfyUI 插件下载」）。
# 注意：站内栏目名已统一为「扩展」，但 **SEO 关键词仍是「插件」**——
# 那是用户真实搜索词（《SEO 与 GEO 方案》10.5 的关键词矩阵），不是栏目名，不要跟着改。
NODE_SEARCH_COLS = (Node.name, Node.description, Node.content)
PLUGIN_SEARCH_COLS = (Plugin.name, Plugin.description, Plugin.content)
# 工具（第四轮审查 P2-30）：`tools` 表没有正文列，也没有 FULLTEXT 索引
# （迁移 0028 只给 tutorials / resources 建），所以这里两列 —— 走 `apply_keyword_filter`
# 时全文候选会命中 errno 1191 并自动降级 LIKE，行为与两侧原先手写的 LIKE 完全一致，
# 差别只是「加一列检索目标」从此只改一处。
TOOL_SEARCH_COLS = (Tool.name, Tool.description)

# 允许进入全文查询的字符集：中英日韩数字，其余（含布尔算子 +-<>()"~* 与标点）一律作分隔符。
# 用白名单而不是黑名单：只要漏掉一个算子字符，用户搜「C++」就可能被 MySQL 语法拒绝。
_TOKEN_RE = re.compile(
    r"[0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+"
)

# 单个关键词最多保留多少个词元（防止长串触发 errno 191）
_MAX_TOKENS = 8

# 全文检索可用性熔断。**粒度是「列组」而不是「进程」**（第二轮复审 C-01）。
#
# 为什么必须按列组：MATCH() 的可用性取决于「这一组列有没有对应的 FULLTEXT 索引」，
# 是**每个表各自的事**。早先只有一个进程级布尔值，于是「某张表没有全文索引」会被
# 误判成「这台 MySQL 不支持 ngram」，把**其它表的全文检索一起永久关掉**。
# 真实事故路径：`tools` 没有 FULLTEXT（0028/0032 只建了 4 张表）→ 搜一次工具必报
# errno 1191 → 熔断 → 教程/资源/百科/扩展全部退回 `LIKE '%kw%'` 全表扫描，直到重启。
#
# 熔断是单向的（进程内不自动恢复）：结构性失败不会自愈，而每次请求多打一条
# 必然失败的查询只会白白放大故障。
_FULLTEXT_BROKEN_GROUPS: set[tuple[str, ...]] = set()
# 无列上下文的全局熔断（兼容旧调用 / 明确「整个实例不支持 ngram」的场景）
_FULLTEXT_DISABLED_ALL = False

# 判定「结构性失败」用的错误特征。命中 → 熔断；未命中 → 视作瞬时故障，只本次回退。
# 之所以不笼统地把所有 DBAPIError 都熔断：连接类故障（server gone away 等）
# 是能自愈的，一刀切熔断会让一次网络抖动永久降级检索质量，直到重启才恢复。
_FULLTEXT_ERROR_MARKERS = (
    "fulltext",
    "ngram",
    "ft query",
    "can't find fulltext",
    "too many words",
    "errno 191",
    "(1191",
    "syntax",
)


def _group_key(columns: Sequence) -> tuple[str, ...]:
    """列组标识：用真实列名元组，避免依赖 ORM 对象身份。

    顺序也进 key —— MATCH() 对列顺序敏感，不同顺序是不同索引。
    """
    return tuple(_column_name(c) for c in columns)


def fulltext_available(columns: Sequence | None = None) -> bool:
    """当前是否应当尝试全文检索（总开关 + 全局熔断 + 该列组熔断同时成立）。

    :param columns: 要检索的列组。传 None 表示只关心「实例级」是否可用。
    """
    if not settings.SEARCH_FULLTEXT_ENABLED or _FULLTEXT_DISABLED_ALL:
        return False
    if columns is None:
        return True
    return _group_key(columns) not in _FULLTEXT_BROKEN_GROUPS


def mark_fulltext_unavailable(reason: str, columns: Sequence | None = None) -> None:
    """熔断：确认**这一组列**没有可用的全文索引后调用，只打一次日志避免刷屏。

    不传 ``columns`` 表示实例级熔断（所有列组都不再尝试全文）—— 只有确认
    「MySQL 不支持 ngram / parser 不可用」时才该这样调用。
    """
    global _FULLTEXT_DISABLED_ALL
    if columns is None:
        if not _FULLTEXT_DISABLED_ALL:
            logger.warning(
                "全文检索不可用，已熔断并退回 LIKE 检索（{}）。"
                "请确认迁移 20261008_0028 已执行且 MySQL 支持 ngram parser；"
                "检索功能不受影响，仅失去分词带来的额外召回。",
                reason,
            )
            _FULLTEXT_DISABLED_ALL = True
        return

    key = _group_key(columns)
    if key not in _FULLTEXT_BROKEN_GROUPS:
        _FULLTEXT_BROKEN_GROUPS.add(key)
        logger.warning(
            "列组 {} 的全文检索不可用，已对该列组熔断并退回 LIKE（{}）。"
            "只影响这一组列，其它内容类型的全文检索不受影响。"
            "请确认对应迁移里的 FULLTEXT 索引已创建（缺索引时 MySQL 报 errno 1191）。",
            "(" + ", ".join(key) + ")",
            reason,
        )


def reset_fulltext_availability() -> None:
    """把熔断状态恢复为「可用」——**仅供测试使用**，生产代码不要调用。"""
    global _FULLTEXT_DISABLED_ALL
    _FULLTEXT_DISABLED_ALL = False
    _FULLTEXT_BROKEN_GROUPS.clear()


def _looks_like_fulltext_error(exc: BaseException) -> bool:
    """判断数据库异常是否为「结构性失败」（缺索引 / 语法被拒），而非瞬时故障。"""
    msg = str(getattr(exc, "orig", exc) or exc).lower()
    return any(marker in msg for marker in _FULLTEXT_ERROR_MARKERS)


def _column_name(col) -> str:
    """取列在数据库中的真实列名（兼容 ORM 属性 ``Tutorial.title`` 与表列）。"""
    prop = getattr(col, "property", None)
    if prop is not None:
        return prop.columns[0].name
    return col.name


def build_boolean_query(keyword: str | None) -> str | None:
    """把用户输入转成 MySQL 布尔模式查询串；不可用（该走 LIKE 回退）时返回 None。

    返回形如 ``+教程 +安装`` 的串（AND 语义，与改造前 LIKE 的"必须都出现"最接近）。
    """
    if not keyword or not keyword.strip():
        return None
    keyword = keyword.strip()
    # 过长的关键词一律走 LIKE：不截断（截断等于换了搜索意图），直接放弃全文路径
    if len(keyword) > settings.SEARCH_MAX_KEYWORD_LEN:
        return None

    min_len = max(1, settings.SEARCH_NGRAM_TOKEN_SIZE)
    tokens = [t for t in _TOKEN_RE.findall(keyword) if len(t) >= min_len]
    if not tokens:
        return None

    tokens = tokens[:_MAX_TOKENS]
    return " ".join(f"+{t}" for t in tokens)


def _like_columns(columns: Sequence) -> tuple:
    """LIKE 兜底专用的列集合：**排除正文大字段**（第四轮全量审查 P1-60，方案 A）。

    `LIKE '%kw%'` 用不上任何索引，而本项目的正文列一律叫 `content`（MEDIUMTEXT，
    单行最大 16MB）：把它放进 OR 条件意味着**整张表每一行的正文都要被读出来扫一遍**，
    而 `pick_keyword_condition` 先用这个条件跑一次 `COUNT(*)`、`apply_keyword_filter`
    再用同一条件跑分页 —— 罕见关键词因此要付**两次全表扫**，全程占住一条池连接
    （单机 5+10=15 条/worker）。触发门槛极低：单个汉字、"C++" 这类纯符号、
    超过 `SEARCH_MAX_KEYWORD_LEN` 的串都会落到 LIKE。

    ⚠️ 语义变更（有意为之，配套测试见 `test_search_parity.py` 与 `test_search.py`）：
    **降级态（全文索引不可用）下不再能命中「只出现在正文里」的关键词**；
    名称与简介照旧命中，全文可用时正文照旧命中。
    P1-41 真正的不变量是「管理端与公开端结论一致」，那一条仍然成立 ——
    「搜不搜得到正文」是可调的产品口径，「两侧不一样」才是 bug。
    若某域的检索列全是正文列，则原样返回（宁可慢，也不给出空条件）。
    """
    picked = tuple(c for c in columns if getattr(c, "key", None) != "content")
    return picked or tuple(columns)


def build_like_condition(columns: Sequence, keyword: str) -> object:
    """改造前的 LIKE 条件（保留原语义：任一列包含关键词即可）。

    实际参与匹配的列见 :func:`_like_columns`。
    """
    kw = escape_like(keyword)
    return or_(*[col.contains(kw, escape="\\") for col in _like_columns(columns)])


def build_match_condition(columns: Sequence, boolean_query: str) -> object:
    """MATCH(col1, col2) AGAINST (:ft_query IN BOOLEAN MODE)。

    注意：MySQL 要求 MATCH() 的列列表必须与某个 FULLTEXT 索引的列列表**完全一致**
    （顺序也要一致），否则报 errno 1191。调用方必须用与索引相同的列与顺序。
    """
    col_sql = ", ".join(f"`{_column_name(c)}`" for c in columns)
    return text(f"MATCH({col_sql}) AGAINST (:ft_query IN BOOLEAN MODE)").bindparams(
        ft_query=boolean_query
    )


def keyword_condition_candidates(
    columns: Sequence, keyword: str | None
) -> list[tuple[str, object]]:
    """按优先级返回候选检索条件：**全文优先，LIKE 兜底**。

    返回 ``[(kind, cond), ...]``，``kind`` 取值为 ``"fulltext"`` / ``"like"``，
    供 :func:`pick_keyword_condition` 区分「失败可否降级」。

    - 有可用词元 → ``[("fulltext", ...), ("like", ...)]``；
    - 无可用词元（单字 / 纯符号 / 纯空格 / 超长 / 开关关闭 / 已熔断）
      → ``[("like", ...)]``，**至少保证一个候选**，绝不让调用方拿到 None 条件；
    - keyword 为空 / None → ``[]``（等价于不加检索条件）。
    """
    if not keyword:
        return []
    like_cond = build_like_condition(columns, keyword)
    # 按列组判断：某张表没有全文索引时，只有它退回 LIKE，不影响其它内容类型
    if not fulltext_available(columns):
        return [("like", like_cond)]
    boolean_query = build_boolean_query(keyword)
    if boolean_query is None:
        return [("like", like_cond)]
    return [("fulltext", build_match_condition(columns, boolean_query)), ("like", like_cond)]


async def pick_keyword_condition(
    count_fn: Callable[[object | None], Awaitable[int]],
    candidates: Sequence[tuple[str, object]],
    *,
    columns: Sequence | None = None,
) -> tuple[object | None, int]:
    """依次尝试候选条件，取第一个「有命中」的；全部无命中则退回最后一个（LIKE）。

    这样调用方拿到的 ``total`` 与 ``cond`` 一定是配套的，不会出现
    "用全文条件数出 0 条、却拿 LIKE 条件去翻页"这类自相矛盾的结果。

    全文候选被服务端拒绝时按 :func:`_looks_like_fulltext_error` 区分处理：
    结构性失败→**只熔断这个列组**（``columns``）后改用 LIKE；疑似瞬时故障→只本次回退。
    LIKE 候选的异常原样上抛（详见模块顶部说明）。

    :param columns: 本次检索的列组，用于把结构性失败精确归因到某张表。
        不传则按实例级熔断处理（旧行为，只有明确「整个实例不支持」时才该这样）。
    """
    last_cond: object | None = None
    last_total = 0
    for kind, cond in candidates:
        # 只有 LIKE 兜底需要语句超时：全文路径走 ngram 索引，本就毫秒级（P1-60 方案 B）
        timeout_ms = settings.SEARCH_FALLBACK_COUNT_TIMEOUT_MS if kind == "like" else None
        try:
            total = await count_fn(cond, timeout_ms=timeout_ms)
        except DBAPIError as exc:
            if kind != "fulltext":
                # 兜底计数被服务端中断 = 关键词过宽/数据量到了要重新设计检索的时候。
                # 这里给**明确的 400 与下一步动作**，既不静默返回 0 结果骗人，
                # 也不让它冒成一个 500（现象会是「搜索偶尔白屏」且无从下手）
                if _is_statement_timeout(exc):
                    logger.warning(
                        f"LIKE 兜底计数超时（{timeout_ms}ms），拒绝该宽泛关键词：{exc}"
                    )
                    raise AppException(
                        code=400,
                        message="搜索超时：关键词过于宽泛，请换用更具体的词组后重试",
                    ) from exc
                raise
            if _looks_like_fulltext_error(exc):
                mark_fulltext_unavailable(f"服务端拒绝全文查询：{exc}", columns)
            else:
                logger.warning(
                    "全文检索查询失败（疑似瞬时故障，本次回退 LIKE，不熔断）：{}", exc
                )
            continue
        if total:
            return cond, total
        last_cond, last_total = cond, total
    return last_cond, last_total


async def fetch_page(db, stmt, *, keyword: str | None, timeout_ms: int | None = None):
    """执行分页列表查询：带关键词时加语句超时，超时统一转 400。

    为什么列表查询也要限时：COUNT 已被 :func:`pick_keyword_condition` 限时（1s 中断），
    但同一条件交给分页查询时此前**没有任何上限** —— 宽泛关键词下它会在 MySQL 里
    一直扫到凑满 LIMIT，5 个模块并发搜索（搜索页一次打 5 个接口）时 2 核机器直接被拖死。
    语义与 count 侧保持一致：不静默返回空列表，而是明确告诉用户「换更具体的词」。
    """
    if keyword:
        stmt = stmt.prefix_with(
            f"/*+ MAX_EXECUTION_TIME({int(timeout_ms or settings.SEARCH_FALLBACK_COUNT_TIMEOUT_MS)}) */"
        )
    try:
        return (await db.execute(stmt)).scalars().all()
    except DBAPIError as exc:
        if keyword and _is_statement_timeout(exc):
            logger.warning(f"搜索列表查询超时，拒绝该宽泛关键词：{exc}")
            raise AppException(
                code=400,
                message="搜索超时：关键词过于宽泛，请换用更具体的词组后重试",
            ) from exc
        raise


# 语句超时（方案 B，第四轮全量审查 P1-60）
#
# MySQL 优化器 hint `MAX_EXECUTION_TIME(n)` 让服务端在 n 毫秒后**自己中断**这条
# 只读 SELECT（errno 3024）。为什么必须在服务端中断：应用侧 `asyncio.wait_for`
# 只能让我们不再等，**查询仍在 MySQL 里跑**，池连接照样被占住 —— 而那正是故障本体。
# 只加在 LIKE 兜底的 COUNT 上：全文路径走 ngram 索引（本就在毫秒级），
# 分页查询带 LIMIT 且扫的已是小字段（方案 A 已排除正文）。
_TIMEOUT_MARKERS = ("maximum statement execution time exceeded", "query execution was interrupted")


def _is_statement_timeout(exc: BaseException) -> bool:
    """判定「语句超时被服务端中断」（而不是别的 DBAPI 故障）。

    按错误码 + 文案双重兜底：不同 aiomysql/PyMySQL 版本对 3024 的包装不一致。
    """
    orig = getattr(exc, "orig", None)
    if orig is not None and getattr(orig, "code", None) == 3024:
        return True
    msg = str(orig if orig is not None else exc).lower()
    return any(marker in msg for marker in _TIMEOUT_MARKERS)


def make_count_fn(db, model, conditions: Sequence):
    """构造「带基础筛选条件」的 COUNT 函数，供 :func:`pick_keyword_condition` 使用。

    把 ``conditions`` 复制一份再追加 ``extra``：pick 过程会多次尝试候选条件，
    直接 append 到原列表会让失败候选的残余条件永久留在筛选里。

    :param timeout_ms: 每次调用的语句超时（毫秒），None = 不加 hint。
    """

    async def _count(extra: object | None = None, *, timeout_ms: int | None = None) -> int:
        conds = list(conditions) + ([extra] if extra is not None else [])
        stmt = select(func.count(model.id)).where(*conds)
        if timeout_ms:
            stmt = stmt.prefix_with(f"/*+ MAX_EXECUTION_TIME({int(timeout_ms)}) */")
        return (await db.execute(stmt)).scalar()

    return _count


async def apply_keyword_filter(
    db,
    model,
    conditions: list,
    columns: Sequence,
    keyword: str | None,
    *,
    log_search: bool = True,
) -> int:
    """把关键词条件（**全文优先 + LIKE 兜底**）追加到 ``conditions``，返回命中总数。

    管理端与公开端**必须调用同一个函数**（第三轮增量审查 P1-41）：
    此前管理端自写 ``Model.name.contains(...)``（只搜名称），而公开端是全文 +
    LIKE over name/description/content —— 同一句话在前台搜得到、在后台搜不到。
    两个文件几乎逐行同构，加字段时必然持续漂移；收敛到此处后只有一份行为。
    """
    count_fn = make_count_fn(db, model, conditions)
    if not keyword:
        return await count_fn()
    keyword_cond, total = await pick_keyword_condition(
        count_fn, keyword_condition_candidates(columns, keyword), columns=columns
    )
    # 站内搜索词日志（内容运营的选题来源）：只记**公开端**的搜索，
    # 管理端的检索是运营自己在找东西，混进来会把「用户想要什么」污染掉。
    # 落库失败绝不影响搜索本身（record_search 内部已用 SAVEPOINT + 吞异常）。
    if log_search:
        from app.services.search_log_service import record_search

        await record_search(db, module_for_model(model), keyword, total)
    # 防御：条件为 None 时不能进 where()，否则 SQLAlchemy 会生成 AND NULL → 静默 0 行
    if keyword_cond is not None:
        conditions.append(keyword_cond)
    return total
