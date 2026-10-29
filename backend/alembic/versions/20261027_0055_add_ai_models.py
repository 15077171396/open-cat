"""AI 模型管理：ai_models 表 + 旧配置迁移 + 权限补种

Revision ID: add_ai_models
Revises: seed_scheduler_permission
Create Date: 2026-10-27

背景：
    模型/凭据/单价原先散落在 system_settings 的固定键里（deepseek/openai/qwen
    各一组），无法支持同一供应商多模型，也让「系统设置」承担了模型目录的角色。
    本次迁到独立 ai_models 表，由「系统 → 模型管理」维护。

迁移策略（零行为变更）：
    - 三条种子记录的 code 直接取供应商名（deepseek/openai/qwen），升级前
      工具 ai_config.model 里的旧值继续命中；
    - 旧 Key 密文原样复制（仍是 encrypt_secret 产物，运行时可解）；
    - 旧库里**没有显式配置**的字段留空，运行时继续逐项回退供应商 .env；
    - 旧 ai_model_prices 拆进各模型单价；旧 ai_default_model 转成 is_default；
    - 迁移完成后删除已迁走的旧键，只保留全站成本护栏 4 项。
"""

import json
import secrets
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.config import settings
from app.services.rbac_catalog import PERMISSION_CATALOG, SYSTEM_ROLES

revision: str = "add_ai_models"
down_revision: Union[str, None] = "seed_scheduler_permission"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROVIDERS = (
    ("deepseek", "DeepSeek", 10),
    ("openai", "OpenAI", 20),
    ("qwen", "通义千问", 30),
)
_MOVED_SETTING_KEYS = (
    "ai_default_model",
    "ai_deepseek_api_key",
    "ai_deepseek_base_url",
    "ai_deepseek_model",
    "ai_openai_api_key",
    "ai_openai_base_url",
    "ai_openai_model",
    "ai_qwen_api_key",
    "ai_qwen_base_url",
    "ai_qwen_model",
    "ai_model_prices",
)
_NEW_PERMISSION_CODES = (
    "ai_model:view",
    "ai_model:create",
    "ai_model:edit",
    "ai_model:delete",
)


def _gen_id() -> int:
    return secrets.randbelow(9_000_000_000) + 1_000_000_000


def _read_settings(conn) -> dict[str, str]:
    rows = conn.execute(
        sa.text("SELECT setting_key, value FROM system_settings WHERE value IS NOT NULL")
    ).all()
    return {row[0]: row[1] for row in rows}


def _parse_prices(raw: str | None) -> dict[str, tuple[float, float]]:
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    table: dict[str, tuple[float, float]] = {}
    for name, price in data.items():
        if not isinstance(price, dict):
            continue
        try:
            table[str(name)] = (
                float(price.get("input") or 0),
                float(price.get("output") or 0),
            )
        except (TypeError, ValueError):
            continue
    return table


def _seed_models(conn) -> None:
    stored = _read_settings(conn)
    default_provider = (
        stored.get("ai_default_model") or settings.DEFAULT_AI_MODEL or "deepseek"
    ).strip()
    prices = _parse_prices(stored.get("ai_model_prices") or settings.AI_MODEL_PRICES)

    for provider, display_name, sort_order in _PROVIDERS:
        prefix = f"ai_{provider}"
        input_price, output_price = prices.get(provider, (0.0, 0.0))
        conn.execute(
            sa.text(
                "INSERT INTO ai_models ("
                "id, code, display_name, provider, model_name, base_url, api_key_enc, "
                "input_price, output_price, is_enabled, is_default, sort_order, remark"
                ") VALUES ("
                ":id, :code, :display_name, :provider, :model_name, :base_url, :api_key_enc, "
                ":input_price, :output_price, 1, :is_default, :sort_order, NULL"
                ")"
            ).bindparams(
                id=_gen_id(),
                code=provider,
                display_name=display_name,
                provider=provider,
                model_name=stored.get(f"{prefix}_model") or "",
                base_url=stored.get(f"{prefix}_base_url") or "",
                api_key_enc=stored.get(f"{prefix}_api_key") or "",
                input_price=input_price,
                output_price=output_price,
                is_default=1 if provider == default_provider else 0,
                sort_order=sort_order,
            )
        )

    # 旧 ai_default_model 指向了未注册供应商时，保证仍有一条默认记录
    if not conn.execute(
        sa.text("SELECT 1 FROM ai_models WHERE is_default = 1 LIMIT 1")
    ).first():
        conn.execute(sa.text("UPDATE ai_models SET is_default = 1 ORDER BY sort_order LIMIT 1"))

    params = {f"k{index}": key for index, key in enumerate(_MOVED_SETTING_KEYS)}
    placeholders = ", ".join(f":{name}" for name in params)
    conn.execute(
        sa.text(f"DELETE FROM system_settings WHERE setting_key IN ({placeholders})"),
        params,
    )


