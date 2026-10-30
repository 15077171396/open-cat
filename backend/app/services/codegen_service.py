"""代码生成器：读取数据库表结构，生成后端 CRUD 骨架（只读预览 + ZIP 下载）。

设计边界：
- 只读取 ``information_schema`` / 检查器元数据，不建表、不写业务库；
- 生成物只是**骨架文件**，下载后需要按 ``INTEGRATION.md`` 手工接入路由、RBAC 与审计；
- 命名与字段都走白名单：模块名 / 类名 / 路由前缀正则校验，字段必须属于目标表，
  避免把任意字符串拼进文件路径或模板。
"""

from __future__ import annotations

import io
import keyword
import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.degrade import degrade_guard
from app.core.exceptions import AppException
from app.database import engine
from app.models.user import User
from app.services.admin_audit_service import record_admin_action

MODULE_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
CLASS_RE = re.compile(r"^[A-Z][A-Za-z0-9]{1,60}$")
ROUTE_RE = re.compile(r"^/[a-z0-9][a-z0-9/_-]{0,79}$")
TABLE_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
COLUMN_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
RESERVED_COLUMNS = {"metadata", "registry"}
RESERVED_EDITABLE = {"db", "admin"}
RESERVED_PRIMARY = {"db", "admin", "payload", "changes"}
TIMESTAMP_FIELDS = {"created_at", "updated_at"}
SKIP_TABLES = {"alembic_version"}
BACKEND_ROOT = Path(__file__).resolve().parents[2]

