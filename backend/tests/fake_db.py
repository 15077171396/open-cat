"""资金/权益链路测试用的内存会话仿真：CI 无数据库，用最小代价复刻行锁/原子 UPDATE/
唯一约束/savepoint 语义，让「并发不超发/不超扣/不重复兑换」成为可失败的断言。

- 权威行状态与 ORM 对象分离：UPDATE 不回写 ORM，仅 refresh() 同步（陈旧快照 bug 会被抓出来）；
- UPDATE 按表串行化（等价行锁）；唯一约束可抛 IntegrityError；begin_nested 为 savepoint；
- **写入后未加载的列**（`server_default=func.now()` 的时间列）在对象侧是 `_Unloaded` 哨兵：
  读它就抛 —— 与真实异步会话里的 `MissingGreenlet` 等价。只有 `refresh()` / 一次 SELECT
  取回该列才会填上真值（2026-10-29 两个 P1 就是漏了这一步：`POST /reports`、
  `POST /users/favorites` 在真实 MySQL 上 100% 500，而这里全绿）；
- **`load_only` 投影**同样生效：没被投影的列不会因为查询而变得可读；
- ⚠️ 只实现被测代码用到的 SQL 子集，未支持语句显式抛错——宁可红，不可假绿。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from contextlib import asynccontextmanager

from sqlalchemy import Column
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import Delete as _DeleteStmt
from sqlalchemy.sql import Insert as _InsertStmt
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, False_, True_


def _is_column(x) -> bool:
    """判定 SQLAlchemy 列元素（Column / ColumnClause，含 ORM 属性解包后的形态）"""
    return isinstance(x, Column) or (hasattr(x, "name") and hasattr(x, "table"))


def _load_only_columns(stmt) -> set[str] | None:
    """该 SELECT 真正取回的列名；返回 None 表示"取全部列"（没写 load_only）。

    SQLAlchemy 把 `load_only(a, b)` 编译成「a/b 不 loader-defer + 其余列 defer」，
    形如 `_AttributeStrategyLoad(path=...-> Model.a)` 加一个
    `_TokenStrategyLoad(path=...-> column:*)`。仿真层据此判断哪些列"查询后就该可读"。
    """
    for opt in getattr(stmt, "_with_options", ()) or ():
        projected: set[str] = set()
        defer_all = False
        for item in getattr(opt, "context", ()) or ():
            # 形如 `ORM Path[Mapper[Tutorial(tutorials)] -> Tutorial.title]`，两端的
            # "ORM Path[" 与 "]" 必须先剥掉，否则 endswith("column:*") 永远不成立
            raw = str(getattr(item, "path", "") or "")
            raw = raw[len("ORM Path[") :] if raw.startswith("ORM Path[") else raw
            raw = raw.rstrip("]")
            if not raw:
                continue
            if raw.endswith("column:*"):
                defer_all = True
            elif "-> " in raw:
                attr = raw.rsplit("-> ", 1)[1].strip()
                projected.add(attr.split(".", 1)[1] if "." in attr else attr)
        if defer_all:
            return projected
    return None


def _hydrate(obj, row: dict, fetched: set[str] | None) -> None:
    """按本次查询真正取回的列，重设对象的加载状态。

    - 取回的列：未加载的从行里填上；
    - **没取回的列：直接标记为未加载** —— 这正是 `load_only` 的语义。
      只"填未加载"而不"降级已加载"会让投影校验失效（seed 出来的对象列本来就是全的，
      `load_only` 漏投影也照样读得到，2026-10-29 的收藏 500 就是这么漏掉的）。
    """
    if fetched is None:
        for col in obj.__table__.columns:
            current = getattr(obj, col.name, None)
            if isinstance(current, _Unloaded) and col.name in row:
                setattr(obj, col.name, row[col.name])
        return
    for col in obj.__table__.columns:
        if col.name in fetched:
            if isinstance(getattr(obj, col.name, None), _Unloaded) and col.name in row:
                setattr(obj, col.name, row[col.name])
        else:
            setattr(obj, col.name, _Unloaded(col.name))


class _Unsupported(Exception):
    """仿真层遇到未实现的 SQL 构造时抛出（宁可红，不可假绿）"""


class _Unloaded:
    """「这一列在对象上还没加载」的哨兵。

    真实异步会话里，`server_default` 列 INSERT 之后对象侧是**未加载**状态：
    读它会触发一次同步 SQL，在 greenlet 之外就是
    `MissingGreenlet: greenlet_spawn has not been called`。
    仿真层没法阻止属性读取，就让读到的值自己炸——任何用法（`.isoformat()`、
    `if x`、`x is None`、字符串拼接）都会先撞到这里。
    """

    __slots__ = ("column",)

    def __init__(self, column: str) -> None:
        object.__setattr__(self, "column", column)

    def _fail(self):
        raise RuntimeError(
            f"读取了未加载的列 '{self.column}'：真实异步会话在这里会抛 MissingGreenlet。"
            f"写入后必须 await db.refresh(obj)（或让本次 SELECT 真正取回该列）再序列化。"
        )

    def __getattr__(self, item):
        self._fail()

    def __bool__(self):
        self._fail()

    def __eq__(self, other):
        self._fail()

    def __hash__(self):
        return id(self)

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<Unloaded {self.column}>"


def _utcnow_naive() -> dt.datetime:
    """与真实库对齐：会话时区固定 +00:00，`func.now()` 落的是 UTC naive 时间"""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _has_time_server_default(col) -> bool:
    """该列是否由数据库在 INSERT 时生成时间（`server_default=func.now()`）。

    字符串字面量（`server_default="0"`）不在此列：那类值 `_apply_defaults` 已当成
    普通默认值写在对象上，本来就不需要回读。
    """
    server_default = getattr(col, "server_default", None)
    if server_default is None:
        return False
    if isinstance(getattr(server_default, "arg", None), str):
        return False
    try:
        return col.type.python_type is dt.datetime
    except (NotImplementedError, AttributeError):  # 自定义类型
        return False


def _literal(x):
    """取出 BindParameter 的字面值"""
    if hasattr(x, "value"):
        return x.value
    return x


def _is_null(x) -> bool:
    """判定 SQL 的 NULL 字面量（``col.is_(None)`` 会包装成 Null 对象）"""
    return type(x).__name__ == "Null"


def _contains_match(value, pattern: str, escape: str | None) -> bool:
    """复刻 SQL ``col LIKE '%' || pattern || '%' [ESCAPE esc]`` 的包含语义。

    各管理列表的「关键词搜索」统一走 ``Column.contains(kw, escape="\\\\")``，
    而 ``escape_like()`` 已把用户输入里的 ``%`` / ``_`` 转义成 ``\\%`` / ``\\_``，
    所以这里必须按 ESCAPE 规则还原：转义字符后的字符按字面量匹配，
    未转义的 ``%`` → 任意串、``_`` → 任意单字符。
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if escape and ch == escape and i + 1 < n:
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if ch == "%":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        else:
            out.append(re.escape(ch))
        i += 1
    # `re.I` 不是可选项：MySQL 侧是 `utf8mb4_unicode_ci`（compose 与 CI 建库都用它），
    # 大小写不敏感。原先仿真层只看大小写，于是「仿真 + 真库都对 MySQL 大小写敏感」这一
    # 错误一致被 `test_list_parity` 那类跨侧比对守卫**掩盖成一致通过**（第五轮 P2-57 之一）。
    return re.search("".join(out), value or "", re.S | re.I) is not None


