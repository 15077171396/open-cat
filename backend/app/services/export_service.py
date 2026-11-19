"""列表导出（CSV）：把后台看到的列表原样带走。

为什么要有它：这是后台运营的第一刚需 —— 对账、拉名单、给财务、做二次分析，
截图和复制粘贴都会在几千行时崩掉。

三个刻意的设计约束：

1. **口径必须与列表一致**：导出直接调用列表接口背后的同一个 service 函数、
   同一组筛选参数，而不是另写一份 SQL。否则迟早出现「页面查出 312 条，导出 318 条」，
   而这种差异没人能一眼看出来。
2. **流式 + 上限**：分批查询、逐批 yield，内存不随行数增长；同时硬上限
   `MAX_ROWS`，超过就截断（并在首行注释里说明），避免一次导出把数据库拖垮。
3. **CSV 注入防护**：单元格以 `= + - @` 开头时前置单引号。用户控制的昵称/标题里
   写 `=cmd|' /c calc'!A0` 这类内容，Excel 打开会当公式执行 —— 导出功能最容易
   被忽略的注入面就在这里。
"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.services import admin_audit_service, admin_tutorial_service, admin_user_service
from app.services.admin_audit_service import record_admin_action
from app.services import login_log_service
from app.services.ops_metric_service import METRIC_DEFS

# 单次导出的硬上限。20k 行 CSV 约 3–5 MB，浏览器与 Excel 都不吃力；
# 再大就该走「按需拉 API」而不是点一个按钮。
MAX_ROWS = 20_000
# 每批查询行数：与列表分页同量级，避免大 OFFSET 拖慢
BATCH = 500

# Excel 打开 UTF-8 CSV 需要 BOM，否则中文全是乱码
_BOM = "\ufeff"

# CSV 公式注入的触发字符（OWASP 建议）
_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> str:
    """把任意值转成安全单元格：None → 空、布尔 → 是/否、防公式注入、去换行。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    text = str(value)
    # 多行内容会破坏「一行一条记录」的结构（Excel 里表现为整表错位）
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    if text.startswith(_INJECTION_PREFIXES):
        text = "'" + text
    return text


def _time(value: Any) -> str:
    """ISO 时间转成 Excel 友好的 `YYYY-MM-DD HH:MM:SS`（保留原时区语义，不做换算）。"""
    if not value:
        return ""
    text = str(value)
    if "T" in text:
        text = text.replace("T", " ", 1)
    # 去掉秒以下与末尾时区标记：导出给人看，不需要微秒
    for sep in (".", "+"):
        if sep in text[10:]:
            cut = text.index(sep, 10)
            text = text[:cut]
    if text.endswith("Z"):
        text = text[:-1]
    return text


@dataclass(frozen=True)
class Column:
    """一列：表头文字 + 取哪个字段 + 可选格式化。"""

    label: str
    key: str
    fmt: Callable[[Any], str] | None = None


@dataclass(frozen=True)
class ExportSpec:
    """一个可导出的资源。"""

    key: str
    label: str
    #: 导出该资源所需的权限码，由端点各自绑定（路径级规则，不走这里）
    perm: str
    #: 写审计时用的 target_type。**刻意复用已有的单数值**（user/tutorial/resource），
    #: 而不是资源 key（users/tutorials）—— 否则同一类对象在审计里有两个代码，
    #: 筛选「用户」时永远只能看到一半记录。
    target_type: str
    #: 与列表接口共用的查询函数：签名统一为 (db, page, page_size, **filters)
    fetch: Callable[..., Awaitable[dict]]
    columns: tuple[Column, ...]
    #: 允许透传的筛选参数名（白名单，防止任意 kwargs 打进查询）
    filters: tuple[str, ...] = ()


async def _fetch_users(db: AsyncSession, page: int, size: int, f: dict) -> dict:
    return await admin_user_service.list_users(
        db,
        page,
        size,
        role=f.get("role") or None,
        keyword=f.get("keyword") or None,
        account_status=f.get("account_status") or "active",
    )


async def _fetch_tutorials(db: AsyncSession, page: int, size: int, f: dict) -> dict:
    return await admin_tutorial_service.list_tutorials(
        db,
        page,
        size,
        keyword=f.get("keyword") or None,
        category=f.get("category") or None,
        status=f.get("status") or None,
        is_published=f.get("is_published"),
        deleted=f.get("deleted") or "active",
    )


async def _fetch_ops_metrics(db: AsyncSession, page: int, size: int, f: dict) -> dict:
    """快照行按 metrics JSON 展开为扁平行（列集合固定 = METRIC_DEFS 键序）。

    text + 绑定参数（与 search_log_service 同款形态），分页值一律绑定。
    """
    import json

    offset = (int(page) - 1) * int(size)
    rows = (
        await db.execute(
            text(
                "SELECT taken_at, metrics FROM ops_metric_snapshots "
                "ORDER BY taken_at DESC, id DESC LIMIT :limit OFFSET :offset"
            ),
            {"limit": int(size), "offset": offset},
        )
    ).all()
    items = []
    for row in rows:
        mapping = row._mapping
        metrics = {}
        raw = mapping.get("metrics")
        if raw:
            try:
                metrics = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                metrics = {}
        item = {"taken_at": mapping.get("taken_at").isoformat() if mapping.get("taken_at") else None}
        for key in METRIC_DEFS:
            value = metrics.get(key)
            item[key] = int(value) if value is not None else ""
        items.append(item)
    total = (
        await db.execute(
            text("SELECT COUNT(*) FROM ops_metric_snapshots"), {}
        )
    ).scalar() or 0
    return {"items": items, "total": int(total)}