def _seed_permissions(conn) -> None:
    existing = {row.code for row in conn.execute(sa.text("SELECT code FROM permissions"))}
    for p in PERMISSION_CATALOG:
        if p.code in existing:
            conn.execute(
                sa.text(
                    "UPDATE permissions SET name=:name, type=:type, module=:module, "
                    "module_name=:module_name, sort=:sort WHERE code=:code"
                ).bindparams(
                    name=p.name,
                    type=p.type,
                    module=p.module,
                    module_name=p.module_name,
                    sort=p.sort,
                    code=p.code,
                )
            )
        else:
            conn.execute(
                sa.text(
                    "INSERT INTO permissions (id, code, name, type, module, module_name, sort) "
                    "VALUES (:id, :code, :name, :type, :module, :module_name, :sort)"
                ).bindparams(
                    id=_gen_id(),
                    code=p.code,
                    name=p.name,
                    type=p.type,
                    module=p.module,
                    module_name=p.module_name,
                    sort=p.sort,
                )
            )

    code_to_perm_id = {
        row.code: row.id for row in conn.execute(sa.text("SELECT id, code FROM permissions"))
    }
    code_to_role_id = {
        row.code: row.id for row in conn.execute(sa.text("SELECT id, code FROM roles"))
    }
    for role in SYSTEM_ROLES:
        role_id = code_to_role_id.get(role["code"])
        if role_id is None:
            continue
        granted = {
            row.code
            for row in conn.execute(
                sa.text(
                    "SELECT p.code FROM role_permissions rp "
                    "JOIN permissions p ON p.id = rp.permission_id "
                    "WHERE rp.role_id = :rid"
                ).bindparams(rid=role_id)
            )
        }
        for code in role["permissions"]:
            if code in granted:
                continue
            perm_id = code_to_perm_id.get(code)
            if perm_id is None:
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_id) VALUES (:rid, :pid)"
                ).bindparams(rid=role_id, pid=perm_id)
            )


