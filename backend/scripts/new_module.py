"""新增业务模块的脚手架：一条命令生成「能跑、能审、能守」的四件套 + 一个前端列表页。

## 为什么不是代码生成器（那种选表/选字段的）

本仓库的三条硬约定（见 README「贡献」）决定了模块不是「建表 + CRUD」那么简单：

1. 新增管理端接口**必须**在 `core/rbac.py` 的规则表登记，否则 fail-closed 直接 403；
2. 新增写操作**必须**写审计，否则 `tests/test_admin_audit_coverage.py` 变红；
3. 新增权限码**必须**同步菜单项，否则 `tests/test_admin_menu.py` 变红。

所以这里的脚手架**只生成文件，不猜业务**：字段是模板给的通用五列，业务字段自己改；
登记动作以清单形式打印出来（附锚点），由人确认后再改 —— 自动改 6 个注册表出错时
更难排查，而清单漏一步就会被上面三条守卫当场抓住。

## 用法

```bash
cd backend
python scripts/new_module.py webinar --label 线上课 --dry-run   # 先看会生成什么
python scripts/new_module.py webinar --label 线上课             # 真的写文件
```

生成后按打印的清单登记（约 6 处），再 `alembic revision --autogenerate` 建表。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent

MODEL_TMPL = '''"""TODO: 写一句这个模型解决什么问题（不要写「用户表」这种同义反复）。"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class __CLASS__(Base):
    __tablename__ = "__TABLE__"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, default=generate_id
    )
    title: Mapped[str] = mapped_column(String(200), index=True, comment="标题")
    description: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="简介")
    # 发布态：公开侧一律过滤 is_published，后台侧不过滤（草稿要能看见）
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, index=True, comment="是否发布")
    # 软删除：删除只置位，恢复靠置空；公开查询必须同时过滤 deleted_at
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
'''

SCHEMA_TMPL = '''"""__LABEL__模块的出入参模型（只放传输结构，不放业务规则）。"""

from typing import Optional

from pydantic import BaseModel, Field

TITLE_MAX_LENGTH = 200


class __CLASS__ListItem(BaseModel):
    """列表条目：够渲染表格即可，不要整行返回（列表页会因此变慢）。"""

    id: int
    title: str
    description: Optional[str] = None
    is_published: bool
    created_at: Optional[str] = None


class __CLASS__Detail(__CLASS__ListItem):
    """详情：在列表字段之上补充编辑页需要的字段。"""

    updated_at: Optional[str] = None


class __CLASS__CreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=TITLE_MAX_LENGTH)
    description: Optional[str] = Field(None, max_length=500)
    is_published: Optional[bool] = True


class __CLASS__UpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=TITLE_MAX_LENGTH)
    description: Optional[str] = Field(None, max_length=500)
    # 默认 None = 不传就不改（不要用 False 覆盖既有状态）
    is_published: Optional[bool] = None