async def _fetch_login_logs(db: AsyncSession, page: int, size: int, f: dict) -> dict:
    # 导出与列表页共用 login_log_service.list_logs：脱敏/筛选/分页口径完全一致
    return await login_log_service.list_logs(
        db,
        page,
        size,
        account=f.get("account") or None,
        status=f.get("status") or None,
        created_after=f.get("start_at"),
        created_before=f.get("end_at"),
    )


async def _fetch_audit_logs(db: AsyncSession, page: int, size: int, f: dict) -> dict:
    # 导出与列表页共用同一查询：数据范围/筛选语义完全一致（导出口径不会比页面宽）
    return await admin_audit_service.list_audit_logs(
        db,
        page,
        size,
        action=f.get("action") or None,
        target_type=f.get("target_type") or None,
        risk=f.get("risk") or None,
        keyword=f.get("keyword") or None,
        start_at=f.get("start_at"),
        end_at=f.get("end_at"),
    )


RESOURCES: dict[str, ExportSpec] = {
    "users": ExportSpec(
        key="users",
        label="用户",
        perm="user:view",
        target_type="user",
        fetch=_fetch_users,
        filters=("role", "keyword", "account_status"),
        columns=(
            Column("ID", "id"),
            Column("账号", "account"),
            Column("认证状态", "auth_status"),
            Column("部门", "department_name"),
            Column("角色", "role"),
            Column("注册时间", "created_at", _time),
            Column("注销时间", "deleted_at", _time),
        ),
    ),
    "tutorials": ExportSpec(
        key="tutorials",
        label="教程",
        perm="tutorial:view",
        target_type="tutorial",
        fetch=_fetch_tutorials,
        filters=("keyword", "category", "status", "is_published", "deleted"),
        columns=(
            Column("ID", "id"),
            Column("标题", "title"),
            Column("分类", "category"),
            Column("状态", "status"),
            Column("已发布", "is_published"),
            Column("浏览量", "view_count"),
            Column("创建时间", "created_at", _time),
            Column("更新时间", "updated_at", _time),
        ),
    ),
    # 审计日志导出（2026-11-19）：离线归档/汇报场景；字段级 diff（detail）刻意不导
    # —— 线上页有完整 detail 与筛选，CSV 只带摘要，避免把安全掩码字段二次扩散
    "audit-logs": ExportSpec(
        # key 即路由段与文件名前缀：用连字符与 /export/audit-logs 路由对齐
        key="audit-logs",
        label="操作审计",
        perm="audit:view",
        target_type="audit_log",
        fetch=_fetch_audit_logs,
        filters=("action", "target_type", "risk", "keyword", "start_at", "end_at"),
        columns=(
            Column("ID", "id"),
            Column("操作者", "admin_account"),
            Column("动作", "action"),
            Column("动作说明", "action_label"),
            Column("风险", "risk_level"),
            Column("目标类型", "target_type_label"),
            Column("目标ID", "target_id"),
            Column("摘要", "summary"),
            Column("IP", "ip"),
            Column("请求ID", "request_id"),
            Column("时间", "created_at", _time),
        ),
    ),
    # 登录日志导出（2026-11-19）：与列表页共用 list_logs（脱敏/筛选/分页同口径）；
    # request_id 不导 —— 它是内部排查线索，运营归档场景价值低且拉宽列
    "login-logs": ExportSpec(
        key="login-logs",
        label="登录日志",
        perm="login_log:view",
        target_type="login_log",
        fetch=_fetch_login_logs,
        filters=("account", "status", "start_at", "end_at"),
        columns=(
            Column("时间", "created_at", _time),
            Column("账号", "account"),
            Column("状态", "status"),
            Column("结果说明", "message"),
            Column("IP", "ip"),
            Column("设备 (User-Agent)", "user_agent"),
        ),
    ),
    # 核心指标快照（2026-11-19）：运维归档用；列 = 采样时间 + 各指标键（固定集合）
    "ops-metrics": ExportSpec(
        key="ops-metrics",
        label="核心指标快照",
        perm="dashboard:view",
        target_type="ops_metric",
        fetch=_fetch_ops_metrics,
        filters=(),
        columns=(
            Column("采样时间", "taken_at", _time),
            *(
                Column(str(defn["label"]), key)
                for key, defn in METRIC_DEFS.items()
            ),
        ),
    ),
}


def get_spec(resource: str) -> ExportSpec:
    """按 key 取资源定义；未知资源抛 KeyError（端点转 404，不静默返回空文件）。"""
    return RESOURCES[resource]