def _eval(expr, row: dict):
    """在某一行的上下文里求值 SQL 表达式。

    支持：列引用、字面量、比较/算术/逻辑运算符、and_/or_ 组合、IS (NOT) NULL、
    IS TRUE / IS FALSE、coalesce、contains/LIKE。
    """
    # SQL 侧的布尔常量（`col.is_(True)` 的右值是 True_() 而不是 Python True）。
    # 不先解包的话 `left is right` 永远为 False —— 这正是 `is_published.is_(True)`
    # 在仿真层恒不命中、统计类断言全空的原因。
    if isinstance(expr, True_):
        return True
    if isinstance(expr, False_):
        return False
    if _is_column(expr):
        return row.get(expr.name)
    if isinstance(expr, BooleanClauseList):
        # and_ / or_
        parts = [_eval(c, row) for c in expr.clauses]
        if expr.operator.__name__ == "and_":
            return all(parts)
        if expr.operator.__name__ == "or_":
            return any(parts)
        raise _Unsupported(f"unsupported boolean operator: {expr.operator}")
    if hasattr(expr, "operator") and hasattr(expr, "left") and hasattr(expr, "right"):
        left = _eval(expr.left, row)
        right = _eval(expr.right, row)
        # `is_()` / `isnot()` 有两种语义，必须在覆盖 right 之前区分：
        #   col.is_(None)  → IS NULL
        #   col.is_(True)  → 布尔比较（发布态过滤大量使用，早期实现把它当成 IS NULL，
        #                    于是 `is_published.is_(True)` 恒为 False，统计类断言全空）
        right_is_null = _is_null(expr.right)
        left_is_null = _is_null(expr.left)
        if right_is_null:
            right = None
        elif left_is_null:
            left = None
        op = expr.operator
        name = getattr(op, "__name__", "")
        # SQLAlchemy 用 operator 模块的函数，也用 and_/or_ 这类特殊形式
        if name in ("and_", "or_"):
            return right(left, right) if name == "or_" else (left and right)
        # IS NULL / IS NOT NULL / IS TRUE / IS FALSE
        # ⚠️ `is_not` 的 operator 名在不同 SQLAlchemy 版本下是 `isnot` 或 `is_not`，
        # 两个都要认：只认一个会让 `col.is_not(None)` 掉进下面的二元运算分支，
        # 报 `'datetime' object has no attribute 'is_not'` —— 崩在替身里，而不是断言失败。
        if name == "is_":
            return (left is None) if right_is_null else (left is right)
        if name in ("isnot", "is_not"):
            return (left is not None) if right_is_null else (left is not right)
        # IN / NOT IN：SQLAlchemy 的 in_op 期望左值是列对象，这里左值已是标量
        if name == "in_op":
            return left in (right or [])
        if name == "notin_op":
            return left not in (right or [])
        # contains / LIKE：左值是列值（已被 _is_column 解包成标量），不能交给二元运算符
        # （``contains_op(str, str)`` 会去调 str.contains → AttributeError）
        if name == "contains_op":
            escape = (getattr(expr, "modifiers", None) or {}).get("escape")
            return _contains_match(left, right, escape)
        try:
            return op(left, right)
        except TypeError as exc:  # pragma: no cover - 防御
            raise _Unsupported(f"unsupported operator {name}: {exc}") from exc
    if hasattr(expr, "clauses"):
        # 函数表达式（当前仅需 coalesce / count）
        fname = getattr(expr, "name", "")
        if fname == "coalesce":
            for clause in expr.clauses:
                value = _eval(clause, row)
                if value is not None:
                    return value
            return None
        raise _Unsupported(f"unsupported function: {fname}")
    return _literal(expr)


