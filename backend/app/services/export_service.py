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

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.services import admin_resource_service, admin_tutorial_service, admin_user_service
from app.services.admin_audit_service import record_admin_action

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
        access_mode=f.get("access_mode") or None,
        deleted=f.get("deleted") or "active",
    )


async def _fetch_resources(db: AsyncSession, page: int, size: int, f: dict) -> dict:
    return await admin_resource_service.list_resources(
        db,
        page,
        size,
        keyword=f.get("keyword") or None,
        resource_type=f.get("resource_type") or None,
        is_featured=f.get("is_featured"),
        deleted=f.get("deleted") or "active",
    )


async def _fetch_points(db: AsyncSession, page: int, size: int, f: dict) -> dict:
    """复用列表接口同一个查询函数（含它对 type/direction 的合法性校验）。

    时间是前端 `toISOString()` 产出的带偏移字符串，必须换算成 naive UTC 再比较 ——
    库内时间列是 naive UTC，直接比在东八区会差 8 小时（与列表接口同样的坑）。
    """
    from app.services import point_service
    from app.utils.time import parse_iso_to_naive_utc

    return await point_service.list_all_transactions(
        db,
        page,
        size,
        keyword=f.get("keyword") or None,
        type=f.get("type") or None,
        direction=f.get("direction") or None,
        start_at=parse_iso_to_naive_utc(f["start_at"]) if f.get("start_at") else None,
        end_at=parse_iso_to_naive_utc(f["end_at"]) if f.get("end_at") else None,
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
            Column("积分", "points"),
            Column("VIP 到期", "vip_expire_time", _time),
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
        filters=("keyword", "category", "status", "is_published", "access_mode", "deleted"),
        columns=(
            Column("ID", "id"),
            Column("标题", "title"),
            Column("分类", "category"),
            Column("状态", "status"),
            Column("已发布", "is_published"),
            Column("访问方式", "access_mode"),
            Column("浏览量", "view_count"),
            Column("创建时间", "created_at", _time),
            Column("更新时间", "updated_at", _time),
        ),
    ),
    "resources": ExportSpec(
        key="resources",
        label="资源",
        perm="resource:view",
        target_type="resource",
        fetch=_fetch_resources,
        filters=("keyword", "resource_type", "is_featured", "deleted"),
        columns=(
            Column("ID", "id"),
            Column("名称", "name"),
            Column("类型", "resource_type"),
            Column("文件大小", "file_size"),
            Column("下载次数", "download_count"),
            Column("精选", "is_featured"),
            Column("创建时间", "created_at", _time),
        ),
    ),
    "point-transactions": ExportSpec(
        key="point-transactions",
        label="积分流水",
        # 与列表页一致：积分流水归 points 模块
        perm="points:view",
        target_type="point_transaction",
        fetch=_fetch_points,
        filters=("keyword", "type", "direction", "start_at", "end_at"),
        columns=(
            Column("ID", "id"),
            Column("用户账号", "user_account"),
            Column("类型", "type"),
            Column("变动", "points"),
            Column("变动后余额", "balance_after"),
            Column("说明", "description"),
            Column("操作人", "operator_account"),
            Column("时间", "created_at", _time),
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


def filename(spec: ExportSpec) -> str:
    """导出文件名：`users-20261030-0117.csv`（带时间，避免同名覆盖）。"""
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return f"{spec.key}-{stamp}.csv"