def pick_filters(spec: ExportSpec, raw: dict[str, Any]) -> dict[str, Any]:
    """只保留白名单内的筛选参数，并丢掉空值（空串/None 与「不筛选」等价）。"""
    return {
        name: raw[name]
        for name in spec.filters
        if raw.get(name) not in (None, "")
    }


async def count_rows(db: AsyncSession, spec: ExportSpec, filters: dict[str, Any]) -> int:
    """先用一次小查询拿到总数：用于审计（谁导出了多少行）与截断判断。"""
    result = await spec.fetch(db, 1, 1, filters)
    total = result.get("total")
    return int(total) if isinstance(total, (int, float)) else len(result.get("items") or [])


async def stream_csv(spec: ExportSpec, filters: dict[str, Any]) -> AsyncIterator[str]:
    """分批查询并逐批产出 CSV 文本。

    自己开 session 而不是复用请求的那个：`StreamingResponse` 的生成器可能
    在依赖清理之后才被消费完，复用请求级 session 会踩「session 已关闭」。
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")  # Excel 更认 CRLF

    def flush() -> str:
        text = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return text

    # 表头
    yield _BOM
    writer.writerow([c.label for c in spec.columns])
    yield flush()

    written = 0
    page = 1
    async with async_session() as db:
        while written < MAX_ROWS:
            want = min(BATCH, MAX_ROWS - written)
            result = await spec.fetch(db, page, want, filters)
            items = result.get("items") or []
            if not items:
                break
            for item in items:
                writer.writerow(
                    [c.fmt(item.get(c.key)) if c.fmt else _cell(item.get(c.key)) for c in spec.columns]
                )
            written += len(items)
            yield flush()

            total = result.get("total")
            # 已是最后一页就停：靠 len(items) < want 判断会多查一次空页
            if isinstance(total, int) and written >= min(total, MAX_ROWS):
                break
            if len(items) < want:
                break
            page += 1

    if written >= MAX_ROWS:
        # 截断必须显式写在文件里：否则拿到文件的人会以为这就是全量
        writer.writerow([])
        writer.writerow([f"【已截断】本次最多导出 {MAX_ROWS} 行，请用筛选条件缩小范围后再导出"])
        yield flush()


async def record_export(
    db: AsyncSession,
    admin: Any,
    spec: ExportSpec,
    filters: dict[str, Any],
    total: int,
) -> None:
    """记一条「导出了什么」的审计并提交。

    导出不改数据，所以容易被当成只读操作跳过审计 —— 但它把库里的数据带到了系统之外，
    是事后唯一能回答「谁把用户手机号列表导走了」的记录点。

    放在 service 而不是端点：`tests/test_arch_guards.py` 禁止 API 层出现 `db.commit()`，
    这条约束在这里是对的 —— 事务边界应当由业务层决定。
    """
    # 注意：record_admin_action 是**同步**函数（只 add 不 commit），不要 await
    record_admin_action(
        db,
        admin,
        "data_export",
        target_type=spec.target_type,
        summary=f"导出{spec.label} {total} 行",
        detail={"filters": filters, "rows": total, "resource": spec.key},
    )
    await db.commit()


async def build_xlsx(spec: ExportSpec, filters: dict[str, Any]) -> bytes:
    """构建 Excel 工作簿（openpyxl write_only 逐批写，内存不随行数爆炸）。

    上限与 CSV 同为 MAX_ROWS；数据来源（spec.fetch / 分页 / total 判断）
    与 stream_csv 完全一致 —— 两种格式的行集永远相同。
    """
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    ws = wb.create_sheet(spec.label)

    ws.append([c.label for c in spec.columns])

    written = 0
    page = 1
    async with async_session() as db:
        while written < MAX_ROWS:
            want = min(BATCH, MAX_ROWS - written)
            result = await spec.fetch(db, page, want, filters)
            items = result.get("items") or []
            if not items:
                break
            for item in items:
                ws.append(
                    [
                        c.fmt(item.get(c.key)) if c.fmt
                        else _xlsx_cell(item.get(c.key))
                        for c in spec.columns
                    ]
                )
            written += len(items)
            total = result.get("total")
            if isinstance(total, int) and written >= min(total, MAX_ROWS):
                break
            if len(items) < want:
                break
            page += 1

    if written >= MAX_ROWS:
        ws.append([f"已达单次导出上限 {MAX_ROWS} 行，文件已截断"])

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _xlsx_cell(value: Any) -> Any:
    """None → 空；数字保留为数字（可排序可求和）；文本走 _cell 清洗
    （xlsx 单元格以 = 开头同样触发 Excel 公式注入，清洗规则与 CSV 一致）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return value
    return _cell(str(value))


def filename(spec: ExportSpec, fmt: str = "csv") -> str:
    """导出文件名：`users-20261030-0117.csv`（带时间，避免同名覆盖）。"""
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    ext = "xlsx" if fmt == "xlsx" else "csv"
    return f"{spec.key}-{stamp}.{ext}"