class _Table:
    """一张内存表：权威行状态 + ORM 对象（二者可短暂不一致，见模块头说明）"""

    def __init__(self):
        self.rows: dict[int, dict] = {}          # id -> 权威列值（数据库真实值）
        self.objs: dict[int, object] = {}        # id -> ORM 对象
        self.committed: dict[int, dict] = {}     # id -> 上次同步时 ORM 已确认的值

    def insert(self, obj) -> None:
        """INSERT：全列写入权威行，并确立 committed 基线。

        ⚠️ 时间型 `server_default` 列（`func.now()`）走「库生成、对象侧未加载」语义：
        权威行拿到 UTC 时间，ORM 属性换成 `_Unloaded` 哨兵。要读它就得 `refresh()`
        —— 真实会话就是这样，漏了就是 `MissingGreenlet`。
        """
        row = self.rows.setdefault(obj.id, {})
        for col in obj.__table__.columns:
            value = getattr(obj, col.name, None)
            if isinstance(value, _Unloaded):
                value = row.get(col.name)
            if value is None and _has_time_server_default(col):
                row[col.name] = _utcnow_naive()
                setattr(obj, col.name, _Unloaded(col.name))
            else:
                row[col.name] = value
        self.objs[obj.id] = obj
        self.committed[obj.id] = dict(row)

    def dirty_columns(self, obj) -> dict:
        """`{列名: 权威行里的旧值}` —— 只含真正与基线不匹配的列。

        与 `flush_dirty` 共用同一个判定，否则「会话以为会写回的列」和
        「实际写回的列」会分叉，事务快照就会漏记或少记。
        `_Unloaded` 的列一律跳过：它没有可写回的值，真实会话也不会把它当脏列。
        """
        baseline = self.committed.get(obj.id)
        row = self.rows.get(obj.id)
        if baseline is None or row is None:
            return {}
        changed = {}
        for col in obj.__table__.columns:
            current = getattr(obj, col.name, None)
            if isinstance(current, _Unloaded):
                continue
            if current != baseline.get(col.name):
                changed[col.name] = row.get(col.name)
        return changed

    def flush_dirty(self, obj) -> None:
        """UPDATE：只把**脏列**写回权威行，与 SQLAlchemy 的 dirty 属性追踪一致。

        这一点很关键：若把所有列都写回，那么「UPDATE 后未 refresh 的陈旧 ORM 对象」
        会在 flush 时把旧值覆盖回数据库，恰好掩盖掉被测代码漏 refresh 的 bug。
        """
        row = self.rows.get(obj.id)
        if row is None:
            return
        changed = self.dirty_columns(obj)
        if not changed:
            return
        for name, _old in changed.items():
            row[name] = getattr(obj, name, None)
        self.committed[obj.id] = dict(row)

    def sync_to_obj(self, obj) -> None:
        """把权威行拷回 ORM 对象（模拟 refresh / SELECT 命中 identity map）。

        这是唯一能把 `_Unloaded` 哨兵替换成真值的地方 —— 与真实 `await db.refresh()`
        一一对应。刻意**不管**对象侧的脏值：refresh 就是"以库为准"。
        """
        row = self.rows.get(obj.id)
        if row is None:
            return
        for col in obj.__table__.columns:
            if col.name in row:
                setattr(obj, col.name, row[col.name])
        self.committed[obj.id] = dict(row)