_ENV = Environment(
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


@dataclass(frozen=True)
class ColumnSchema:
    name: str
    sql_type: str
    sa_type: str
    py_type: str
    nullable: bool
    comment: str
    primary: bool = False
    unique: bool = False
    indexed: bool = False
    default: str | None = None

    @property
    def editable(self) -> bool:
        return not self.primary and self.name not in TIMESTAMP_FIELDS


@dataclass(frozen=True)
class TableSchema:
    name: str
    comment: str
    columns: tuple[ColumnSchema, ...]
    uniques: tuple[tuple[str, ...], ...] = ()

    @property
    def primary_key(self) -> ColumnSchema | None:
        for column in self.columns:
            if column.primary:
                return column
        return None


# ---------- 类型映射 ----------
_SA_TYPE_NAMES = {
    "int": "Integer",
    "bool": "Boolean",
    "float": "Float",
    "str": "String",
    "bytes": "LargeBinary",
    "datetime": "DateTime",
    "date": "Date",
    "time": "Time",
    "Decimal": "Numeric",
    "Any": "JSON",
}


def _display_type(column_type: Any, type_name: str) -> str:
    """生成简洁的类型展示（反射出的 MySQL 类型字符串带 COLLATE，直接展示太吵）。"""
    length = getattr(column_type, "length", None)
    precision = getattr(column_type, "precision", None)
    scale = getattr(column_type, "scale", None)
    if type_name in {"VARCHAR", "CHAR", "STRING", "NCHAR", "NVARCHAR", "BINARY", "VARBINARY"}:
        return f"{type_name}({length})" if length else type_name
    if type_name in {"DECIMAL", "NUMERIC"} and precision is not None:
        return f"{type_name}({precision},{scale})" if scale is not None else f"{type_name}({precision})"
    if type_name == "TINYINT" and getattr(column_type, "display_width", None) == 1:
        return "TINYINT(1)"
    return type_name


def _type_info(column_type: Any) -> tuple[str, str, str]:
    """返回 (SQLAlchemy 类型表达式, Python 类型, 展示用类型名)。未知类型回退 Text。"""
    name = type(column_type).__name__.upper()
    display = _display_type(column_type, name)

    if name in {"BIGINT"}:
        return "BigInteger", "int", display
    if name in {"INTEGER", "INT"}:
        return "Integer", "int", display
    if name in {"SMALLINT"}:
        return "SmallInteger", "int", display
    if name == "TINYINT":
        if getattr(column_type, "display_width", None) == 1:
            return "Boolean", "bool", display
        return "SmallInteger", "int", display
    if name in {"VARCHAR", "CHAR", "STRING", "NCHAR", "NVARCHAR"}:
        length = getattr(column_type, "length", None) or 255
        return f"String({length})", "str", display
    if name in {"TEXT", "MEDIUMTEXT", "LONGTEXT", "TINYTEXT"}:
        return "Text", "str", display
    if name in {"DECIMAL", "NUMERIC"}:
        precision = getattr(column_type, "precision", None)
        scale = getattr(column_type, "scale", None)
        args = ", ".join(str(value) for value in (precision, scale) if value is not None)
        return f"Numeric({args})" if args else "Numeric", "Decimal", display
    if name in {"FLOAT", "DOUBLE", "REAL"}:
        return "Float", "float", display
    if name in {"DATETIME", "TIMESTAMP"}:
        return "DateTime", "datetime", display
    if name == "DATE":
        return "Date", "date", display
    if name == "TIME":
        return "Time", "time", display
    if name == "JSON":
        return "JSON", "Any", display
    if name in {"BLOB", "LONGBLOB", "MEDIUMBLOB", "TINYBLOB", "BINARY", "VARBINARY"}:
        return "LargeBinary", "bytes", display
    if name == "ENUM":
        values = getattr(column_type, "enums", None) or []
        length = max((len(str(value)) for value in values), default=50)
        return f"String({length})", "str", display
    return "Text", "str", display


def _default_expr(column: ColumnSchema) -> str | None:
    """把 introspection 的默认值转成 Python 表达式；时间函数/复杂表达式不猜。"""
    raw = column.default
    if raw is None:
        return None
    text = str(raw).strip()
    if text.startswith("'") and text.endswith("'") and len(text) >= 2:
        text = text[1:-1]
    lowered = text.lower()
    if "current_timestamp" in lowered or "now()" in lowered:
        return None
    if column.py_type == "bool":
        if text in {"0", "false", "False"}:
            return "False"
        if text in {"1", "true", "True"}:
            return "True"
        return None
    if column.py_type == "int":
        try:
            return str(int(text))
        except ValueError:
            return None
    if column.py_type == "float":
        try:
            return repr(float(text))
        except ValueError:
            return None
    if column.py_type == "Decimal":
        try:
            Decimal(text)
        except InvalidOperation:
            return None
        return f'Decimal("{text}")'
    if column.py_type == "str":
        return repr(text)
    return None


def _introspect_sync(sync_conn: Any) -> dict[str, TableSchema]:
    inspector = inspect(sync_conn)
    tables: dict[str, TableSchema] = {}
    for table_name in inspector.get_table_names():
        if table_name in SKIP_TABLES:
            continue
        pk_columns = set(inspector.get_pk_constraint(table_name).get("constrained_columns") or [])
        uniques = [
            tuple(item.get("column_names") or [])
            for item in inspector.get_unique_constraints(table_name)
        ]
        unique_columns = {columns[0] for columns in uniques if len(columns) == 1}
        indexed_columns = {
            columns[0]
            for item in inspector.get_indexes(table_name)
            if len(columns := item.get("column_names") or []) == 1 and not item.get("unique")
        }
        columns: list[ColumnSchema] = []
        for item in inspector.get_columns(table_name):
            sa_type, py_type, display = _type_info(item["type"])
            columns.append(
                ColumnSchema(
                    name=item["name"],
                    sql_type=display,
                    sa_type=sa_type,
                    py_type=py_type,
                    nullable=bool(item.get("nullable", True)),
                    comment=str(item.get("comment") or ""),
                    primary=item["name"] in pk_columns,
                    unique=item["name"] in unique_columns,
                    indexed=item["name"] in indexed_columns,
                    default=item.get("default"),
                )
            )
        tables[table_name] = TableSchema(
            name=table_name,
            comment=str((inspector.get_table_comment(table_name) or {}).get("text") or ""),
            columns=tuple(columns),
            uniques=tuple(uniques),
        )
    return tables


async def get_tables() -> dict:
    """读取当前库的全部业务表；失败时降级为 available=False，不抛 500。"""
    snapshot: dict[str, TableSchema] = {}
    readable = False
    with degrade_guard(
        "codegen.tables", detail="代码生成器读取表结构失败，页面显示不可用（不影响其它后台功能）"
    ):
        async with engine.connect() as conn:
            snapshot = await conn.run_sync(_introspect_sync)
        readable = True
    database = getattr(engine.url, "database", "") or ""
    return {
        "available": readable,
        "database": database if readable else "",
        "error": "" if readable else "读取数据库表结构失败",
        "items": [_table_payload(schema) for schema in snapshot.values()] if readable else [],
    }


def _suggest_module(table_name: str) -> str:
    module = re.sub(r"[^a-z0-9_]+", "_", table_name.lower()).strip("_")
    if not module or module[0].isdigit():
        module = f"t_{module}" if module else "generated_module"
    return module[:40]


def _suggest_class(module: str) -> str:
    return "".join(part.capitalize() for part in module.split("_") if part) or "Generated"


def _table_payload(schema: TableSchema) -> dict:
    module = _suggest_module(schema.name)
    editable = [column.name for column in schema.columns if column.editable]
    return {
        "name": schema.name,
        "comment": schema.comment,
        "columns": [
            {
                "name": column.name,
                "type": column.sql_type,
                "nullable": column.nullable,
                "comment": column.comment,
                "primary": column.primary,
                "editable": column.editable,
            }
            for column in schema.columns
        ],
        "suggested": {
            "module": module,
            "class_name": _suggest_class(module),
            "title": schema.comment or schema.name,
            "route_prefix": f"/{module.replace('_', '-')}",
            "fields": editable,
        },
    }


async def _load_table(table_name: str) -> TableSchema:
    tables = await _snapshot_or_raise()
    schema = tables.get(table_name)
    if schema is None:
        raise AppException(code=404, message=f"表「{table_name}」不存在")
    return schema


async def _snapshot_or_raise() -> dict[str, TableSchema]:
    snapshot: dict[str, TableSchema] | None = None
    with degrade_guard(
        "codegen.snapshot", detail="代码生成器读取表结构失败，本次请求返回 503"
    ):
        async with engine.connect() as conn:
            snapshot = await conn.run_sync(_introspect_sync)
    if snapshot is None:
        raise AppException(code=503, message="读取数据库表结构失败，请检查数据库连接后重试")
    return snapshot


@dataclass(frozen=True)
class CodegenConfig:
    table: str
    module: str
    class_name: str
    title: str = ""
    route_prefix: str = ""
    fields: tuple[str, ...] = ()


def _validate_config(schema: TableSchema, config: CodegenConfig) -> tuple[CodegenConfig, list[str]]:
    module = config.module.strip()
    class_name = config.class_name.strip()
    if not MODULE_RE.match(module) or keyword.iskeyword(module):
        raise AppException(
            code=400,
            message="模块名需为小写字母开头的 snake_case，1-40 位，且不能是 Python 关键字",
        )
    if not CLASS_RE.match(class_name) or keyword.iskeyword(class_name):
        raise AppException(code=400, message="类名需为大写字母开头的 PascalCase，1-60 位")
    if not TABLE_RE.match(schema.name):
        raise AppException(code=400, message="表名包含代码生成器不支持的字符")
    invalid_columns = [
        column.name
        for column in schema.columns
        if not COLUMN_RE.match(column.name) or keyword.iskeyword(column.name)
        or column.name in RESERVED_COLUMNS
    ]
    if invalid_columns:
        raise AppException(
            code=400,
            message=(
                "以下列名无法直接映射为 Python 标识符，请先改表结构或手写该模块："
                + "、".join(invalid_columns)
            ),
        )
    raw_title = (config.title or schema.comment or schema.name).strip()
    title = re.sub(r"[\r\n\"'\\]+", " ", raw_title).strip()[:80]
    route_prefix = (config.route_prefix or f"/{module.replace('_', '-')}").strip()
    if not ROUTE_RE.match(route_prefix):
        raise AppException(code=400, message="路由前缀需以 / 开头，只能包含小写字母、数字、-、_、/")
    editable = {column.name for column in schema.columns if column.editable}
    selected = tuple(dict.fromkeys(config.fields)) if config.fields else tuple(
        column.name for column in schema.columns if column.editable
    )
    if not selected:
        raise AppException(code=400, message="该表没有可编辑字段（除主键与时间戳外）")
    unknown = [name for name in selected if name not in editable]
    if unknown:
        raise AppException(code=400, message=f"字段不可编辑或不存在：{', '.join(unknown)}")
    conflicting = [name for name in selected if name in RESERVED_EDITABLE]
    if conflicting:
        raise AppException(
            code=400,
            message=f"列名与生成的函数参数冲突，暂不支持：{', '.join(conflicting)}",
        )
    warnings: list[str] = []
    if not any(
        column.name in selected and column.py_type == "str" for column in schema.columns
    ):
        warnings.append("所选字段中没有字符串列，列表接口的 keyword 搜索不会生效")
    if schema.uniques:
        names = "、".join("+".join(item) for item in schema.uniques)
        warnings.append(f"表存在唯一约束（{names}），生成的 Service 未自动做重复校验，请按业务补充")
    return (
        CodegenConfig(
            table=schema.name,
            module=module,
            class_name=class_name,
            title=title,
            route_prefix=route_prefix.rstrip("/") or "/",
            fields=selected,
        ),
        warnings,
    )


def _column_context(column: ColumnSchema, selected: bool) -> dict:
    py_type = column.py_type
    nullable = column.nullable
    annotation = py_type
    if nullable and py_type != "Any":
        annotation = f"{py_type} | None"

    is_timestamp = column.name in TIMESTAMP_FIELDS
    args = [column.sa_type]
    pk = column.primary
    random_id = pk and column.name == "id" and column.sa_type.startswith("BigInteger")
    if pk:
        args.append("primary_key=True")
        if random_id:
            args.extend(["autoincrement=False", "default=generate_id"])
    elif is_timestamp:
        args.extend(["server_default=func.now()", "nullable=False"])
        if column.name == "updated_at":
            args.append("onupdate=func.now()")
    else:
        args.append(f"nullable={nullable}")
    if not pk and column.unique:
        args.append("unique=True")
    if not pk and column.indexed:
        args.append("index=True")
    if column.comment:
        args.append(f"comment={column.comment!r}")

    default_expr = None if (pk or is_timestamp) else _default_expr(column)
    required = not pk and not nullable and default_expr is None

    create_annotation = py_type
    create_default = "None"
    if required:
        create_default = ""
    else:
        if nullable and py_type != "Any":
            create_annotation = f"{py_type} | None"
        if default_expr is not None and not nullable:
            create_annotation = py_type
        create_default = default_expr or "None"

    return {
        "name": column.name,
        "py_type": py_type,
        "mapped_annotation": annotation,
        "mapped_args": ", ".join(args),
        "create_annotation": create_annotation,
        "create_default": create_default,
        "update_annotation": "Any" if py_type == "Any" else f"{py_type} | None",
        "required": required,
        "selected": selected,
        "unique": column.unique,
    }


def _py_import_lines(columns: list[ColumnSchema]) -> list[str]:
    lines: list[str] = []
    if any(column.py_type in {"datetime", "date", "time"} for column in columns):
        names = sorted({column.py_type for column in columns if column.py_type in {"datetime", "date", "time"}})
        lines.append(f"from datetime import {', '.join(names)}")
    if any(column.py_type == "Decimal" for column in columns):
        lines.append("from decimal import Decimal")
    if any(column.py_type == "Any" for column in columns):
        lines.append("from typing import Any")
    return lines


def _render(schema: TableSchema, config: CodegenConfig) -> list[dict]:
    columns = list(schema.columns)
    selected = set(config.fields)
    column_contexts = [_column_context(column, column.name in selected) for column in columns]
    editable_contexts = [item for item in column_contexts if item["name"] in selected]
    required = [item for item in editable_contexts if item["required"]]
    optional = [item for item in editable_contexts if not item["required"]]
    search_columns = [
        item for item in editable_contexts if item["py_type"] == "str"
    ]
    primary_columns = [column for column in schema.columns if column.primary]
    if len(primary_columns) != 1:
        raise AppException(
            code=400,
            message="该表没有主键或使用复合主键，暂不支持生成单键 CRUD 骨架",
        )
    primary_key = primary_columns[0]
    if primary_key.name in RESERVED_PRIMARY:
        raise AppException(code=400, message=f"主键名「{primary_key.name}」与生成的函数参数冲突")

    model_types = sorted(
        {column.sa_type.split("(")[0] for column in columns}
        | ({"func"} if any(column.name in TIMESTAMP_FIELDS for column in columns) else set())
        | ({"UniqueConstraint"} if any(len(item) > 1 for item in schema.uniques) else set())
    )
    composite_uniques = [item for item in schema.uniques if len(item) > 1]
    py_imports = _py_import_lines(columns)
    py_import_block = "".join(f"{line}\n" for line in py_imports)
    if py_import_block:
        py_import_block += "\n"
    create_required_block = "\n".join(
        f"    {item['name']}: {item['create_annotation']}" for item in required
    )
    create_optional_block = "\n".join(
        f"    {item['name']}: {item['create_annotation']}"
        + (f" = {item['create_default']}" if item["create_default"] else "")
        for item in optional
    )
    update_block = "\n".join(
        f"    {item['name']}: {item['update_annotation']} = None" for item in editable_contexts
    )
    row_block = "\n".join(
        f"    {item['name']}: {item['mapped_annotation']}" for item in column_contexts
    )
    service_required_block = "\n".join(
        f"    {item['name']}: {item['create_annotation']}," for item in required
    )
    service_optional_block = "\n".join(
        f"    {item['name']}: {item['create_annotation']}"
        + (f" = {item['create_default']}" if item["create_default"] else "")
        + ","
        for item in optional
    )
    service_assign_block = "\n".join(
        f"        {item['name']}={item['name']}," for item in editable_contexts
    )
    service_row_block = "\n".join(
        f'        "{item["name"]}": row.{item["name"]},' for item in column_contexts
    )
    service_allowed_block = "\n".join(
        f'        "{item["name"]}",' for item in editable_contexts
    )
    api_create_block = "\n".join(
        f"        {item['name']}=payload.{item['name']}," for item in editable_contexts
    )
    search_condition_block = ""
    if search_columns:
        conditions = "\n".join(
            f'                {config.class_name}.{item["name"]}.like(f"%{{needle}}%"),'
            for item in search_columns
        )
        search_condition_block = (
            "    needle = keyword.strip()\n"
            "    if needle:\n"
            "        stmt = stmt.where(\n"
            "            or_(\n"
            f"{conditions}\n"
            "            )\n"
            "        )"
        )
    context = {
        "schema": schema,
        "config": config,
        "columns": column_contexts,
        "editable": editable_contexts,
        "required": required,
        "optional": optional,
        "search_columns": search_columns,
        "pk": primary_key,
        "model_types": model_types,
        "composite_uniques": composite_uniques,
        "py_imports": py_imports,
        "py_import_block": py_import_block,
        "create_required_block": create_required_block,
        "create_optional_block": create_optional_block,
        "update_block": update_block,
        "row_block": row_block,
        "service_required_block": service_required_block,
        "service_optional_block": service_optional_block,
        "service_assign_block": service_assign_block,
        "service_row_block": service_row_block,
        "service_allowed_block": service_allowed_block,
        "api_create_block": api_create_block,
        "search_condition_block": search_condition_block,
        "route_items": f"{config.route_prefix}/{{{primary_key.name}}}",
        "unique_text": "、".join("+".join(item) for item in schema.uniques),
        "table_label": f"{schema.name}（{schema.comment}）" if schema.comment else schema.name,
        "audit_prefix": config.module,
        "code_files": [
            f"backend/app/models/{config.module}.py",
            f"backend/app/schemas/{config.module}.py",
            f"backend/app/services/{config.module}_service.py",
            f"backend/app/api/admin_{config.module}.py",
        ],
    }

    files = [
        {
            "path": f"backend/app/models/{config.module}.py",
            "content": _ENV.from_string(_MODEL_TEMPLATE).render(**context),
        },
        {
            "path": f"backend/app/schemas/{config.module}.py",
            "content": _ENV.from_string(_SCHEMA_TEMPLATE).render(**context),
        },
        {
            "path": f"backend/app/services/{config.module}_service.py",
            "content": _ENV.from_string(_SERVICE_TEMPLATE).render(**context),
        },
        {
            "path": f"backend/app/api/admin_{config.module}.py",
            "content": _ENV.from_string(_API_TEMPLATE).render(**context),
        },
        {
            "path": "INTEGRATION.md",
            "content": _ENV.from_string(_INTEGRATION_TEMPLATE).render(**context),
        },
    ]
    return files


def _conflicts(files: list[dict]) -> list[str]:
    warnings = []
    for item in files:
        if item["path"] == "INTEGRATION.md":
            continue
        if (BACKEND_ROOT.parent / item["path"]).exists():
            warnings.append(f"目标文件已存在：{item['path']}（导入前请备份或改名）")
    return warnings


async def preview(
    table: str,
    *,
    module: str,
    class_name: str,
    title: str = "",
    route_prefix: str = "",
    fields: list[str] | None = None,
) -> dict:
    schema = await _load_table(table)
    config, warnings = _validate_config(
        schema,
        CodegenConfig(
            table=table,
            module=module,
            class_name=class_name,
            title=title,
            route_prefix=route_prefix,
            fields=tuple(fields or ()),
        ),
    )
    files = _render(schema, config)
    warnings = warnings + _conflicts(files)
    return {"available": True, "warnings": warnings, "files": files}


async def download(
    db: AsyncSession,
    admin: User,
    *,
    table: str,
    module: str,
    class_name: str,
    title: str = "",
    route_prefix: str = "",
    fields: list[str] | None = None,
) -> tuple[str, bytes]:
    """生成 ZIP 并写审计；返回 (文件名, 二进制内容)。"""
    schema = await _load_table(table)
    config, warnings = _validate_config(
        schema,
        CodegenConfig(
            table=table,
            module=module,
            class_name=class_name,
            title=title,
            route_prefix=route_prefix,
            fields=tuple(fields or ()),
        ),
    )
    files = _render(schema, config)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in files:
            archive.writestr(item["path"], item["content"])
    filename = f"{config.module}-codegen.zip"
    record_admin_action(
        db,
        admin,
        "codegen_download",
        target_type="codegen",
        summary=f"下载代码生成包「{config.title}」（表 {schema.name}，{len(files)} 个文件）",
        detail={
            "table": schema.name,
            "module": config.module,
            "class_name": config.class_name,
            "route_prefix": config.route_prefix,
            "files": [item["path"] for item in files],
            "warnings": warnings,
        },
    )
    await db.commit()
    return filename, buffer.getvalue()


_MODEL_TEMPLATE = '''"""{{ config.title }}（表 `{{ schema.name }}`）模型骨架，由代码生成器产出。"""

{{ py_import_block }}from sqlalchemy import {{ model_types | join(', ') }}
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base{% if pk.name == 'id' and pk.sa_type.startswith('BigInteger') %}, generate_id{% endif %}


class {{ config.class_name }}(Base):
    """{{ schema.comment or config.title }}"""

    __tablename__ = "{{ schema.name }}"
{% if composite_uniques %}

    __table_args__ = (
{% for item in composite_uniques %}        UniqueConstraint({% for name in item %}"{{ name }}"{% if not loop.last %}, {% endif %}{% endfor %}),
{% endfor %}    )
{% endif %}
{% for column in columns %}
    {{ column.name }}: Mapped[{{ column.mapped_annotation }}] = mapped_column({{ column.mapped_args }})
{% endfor %}
'''

_SCHEMA_TEMPLATE = '''"""{{ config.title }}请求 / 响应模型骨架，由代码生成器产出。"""

{{ py_import_block }}from pydantic import BaseModel


class {{ config.class_name }}Create(BaseModel):
{{ create_required_block }}
{{ create_optional_block }}

class {{ config.class_name }}Update(BaseModel):
{{ update_block }}

class {{ config.class_name }}Row(BaseModel):
{{ row_block }}

class {{ config.class_name }}ListData(BaseModel):
    items: list[{{ config.class_name }}Row]
'''

_SERVICE_TEMPLATE = '''"""{{ config.title }}（表 `{{ schema.name }}`）CRUD 骨架，由代码生成器产出。

接入前请阅读同批生成的 INTEGRATION.md：唯一约束、审计动作码、路由规则都需要人工补齐。
"""

from __future__ import annotations

from sqlalchemy import {% if search_columns %}or_, {% endif %}select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.{{ config.module }} import {{ config.class_name }}
from app.models.user import User
from app.services.admin_audit_service import record_admin_action


def _row(row: {{ config.class_name }}) -> dict:
    return {
{{ service_row_block }}
    }


async def _get(db: AsyncSession, {{ pk.name }}: int) -> {{ config.class_name }}:
    result = await db.execute(
        select({{ config.class_name }}).where({{ config.class_name }}.{{ pk.name }} == {{ pk.name }})
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise AppException(code=404, message="{{ config.title }}不存在")
    return row


async def list_{{ config.module }}(db: AsyncSession, keyword: str = "") -> dict:
    stmt = select({{ config.class_name }})
{{ search_condition_block }}
    result = await db.execute(stmt)
    return {"items": [_row(row) for row in result.scalars().all()]}


async def create_{{ config.module }}(
    db: AsyncSession,
    admin: User,
    *,
{{ service_required_block }}
{{ service_optional_block }}
) -> dict:
    row = {{ config.class_name }}(
{{ service_assign_block }}
    )
    db.add(row)
    await db.flush()
    record_admin_action(
        db,
        admin,
        "{{ config.module }}_create",
        target_type="{{ config.module }}",
        target_id=row.{{ pk.name }},
        summary="新建{{ config.title }}",
    )
    await db.commit()
    return _row(row)


async def update_{{ config.module }}(
    db: AsyncSession,
    admin: User,
    {{ pk.name }}: int,
    changes: dict,
) -> dict:
    row = await _get(db, {{ pk.name }})
    allowed = {
{{ service_allowed_block }}
    }
    unknown = set(changes) - allowed
    if unknown:
        raise AppException(code=400, message=f"不支持的字段：{sorted(unknown)}")
    for field, value in changes.items():
        setattr(row, field, value)
    await db.flush()
    record_admin_action(
        db,
        admin,
        "{{ config.module }}_update",
        target_type="{{ config.module }}",
        target_id=row.{{ pk.name }},
        summary="编辑{{ config.title }}",
    )
    await db.commit()
    return _row(row)


async def delete_{{ config.module }}(db: AsyncSession, admin: User, {{ pk.name }}: int) -> None:
    row = await _get(db, {{ pk.name }})
    await db.delete(row)
    await db.flush()
    record_admin_action(
        db,
        admin,
        "{{ config.module }}_delete",
        target_type="{{ config.module }}",
        target_id={{ pk.name }},
        summary="删除{{ config.title }}",
    )
    await db.commit()
'''

_API_TEMPLATE = '''"""{{ config.title }}管理接口骨架，由代码生成器产出。接入前请先登记 core/rbac.py 路由规则。"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.admin import ActionAck
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.{{ config.module }} import (
    {{ config.class_name }}Create,
    {{ config.class_name }}ListData,
    {{ config.class_name }}Row,
    {{ config.class_name }}Update,
)
from app.services import {{ config.module }}_service


@router.get("{{ config.route_prefix }}", response_model=ApiResponse[{{ config.class_name }}ListData])
async def list_{{ config.module }}(
    keyword: str = Query("", max_length=KEYWORD_MAX_LENGTH),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return success_response(await {{ config.module }}_service.list_{{ config.module }}(db, keyword))


@router.post("{{ config.route_prefix }}", response_model=ApiResponse[{{ config.class_name }}Row])
async def create_{{ config.module }}(
    payload: {{ config.class_name }}Create,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    row = await {{ config.module }}_service.create_{{ config.module }}(
        db,
        admin,
{{ api_create_block }}
    )
    return success_response(row)


@router.put("{{ route_items }}", response_model=ApiResponse[{{ config.class_name }}Row])
async def update_{{ config.module }}(
    {{ pk.name }}: int,
    payload: {{ config.class_name }}Update,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    changes = {field: getattr(payload, field) for field in payload.model_fields_set}
    row = await {{ config.module }}_service.update_{{ config.module }}(db, admin, {{ pk.name }}, changes)
    return success_response(row)


@router.delete("{{ route_items }}", response_model=ApiResponse[ActionAck])
async def delete_{{ config.module }}(
    {{ pk.name }}: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    await {{ config.module }}_service.delete_{{ config.module }}(db, admin, {{ pk.name }})
    return success_response({"ok": True})
'''

_INTEGRATION_TEMPLATE = '''# {{ config.title }} 代码生成结果

- 来源表：`{{ table_label }}`
- 模块名：`{{ config.module }}`
- 类名：`{{ config.class_name }}`
- 路由前缀：`{{ config.route_prefix }}`

## 文件

{% for file in code_files %}- `{{ file }}`
{% endfor %}

## 接入步骤

1. 把上面 4 个文件复制到仓库对应路径。
2. 在 `backend/app/api/admin.py` 的 import 列表中加入 `admin_{{ config.module }}`。
3. 在 `backend/app/core/rbac.py` 的 `_RULES` 中登记（未登记会被 fail-closed 拦成 403）：

```python
("GET", "/api/v1/admin{{ config.route_prefix }}", ["setting:view"]),
("POST", "/api/v1/admin{{ config.route_prefix }}", ["setting:update"]),
("PUT", "/api/v1/admin{{ route_items }}", ["setting:update"]),
("DELETE", "/api/v1/admin{{ route_items }}", ["setting:update"]),
```

4. 在 `backend/app/models/admin_audit_log.py` 登记三个动作码、目标类型与风险等级：

```python
# AUDIT_ACTIONS
"{{ config.module }}_create": "新建{{ config.title }}",
"{{ config.module }}_update": "编辑{{ config.title }}",
"{{ config.module }}_delete": "删除{{ config.title }}",
# AUDIT_TARGET_TYPES / AUDIT_TARGET_TYPE_LABELS
"{{ config.module }}": "{{ config.title }}",
# AUDIT_ACTION_RISK
"{{ config.module }}_create": AUDIT_RISK_NORMAL,
"{{ config.module }}_update": AUDIT_RISK_NORMAL,
"{{ config.module }}_delete": AUDIT_RISK_DANGER,
```

5. 可选：在 `backend/app/services/admin_menu.py` 增加菜单项，并为 `MODULES` 登记路径前缀与菜单 key。

## 生成边界

- 代码生成器只读表结构，不会建表、不会改库；生成物是**骨架**，下载后由人决定是否接入。
- 唯一约束未自动生成重复校验：{% if schema.uniques %}本表存在 `{{ unique_text }}`，请按业务补 400 提示。{% else %}本表未发现唯一约束。{% endif %}


- 字段类型映射：未知类型会回退为 `Text`，请在模型里核对。
- 前端页面未生成：本项目前端列表页与字段展示强相关，请参考 `admin-frontend/src/pages/Positions` 手写页面与权限守卫。
'''