'''

SERVICE_TMPL = '''"""__LABEL__的后台业务逻辑。

分层约定（`tests/test_arch_guards.py` 会检查）：
- 本文件负责事务边界：`commit` 只能出现在这里，不能出现在 `api/`；
- 每个写操作都要 `record_admin_action`（只 add 不 commit），与业务同事务提交；
- 返回 ORM 对象或基础类型，序列化交给 `response_model`。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.user import User
from app.models.__MODEL__ import __CLASS__
from app.services.admin_audit_service import record_admin_action


def _utcnow() -> datetime:
    """库内时间列是 naive UTC，统一在这里收敛，避免各处自己 `replace(tzinfo=None)`。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def list___KEY__(
    db: AsyncSession,
    page: int,
    page_size: int,
    *,
    keyword: str | None = None,
    is_published: bool | None = None,
) -> dict:
    """后台列表：**不过滤发布态与软删**（运营要能看到草稿与回收站）。"""
    stmt = select(__CLASS__).where(__CLASS__.deleted_at.is_(None))
    if keyword:
        stmt = stmt.where(__CLASS__.title.contains(keyword))
    if is_published is not None:
        stmt = stmt.where(__CLASS__.is_published.is_(is_published))

    total = int(
        (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    )
    rows = (
        (
            await db.execute(
                stmt.order_by(__CLASS__.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


async def get___KEY__(db: AsyncSession, item_id: int) -> __CLASS__:
    row = (
        await db.execute(
            select(__CLASS__).where(__CLASS__.id == item_id, __CLASS__.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppException(code=404, message="__LABEL__不存在")
    return row


async def create___KEY__(db: AsyncSession, data: dict, admin: User) -> int:
    row = __CLASS__(**data)
    db.add(row)
    await db.flush()  # 先拿到主键，审计才能归因到具体行

    record_admin_action(
        db, admin, "create",
        target_type="__KEY__", target_id=row.id,
        summary=f"新建__LABEL__《{row.title}》",
    )
    await db.commit()
    return row.id


async def update___KEY__(db: AsyncSession, item_id: int, updates: dict, admin: User) -> None:
    row = await get___KEY__(db, item_id)
    changed = {k: v for k, v in updates.items() if v is not None}
    if not changed:
        return
    for field, value in changed.items():
        setattr(row, field, value)

    record_admin_action(
        db, admin, "update",
        target_type="__KEY__", target_id=item_id,
        summary=f"修改__LABEL__《{row.title}》",
        detail={"fields": sorted(changed)},
    )
    await db.commit()


async def delete___KEY__(db: AsyncSession, item_id: int, admin: User) -> None:
    """软删除：置 `deleted_at`，不做物理删除（误删可恢复，且审计仍能指到行）。"""
    row = await get___KEY__(db, item_id)
    row.deleted_at = _utcnow()

    record_admin_action(
        db, admin, "delete",
        target_type="__KEY__", target_id=item_id,
        summary=f"删除__LABEL__《{row.title}》",
    )
    await db.commit()
'''

API_TMPL = '''"""__LABEL__后台接口。

⚠️ 新增端点后必须同时：
1. 在 `core/rbac.py` 的 `_RULES` 里登记（未登记 = fail-closed 403）；
2. 在 `services/rbac_catalog.py` 里声明权限码（否则规则表引用了不存在的码，测试变红）；
3. 在 `services/admin_menu.py` 里加菜单项（否则「有接口没入口」）。
"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.admin import ActionAck, CreatedResult
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.content import PageResult
from app.schemas.__KEY__ import (
    __CLASS__CreateRequest,
    __CLASS__Detail,
    __CLASS__ListItem,
    __CLASS__UpdateRequest,
)
from app.services import admin___KEY___service as service


@router.get("/__PLURAL__", response_model=ApiResponse[PageResult[__CLASS__ListItem]])
async def list___KEY__(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    is_published: bool = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin("__KEY__:view")),
):
    return success_response(await service.list___KEY__(db, page, page_size, keyword=keyword, is_published=is_published))


@router.get("/__PLURAL__/{{__KEY___id}}", response_model=ApiResponse[__CLASS__Detail])
async def get___KEY__(
    __KEY___id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin("__KEY__:view")),
):
    return success_response(await service.get___KEY__(db, __KEY___id))


@router.post("/__PLURAL__", response_model=ApiResponse[CreatedResult])
async def create___KEY__(
    body: __CLASS__CreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin("__KEY__:create")),
):
    new_id = await service.create___KEY__(db, body.model_dump(exclude_none=True), admin)
    return success_response({{"id": new_id}})


@router.put("/__PLURAL__/{{__KEY___id}}", response_model=ApiResponse[ActionAck])
async def update___KEY__(
    __KEY___id: int,
    body: __CLASS__UpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin("__KEY__:edit")),
):
    await service.update___KEY__(db, __KEY___id, body.model_dump(exclude_unset=True), admin)
    return success_response({{"message": "已保存"}})


@router.delete("/__PLURAL__/{{__KEY___id}}", response_model=ApiResponse[ActionAck])
async def delete___KEY__(
    __KEY___id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin("__KEY__:delete")),
):
    await service.delete___KEY__(db, __KEY___id, admin)
    return success_response({{"message": "已删除"}})
'''

PAGE_TMPL = '''import {{ useState }} from 'react'
import {{ Button, Input, Space, Tag }} from 'antd'
import {{ FileTextOutlined, PlusOutlined }} from '@ant-design/icons'
import {{ useNavigate }} from 'react-router-dom'

import {{ DataTable, ListToolbar, PageHeader }} from '../../components/ui'
import {{ useCrudList }} from '../../hooks/useCrudList'
import {{ formatTime }} from '../../utils/datetime'

/**
 * __LABEL__列表（脚手架生成）。
 *
 * 骨架已按仓库约定接好：PageHeader / ListToolbar / DataTable / useCrudList。
 * 业务字段（列、筛选项、权限码）按需替换，其余部分不要重写——
 * 手写外壳会被 `components/ui/listPageCoverage.test.ts` 判红。
 */
interface __CLASS__Item {{
  id: number
  title: string
  is_published: boolean
  created_at: string | null
}}

export default function __CLASS__List() {{
  const navigate = useNavigate()
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')

  const {{ items, loading, page, pageSize, total, refresh, setPage }} = useCrudList<__CLASS__Item>({{
    basePath: '/admin/__PLURAL__',
    params: {{ keyword }},
  }})

  const hasFilter = !!keyword

  return (
    <div className="adm-page">
      <PageHeader
        icon={{<FileTextOutlined />}}
        title="__LABEL__管理"
        subtitle="脚手架生成：替换列、筛选项与权限码即可"
        count={{{{ total }}}}
        actions={{
          <Button type="primary" icon={{<PlusOutlined />}} onClick={{() => navigate('/__PLURAL__/new')}}>
            新建__LABEL__
          </Button>
        }}
      />

      <ListToolbar
        hasFilter={{hasFilter}}
        onReset={{() => {{
          setKeywordInput('')
          setKeyword('')
          setPage(1)
        }}}}
        extra={{
          <Button onClick={{refresh}}>刷新</Button>
        }}
      >
        <Input.Search
          allowClear
          placeholder="搜索标题"
          value={{keywordInput}}
          onChange={{(e) => setKeywordInput(e.target.value)}}
          onSearch={{(v) => {{
            setKeyword(v.trim())
            setPage(1)
          }}}}
          style={{{{ width: 240 }}}}
        />
      </ListToolbar>

      <DataTable<__CLASS__Item>
        dataSource={{items}}
        rowKey="id"
        loading={{loading}}
        scrollX={{720}}
        pagination={{{{ page, pageSize, total, onPageChange: setPage }}}}
        empty={{{{
          hasFilter,
          filteredText: '没有符合筛选条件的__LABEL__',
          emptyText: '还没有__LABEL__',
          onReset: () => {{
            setKeywordInput('')
            setKeyword('')
          }},
        }}}}
        columns={{[
          {{ title: 'ID', dataIndex: 'id', width: 90 }},
          {{ title: '标题', dataIndex: 'title' }},
          {{
            title: '状态',
            dataIndex: 'is_published',
            width: 100,
            render: (v: boolean) => (v ? <Tag color="green">已发布</Tag> : <Tag>草稿</Tag>),
          }},
          {{
            title: '创建时间',
            dataIndex: 'created_at',
            width: 170,
            render: (v: string | null) => <span className="adm-muted">{{formatTime(v)}}</span>,
          }},
          {{
            title: '操作',
            key: 'action',
            width: 120,
            fixed: 'right',
            render: (_, row) => (
              <Button type="link" size="small" onClick={{() => navigate(`/__PLURAL__/${{row.id}}/edit`)}}>
                编辑
              </Button>
            ),
          }},
        ]}}
      />
    </div>
  )
}}
'''


def _fill(tmpl: str, cls: str, label: str, key: str, table: str, plural: str, model_file: str) -> str:
    """用占位符替换而不是 str.format：模板里全是 JSX/字典花括号，format 会当场炸。"""
    return (
        tmpl.replace("__CLASS__", cls)
        .replace("__LABEL__", label)
        .replace("__PLURAL__", plural)
        .replace("__MODEL__", model_file)
        .replace("__TABLE__", table)
        .replace("__KEY__", key)
    )


def pascal(key: str) -> str:
    return "".join(part.capitalize() for part in key.split("_"))


def build_files(key: str, label: str, table: str) -> dict[Path, str]:
    cls = pascal(key)
    # 表名与模块键通常只差一个复数 s；文件名用模块键，避免 models/webinars.py 这种复数命名
    model_file = key
    plural = key + "s"
    return {
        BACKEND / "app" / "models" / f"{model_file}.py": _fill(
            MODEL_TMPL, cls, label, key, table, plural, model_file
        ),
        BACKEND / "app" / "schemas" / f"{key}.py": _fill(
            SCHEMA_TMPL, cls, label, key, table, plural, model_file
        ),
        BACKEND / "app" / "services" / f"admin_{key}_service.py": _fill(
            SERVICE_TMPL, cls, label, key, table, plural, model_file
        ),
        BACKEND / "app" / "api" / f"admin_{key}.py": _fill(
            API_TMPL, cls, label, key, table, plural, model_file
        ),
        # 前端模板是用 str.format 时代写的（双花括号转义），现在走 replace，需要还原一层
        REPO / "admin-frontend" / "src" / "pages" / cls / "List.tsx": _fill(
            PAGE_TMPL, cls, label, key, table, plural, model_file
        ).replace("{{", "{").replace("}}", "}"),
    }


CHECKLIST = """
接下来必须手工登记的 8 处（漏任一处都会被守卫拦下）：
  1. backend/app/models/__init__.py    —— import 模型并加入 __all__（漏了 Alembic 会判定为多余表）
  2. backend/app/api/admin.py          —— 加 `admin___KEY__,`（import 即注册路由）
  3. backend/app/services/rbac_catalog.py —— 加 __KEY__:view/create/edit/delete 四条权限码
  4. backend/app/core/rbac.py          —— _RULES 里登记每条路由（未登记 = fail-closed 403）
  5. backend/app/services/admin_menu.py —— 加菜单项（key 必须与前端路由一致）
  6. backend/app/module_registry.py    —— 加 ModuleDef（路径前缀 + 菜单 key），否则模块开关管不到它
  7. admin-frontend/src/App.tsx        —— 加 lazy 路由（/ __PLURAL__、/ __PLURAL__/new、/ __PLURAL__/:id/edit）
  8. 生成迁移：cd backend && alembic revision --autogenerate -m "add __TABLE__"

改完立刻跑这三条，红了就按提示补：
  cd backend && DB_PORT=1 REDIS_PORT=1 python -m pytest tests/test_admin_menu.py tests/test_rbac.py tests/test_admin_audit_coverage.py -q
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成一个新业务模块的骨架")
    parser.add_argument("key", help="模块键（snake_case），同时作为权限码前缀，如 webinar")
    parser.add_argument("--label", required=True, help="中文名，如「线上课」")
    parser.add_argument("--table", help="表名，默认 <key>s")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要写入的文件，不落盘")
    args = parser.parse_args(argv)

    key = args.key.strip().lower()
    if not key.isidentifier():
        print("✗ key 必须是合法的 snake_case 标识符：__KEY__", file=sys.stderr)
        return 2
    table = args.table or f"{key}s"
    files = build_files(key, args.label, table)

    existing = [p for p in files if p.exists()]
    if existing:
        print("✗ 以下文件已存在，脚手架不会覆盖：", file=sys.stderr)
        for p in existing:
            print(f"    {p.relative_to(REPO)}", file=sys.stderr)
        return 1

    for path, content in files.items():
        rel = path.relative_to(REPO)
        if args.dry_run:
            print(f"--- {rel} ---")
            print(content)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"✓ {rel}")

    print(
        CHECKLIST.replace("__KEY__", key)
        .replace("__PLURAL__", f"{key}s")
        .replace("__TABLE__", table)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