class _Result:
    def __init__(self, tuples, kind: str):
        self._tuples = tuples
        self._kind = kind

    def first(self):
        return self._tuples[0] if self._tuples else None

    def scalar_one_or_none(self):
        return self._tuples[0][0] if self._tuples else None

    def scalar(self):
        return self._tuples[0][0] if self._tuples else None

    def all(self):
        return self._tuples

    def scalars(self):
        return _ScalarResult([t[0] for t in self._tuples])


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values

    def first(self):
        return self._values[0] if self._values else None


class _ExecResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


def _order_directives(stmt) -> list:
    """把 ORDER BY 解析成 `[(取值规格, 是否 DESC, 种类)]`（第四轮整改 P2-51）。

    仿真层原先**完全不读** ORDER BY / LIMIT / OFFSET，只按插入顺序返回全部匹配行 ——
    于是「靠 SQL 侧排序与截断保证正确性」的代码在离线门禁里从未被真正验证过，
    测试绿只是因为 Python 侧还留着一道兜底。这里补上，并刻意在遇到不支持的形态时
    抛错而不是忽略（同本模块「宁可红，不可假绿」的原则）。

    支持两种排序键：
    - 列（含 `col.desc()`、`col.desc().nullslast()` 这类包装）；
    - 布尔表达式（`(status == 'pending').desc()` —— 审核队列「待处理排前面」就是这种）。
    """
    out = []
    for clause in list(getattr(stmt, "_order_by_clauses", ()) or []):
        # UnaryExpression 对 asc/desc 不暴露可判别的 operator，用渲染后的 SQL 串判向最稳
        desc = " DESC" in str(clause).upper()
        expr = clause
        # 逐层脱掉 UnaryExpression 包装（asc/desc、NULLS FIRST/LAST）直到拿到列或布尔式
        for _ in range(4):
            if _is_column(expr) or isinstance(expr, (BooleanClauseList, BinaryExpression)):
                break
            expr = getattr(expr, "element", None)
            if expr is None:
                break
        if _is_column(expr):
            out.append((expr.name, desc, "col"))
        elif isinstance(expr, (BooleanClauseList, BinaryExpression)):
            out.append((expr, desc, "expr"))
        else:
            raise _Unsupported(
                f"unsupported ORDER BY expression: {type(clause).__name__}"
                "（标签引用 / 函数表达式不在仿真子集内，请显式处理而不是悄悄忽略）"
            )
    return out


def _order_value(spec, kind: str, row: dict):
    if kind == "col":
        return row.get(spec)
    return bool(_eval(spec, row))


def _sort_key(spec, kind: str, row: dict):
    """排序键：NULL 用「(False, 0)」表示，保证 ASC 时排最前、DESC（reverse）时排最后。

    第二元素在 NULL 情况下恒为 0，是为了让**两个 NULL 互比**时不落到
    ``None < None``（datetime 与 None 比较会 TypeError）。
    """
    value = _order_value(spec, kind, row)
    if value is None:
        return (False, 0)
    return (True, value)