def upgrade() -> None:
    op.create_table(
        "ai_models",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False, comment="主键"),
        sa.Column("code", sa.String(length=64), nullable=False, comment="稳定标识（工具/翻译引用），创建后不可改"),
        sa.Column("display_name", sa.String(length=100), nullable=False, comment="后台与看板展示名"),
        sa.Column("provider", sa.String(length=30), nullable=False, comment="协议实现名（必须来自 Provider 注册表）"),
        sa.Column("model_name", sa.String(length=100), server_default="", nullable=False, comment="上游模型名，空=回退供应商 .env"),
        sa.Column("base_url", sa.String(length=500), server_default="", nullable=False, comment="接口地址，空=回退供应商 .env"),
        sa.Column("api_key_enc", sa.String(length=1000), server_default="", nullable=False, comment="加密后的 API Key，空=回退供应商 .env"),
        sa.Column("input_price", sa.Numeric(12, 6), server_default="0", nullable=False, comment="输入单价（元/百万 tokens，0=回退兜底单价）"),
        sa.Column("output_price", sa.Numeric(12, 6), server_default="0", nullable=False, comment="输出单价（元/百万 tokens，0=回退兜底单价）"),
        sa.Column("is_enabled", sa.Boolean(), server_default="1", nullable=False, comment="是否启用"),
        sa.Column("is_default", sa.Boolean(), server_default="0", nullable=False, comment="是否全局默认（最多一条）"),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False, comment="排序（越小越靠前）"),
        sa.Column("remark", sa.String(length=500), nullable=True, comment="备注"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="更新时间"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_models_code", "ai_models", ["code"], unique=True)
    op.create_index("ix_ai_models_provider", "ai_models", ["provider"], unique=False)
    op.create_index("ix_ai_models_is_enabled", "ai_models", ["is_enabled"], unique=False)
    op.create_index("ix_ai_models_is_default", "ai_models", ["is_default"], unique=False)
    op.create_index("ix_ai_models_updated_at", "ai_models", ["updated_at"], unique=False)

    conn = op.get_bind()
    _seed_models(conn)
    _seed_permissions(conn)


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT code, provider, model_name, base_url, api_key_enc, input_price, output_price, "
            "is_default, sort_order FROM ai_models ORDER BY sort_order, id"
        )
    ).all()

    # 尽力恢复旧键：每个供应商取默认/首条模型
    by_provider: dict[str, object] = {}
    default_provider = ""
    for row in rows:
        provider = row.provider
        if provider not in by_provider:
            by_provider[provider] = row
        if row.is_default and not default_provider:
            default_provider = provider

    for provider, row in by_provider.items():
        for key, value in (
            (f"ai_{provider}_api_key", row.api_key_enc or ""),
            (f"ai_{provider}_base_url", row.base_url or ""),
            (f"ai_{provider}_model", row.model_name or ""),
        ):
            if not value:
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO system_settings (setting_key, value) VALUES (:key, :value) "
                    "ON DUPLICATE KEY UPDATE value = VALUES(value)"
                ).bindparams(key=key, value=value)
            )
    if default_provider:
        conn.execute(
            sa.text(
                "INSERT INTO system_settings (setting_key, value) VALUES ('ai_default_model', :value) "
                "ON DUPLICATE KEY UPDATE value = VALUES(value)"
            ).bindparams(value=default_provider)
        )

    prices = {}
    for row in rows:
        try:
            input_price = float(row.input_price or 0)
            output_price = float(row.output_price or 0)
        except (TypeError, ValueError):
            continue
        if input_price > 0 or output_price > 0:
            prices[row.code] = {"input": input_price, "output": output_price}
    if prices:
        conn.execute(
            sa.text(
                "INSERT INTO system_settings (setting_key, value) VALUES ('ai_model_prices', :value) "
                "ON DUPLICATE KEY UPDATE value = VALUES(value)"
            ).bindparams(value=json.dumps(prices, ensure_ascii=False, sort_keys=True))
        )

    # 回收本迁移新增的权限
    for code in _NEW_PERMISSION_CODES:
        perm = conn.execute(
            sa.text("SELECT id FROM permissions WHERE code = :code").bindparams(code=code)
        ).first()
        if perm is None:
            continue
        conn.execute(
            sa.text("DELETE FROM role_permissions WHERE permission_id = :pid").bindparams(
                pid=perm.id
            )
        )
        conn.execute(
            sa.text("DELETE FROM permissions WHERE id = :pid").bindparams(pid=perm.id)
        )

    op.drop_index("ix_ai_models_updated_at", table_name="ai_models")
    op.drop_index("ix_ai_models_is_default", table_name="ai_models")
    op.drop_index("ix_ai_models_is_enabled", table_name="ai_models")
    op.drop_index("ix_ai_models_provider", table_name="ai_models")
    op.drop_index("ix_ai_models_code", table_name="ai_models")
    op.drop_table("ai_models")