def _sort_rows(items: list, directives: list) -> list:
    """按 MySQL 语义排序：ASC 时 NULL 在前、DESC 时 NULL 在后；多列从右往左稳定排序。"""
    for spec, desc, kind in reversed(directives):
        items.sort(key=lambda item, s=spec, k=kind: _sort_key(s, k, item[1]), reverse=desc)
    return items


def _slice_rows(items: list, stmt) -> list:
    """应用 OFFSET / LIMIT（值必须是字面整数，否则显式抛错）。"""
    for attr, cast in (("_offset", lambda n: n), ("_limit", lambda n: n)):
        value = getattr(stmt, attr, None)
        if value is None:
            continue
        if not isinstance(value, int):
            raise _Unsupported(f"{attr[1:]} 不是字面整数（{type(value).__name__}），仿真层不猜语义")
    offset = getattr(stmt, "_offset", None) or 0
    limit = getattr(stmt, "_limit", None)
    items = items[int(offset):]
    if limit is not None:
        items = items[: int(limit)]
    return items


class MemorySession:
    """最小 AsyncSession 仿真，覆盖资金链路实际用到的语句子集。

    与真实会话的两处刻意差异（都是为了让 bug 更容易暴露）：
    - ``execute`` 前后各让出一次事件循环，制造并发交错；UPDATE 在锁内完成，
      因此交错只发生在语句之间，不会插进「判定 + 写入」中间。
    - ``commit()`` 不真正丢弃状态，测试才能在结束后断言最终余额。
    """

    def __init__(self, storage: dict | None = None):
        # storage 可跨会话共享，模拟「多个请求会话 + 同一个数据库」
        self._tables: dict[str, _Table] = storage if storage is not None else {}
        self._pending: list = []
        # 事务快照：(表名, id) -> (row|None, committed|None, obj|None)
        # 只记每行**本事务第一次改动前**的状态，rollback 时按它整行还原。
        self._undo: dict[tuple[str, int], tuple] = {}
        self.commits = 0
        self.flushes = 0

    def new_session(self) -> "MemorySession":
        """新建一个共享同一库、但事务（pending）独立的会话。

        并发测试用它模拟多个并发请求：会话各自独立，数据库只有一份。
        """
        return MemorySession(storage=self._tables)

    # ---------- 事务快照（P1-42 之一：rollback 必须真回滚） ----------

    def _snapshot(self, table_name: str, row_id: int) -> None:
        key = (table_name, row_id)
        if key in self._undo:
            return  # 保留本事务开始时的状态
        table = self._tables.get(table_name)
        row = table.rows.get(row_id) if table else None
        committed = table.committed.get(row_id) if table else None
        obj = table.objs.get(row_id) if table else None
        self._undo[key] = (
            dict(row) if row is not None else None,
            dict(committed) if committed is not None else None,
            obj,
        )

    # ---------- 内部工具 ----------
    def _table(self, obj_or_name) -> _Table:
        name = obj_or_name if isinstance(obj_or_name, str) else obj_or_name.__table__.name
        return self._tables.setdefault(name, _Table())

    def _apply_defaults(self, obj) -> None:
        """应用 column default / server_default（真实库由 INSERT 时补全）"""
        for col in obj.__table__.columns:
            current = getattr(obj, col.name, None)
            if current is not None:
                continue
            if col.default is not None and hasattr(col.default, "arg"):
                arg = col.default.arg
                # 只应用标量默认值（role='user' / points=0 等）。
                # callable default（generate_id）交给下方 id 兜底：测试里用连续
                # id 比随机 10 位 id 更容易定位问题。
                if not callable(arg):
                    setattr(obj, col.name, arg)
            elif col.server_default is not None:
                raw = getattr(col.server_default, "arg", None)
                if isinstance(raw, str):
                    # server_default 的 arg 是字符串字面量（'0' / 'active'）
                    text = raw.text if hasattr(raw, "text") else raw
                    text = text.strip().strip("'\"")
                    py_type = col.type.python_type
                    try:
                        setattr(obj, col.name, py_type(text))
                    except (TypeError, ValueError):
                        setattr(obj, col.name, text)
        if getattr(obj, "id", None) is None:
            obj.id = self._alloc_id(self._table(obj))
        # 时间型 server_default 列**不**在这里填：真实库里它由 INSERT 生成，
        # 对象侧读完之前一直是"未加载"（见 _Table.insert）。曾经在这里塞
        # `dt.datetime.now()`，正是这一步让「漏 refresh」的 500 在仿真层完全隐形。

    @staticmethod
    def _alloc_id(table: "_Table") -> int:
        """分配主键：使用挂在表上的单调计数器，而非 ``max(id)+1``。

        ``max(rows)+1`` 在并发会话下会分配出**重复 id**——两个会话都还没 flush，
        看到的 max 相同。而唯一约束检查用 ``other_id == obj.id`` 跳过自身，
        重复 id 会让两条本应冲突的行被当成同一行，唯一约束兜底路径就此失效
        （假绿）。真实数据库的主键由引擎分配，天然不重复，这里必须对齐。
        """
        base = (max(table.rows.keys()) + 1) if table.rows else 1
        counter = getattr(table, "_next_id", 0)
        if counter < base:
            counter = base
        table._next_id = counter + 1
        return counter

    def _check_unique(self, obj) -> None:
        """检查表级唯一约束（redemption_records 的 (code_id, user_id)、
        auth_requests 的 (user_id, pending_key) 等）

        ⚠️ 对齐 MySQL 语义：**参与约束的列只要有一个为 NULL，该行就不参与冲突判定**。
        这正是 (user_id, pending_key) 能做到「同一用户最多一条 pending，终态记录不限」
        的原因；仿真层若把 NULL == NULL 判成冲突，会把合法的历史记录误判为违规。
        """
        for cons in obj.__table__.constraints:
            if not hasattr(cons, "columns") or len(cons.columns) == 0:
                continue
            names = [c.name for c in cons.columns]
            row = self.rows_snapshot(obj)
            if any(row.get(n) is None for n in names):
                continue  # MySQL: NULL 不参与唯一性冲突
            table = self._table(obj)
            for other_id, other in table.rows.items():
                if other_id == obj.id:
                    continue
                if all(other.get(n) == row.get(n) for n in names):
                    raise IntegrityError("UNIQUE constraint failed", None, None)

    def rows_snapshot(self, obj) -> dict:
        # `_Unloaded` 视为未加载（等价于"库里没这个值"）：唯一约束不能拿哨兵当普通值比
        return {
            c.name: (None if isinstance(v, _Unloaded) else v)
            for c in obj.__table__.columns
            for v in (getattr(obj, c.name, None),)
        }

    # ---------- Session API ----------
    def add(self, obj) -> None:
        self._pending.append(obj)

    def add_all(self, objs) -> None:
        self._pending.extend(objs)

    async def flush(self) -> None:
        self.flushes += 1
        for obj in list(self._pending):
            self._apply_defaults(obj)
            self._check_unique(obj)
            self._snapshot(obj.__table__.name, obj.id)
            self._table(obj).insert(obj)
        self._pending.clear()
        # 已加载对象的属性改动（如 user.points = x / code.status = 'disabled'）
        # 以「脏列」为单位写回权威行
        for table in self._tables.values():
            for obj in list(table.objs.values()):
                if table.dirty_columns(obj):
                    self._snapshot(obj.__table__.name, obj.id)
                table.flush_dirty(obj)

    async def commit(self) -> None:
        await self.flush()
        self.commits += 1
        # 事务结束，快照失效（真实数据库同样不可能再回滚已提交的事务）
        self._undo.clear()

    async def rollback(self) -> None:
        """回滚**已应用**的写入（P1-42 之一）。

        原先这里只 `self._pending.clear()`，于是 INSERT / UPDATE / DELETE 一旦
        flush 过就留在权威行里 —— 「失败分支里 rollback 了，所以余额没变」这类
        断言其实在仿真层是空话：钱已经被扣掉了，测试却照样绿。
        """
        for (table_name, row_id), (row, committed, obj) in reversed(list(self._undo.items())):
            table = self._tables.get(table_name)
            if table is None:
                continue
            if row is None:
                table.rows.pop(row_id, None)
                table.committed.pop(row_id, None)
                table.objs.pop(row_id, None)
                continue
            table.rows[row_id] = row
            if committed is not None:
                table.committed[row_id] = committed
            if obj is not None:
                table.objs[row_id] = obj
        self._undo.clear()
        self._pending.clear()

    async def refresh(self, obj) -> None:
        """权威行 → ORM 对象。这是唯一能修正陈旧快照的途径。"""
        self._table(obj).sync_to_obj(obj)

    async def delete(self, obj) -> None:
        """删除单个 ORM 对象（对应真实会话的 session.delete）"""
        await asyncio.sleep(0)
        table = self._table(obj)
        rid = getattr(obj, "id", None)
        self._snapshot(obj.__table__.name, rid)
        table.rows.pop(rid, None)
        table.committed.pop(rid, None)
        table.objs.pop(rid, None)
        # 尚未 flush 的 pending 对象一并移除，避免后续 flush 把它插回来
        self._pending = [o for o in self._pending if o is not obj]

    @asynccontextmanager
    async def begin_nested(self):
        """savepoint：本段插入失败只回滚本段，会话不进入失败态"""
        before = list(self._pending)
        try:
            yield self
            await self.flush()
        except Exception:
            self._pending = before
            raise

    async def execute(self, stmt, *args, **kwargs):
        await asyncio.sleep(0)  # 让出事件循环，制造并发交错
        try:
            result = self._execute(stmt, args)
        finally:
            await asyncio.sleep(0)
        return result

    def _execute(self, stmt, args=()):
        if isinstance(stmt, _DeleteStmt):
            return self._do_delete(stmt)
        if isinstance(stmt, _InsertStmt):
            return self._do_insert_bulk(stmt, args)
        if hasattr(stmt, "_values") and stmt._values is not None:
            return self._do_update(stmt)
        if hasattr(stmt, "column_descriptions"):
            return self._do_select(stmt)
        raise _Unsupported(f"unsupported statement: {type(stmt)}")

    # ---------- INSERT（批量） ----------
    def _do_insert_bulk(self, stmt, args):
        """`db.execute(insert(Model), [dict, ...])` —— SQLAlchemy 的 executemany 形态。

        为什么必须支持：`content_notify`（内容下架通知）与 `admin_message_service`（群发系统通知）
        都用这种写法批量写消息 —— 一条条 `add` 会同时持有上万个待 flush 的 ORM 实例。
        替身不支持的话，这两个服务只能「绕过测试」或被迫改成生产上更差的写法。
        """
        rows = args[0] if args else []
        if not isinstance(rows, (list, tuple)):
            raise _Unsupported("insert 参数必须是 dict 列表")
        table = self._table(stmt.table.name)
        for row in rows:
            if not isinstance(row, dict):
                raise _Unsupported("insert 的行必须是 dict")
            row_id = row.get("id")
            if row_id is None:
                raise _Unsupported("批量 insert 的行必须带显式 id（替身不模拟自增）")
            stored = dict(row)
            table.rows[row_id] = stored
            table.committed[row_id] = dict(stored)
        return _ExecResult(len(rows))


    # ---------- SELECT ----------
    def _do_select(self, stmt):
        descriptions = list(stmt.column_descriptions)
        if len(descriptions) > 1:
            return self._do_select_multi(stmt, descriptions)

        first_col = descriptions[0]
        entity = first_col.get("entity")
        if entity is None:
            # `select(func.count()).select_from(Model)` 的列描述没有 entity，
            # 但 FROM 子句里有；用于素材库等「只取总数」的查询。
            froms = list(stmt.get_final_froms() or [])
            if len(froms) == 1 and hasattr(froms[0], "name"):
                # 聚合查询只用表名；下面会在 entity.__name__ 之前走 count 分支。
                entity = froms[0].name
        if entity is None:
            raise _Unsupported("select without ORM entity is not supported")
        table = self._table(entity)
        criteria = list(getattr(stmt, "_where_criteria", []) or [])

        # 聚合查询：func.count(...)
        expr = first_col.get("expr")
        if expr is not None and hasattr(expr, "clauses") and not _is_column(expr):
            matched = [
                rid for rid, row in table.rows.items()
                if all(_eval(c, row) for c in criteria)
            ]
            return _Result([(len(matched),)], "count")

        # 实体查询时 name 就是类名；单列查询时 name 是列名
        single_column = first_col.get("name") != entity.__name__

        matched = [
            (rid, row) for rid, row in table.rows.items()
            if all(_eval(c, row) for c in criteria)
        ]
        # ORDER BY / LIMIT / OFFSET 必须真实生效（P2-51）：排序按**权威行**取值，
        # 与 WHERE 同源，避免「按陈旧 ORM 属性排序」把代码 bug 掩盖成仿真差异
        matched = _slice_rows(_sort_rows(matched, _order_directives(stmt)), stmt)

        fetched = _load_only_columns(stmt)
        tuples = []
        for rid, row in matched:
            obj = table.objs.get(rid)
            if obj is None:
                continue
            # SELECT 命中 identity map：返回对象（属性可能陈旧于最近一次 UPDATE）
            if single_column:
                name = first_col["name"]
                value = getattr(obj, name, None)
                if isinstance(value, _Unloaded):
                    # 列查询直接读库里的值：真实会话不会把"未加载"带出来
                    value = row.get(name)
                tuples.append((value,))
            else:
                _hydrate(obj, row, fetched)
                tuples.append((obj,))
        return _Result(tuples, "select")

    def _do_select_multi(self, stmt, descriptions):
        """多列 SELECT。

        本项目用到的形态只有一种：``select(分组列…, func.count())`` + ``GROUP BY``
        （评论列表用它取「每条顶层评论的真实回复数」）。这里精确实现该形态，
        其余多列形态**显式报错**而不是猜——测试替身返回错数据比报错危险得多。
        """
        entity = descriptions[0].get("entity")
        if entity is None:
            raise _Unsupported("multi-column select without ORM entity is not supported")
        table = self._table(entity)
        criteria = list(getattr(stmt, "_where_criteria", []) or [])

        agg_idx = [
            i
            for i, d in enumerate(descriptions)
            if d.get("expr") is not None
            and hasattr(d["expr"], "clauses")
            and not _is_column(d["expr"])
        ]
        if len(agg_idx) > 1:
            raise _Unsupported("multi-column select with >1 aggregate is not supported")

        value_cols = [d["name"] for i, d in enumerate(descriptions) if i not in agg_idx]
        matched = [
            row for row in table.rows.values() if all(_eval(c, row) for c in criteria)
        ]

        if not agg_idx:
            # 无聚合：逐行取值（保持插入顺序）
            return _Result([tuple(row.get(name) for name in value_cols) for row in matched], "select")

        # 分组聚合：按分组列取值分桶计数，保持首次出现顺序
        buckets: dict[tuple, int] = {}
        for row in matched:
            key = tuple(row.get(name) for name in value_cols)
            buckets[key] = buckets.get(key, 0) + 1

        tuples = []
        for key, count in buckets.items():
            out = list(key)
            out.insert(agg_idx[0], count)
            tuples.append(tuple(out))
        return _Result(tuples, "count")

    # ---------- UPDATE ----------
    def _do_update(self, stmt):
        table_name = stmt.table.name
        table = self._table(table_name)
        criteria = list(getattr(stmt, "_where_criteria", []) or [])
        # 「判定 + 写入」在同一段纯同步代码内完成：asyncio 不会在同步代码中间
        # 切换协程，因此并发 UPDATE 天然串行化，等价于数据库行锁。
        # 交错点只可能出现在 execute() 前后的 sleep(0)，即语句与语句之间。
        matched = [
            rid for rid, row in table.rows.items()
            if all(_eval(c, row) for c in criteria)
        ]
        for rid in matched:
            row = table.rows[rid]
            for col, value_expr in stmt._values.items():
                row[col.name] = _eval(value_expr, row)
        return _ExecResult(len(matched))

    # ---------- DELETE ----------
    def _do_delete(self, stmt):
        """DELETE：同时移除权威行、ORM 对象与 committed 基线。

        三者必须一起清，否则被删的行会通过 identity map 复活（objs 仍在），
        或让后续 flush_dirty 把已删行写回（committed 仍在）。
        """
        table = self._table(stmt.table.name)
        criteria = list(getattr(stmt, "_where_criteria", []) or [])
        matched = [
            rid for rid, row in list(table.rows.items())
            if all(_eval(c, row) for c in criteria)
        ]
        for rid in matched:
            table.rows.pop(rid, None)
            table.committed.pop(rid, None)
            table.objs.pop(rid, None)
        return _ExecResult(len(matched))

    # ---------- 测试辅助 ----------
    def seed(self, *objs) -> None:
        """预置数据（跳过 pending，直接落权威行）。

        ⚠️ 与「被测代码自己 INSERT」不同，seed 代表**库里已经存在、且已被读出来**的行：
        落库后立刻把权威行回填到对象上。否则测试里直接使用 `seed` 出来的对象
        （例如把 user 传进 service）会读到 `_Unloaded` 哨兵，
        而生产里这些对象都是 `get_current_user` 查出来的、列本来就是加载好的。
        """
        for obj in objs:
            self._apply_defaults(obj)
            table = self._table(obj)
            table.insert(obj)
            table.sync_to_obj(obj)

    def all_rows(self, model) -> list[dict]:
        return list(self._table(model).rows.values())

    def get(self, model, obj_id):
        return self._table(model).objs.get(obj_id)
