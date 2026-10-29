"""扩展删除 platform / version / rating / install_count

Revision ID: drop_plugin_legacy_fields
Revises: add_plugin_author
Create Date: 2026-10-12

背景（用户需求）：
    扩展模块决定只保留「预览量」一个指标。此前已把安装量/评分从前后台展示中下线，
    但数据列、后台表单、排序内部依赖都还在 —— 用户要求「清除干净」，故一次性删列。

⚠️ **本迁移是破坏性的**：这四列的现有数据会随列一起消失，downgrade **找不回数据**
    （只能把列按原类型加回来，值为空）。执行前请确认已有备份
    （`scripts/backup_mysql.sh`）。
    之所以仍然删：留着它们会持续产生「后台能填但前台不显示」「排序按看不见的指标」
    这类问题，而它们已无任何消费方 —— 前端、管理端、小程序三端本次已同步移除。

同时删掉两个只为这两列存在的索引（列没了索引必须一起走）：
    - `ix_plugins_rating_install`：0035 为「综合排序」建的复合索引
    - `ix_plugins_install_count`：`install_count` 的单列索引（模型里的 index=True）

⚠️ 替代排序键 `view_count` 的索引在 `20261012_0043_add_plugin_view_count_index`，
   与本次删列**同批发版** —— 删了 0035 建的两个索引却不补新的，等于把 0035 修的
   「排序键无索引 → 每次翻页 filesort」直接回退（第五轮审查 R5-07）。

执行方式（第五轮审查 R5-09 的整改）：原实现是 2 条 drop_index + 4 条 drop_column
= **6 次独立 ALTER，即把 plugins 整表重建 6 遍**（`content` 是 MEDIUMTEXT，每遍都要
约 1 倍表大小的临时空间，而 MySQL 的 DDL 不是事务性的 —— 中途失败就停在两不管）。
现在合并成**一条** ALTER（一次重建），并给每步加存在性判断，使中断后可以原地重跑。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'drop_plugin_legacy_fields'
down_revision: Union[str, None] = 'add_plugin_author'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DROP_INDEXES = ("ix_plugins_rating_install", "ix_plugins_install_count")
DROP_COLUMNS = ("install_count", "rating", "version", "platform")


def _conn():
    return op.get_bind()


def _column_exists(table: str, column: str) -> bool:
    row = _conn().execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row is not None


def _index_exists(table: str, index: str) -> bool:
    row = _conn().execute(
        sa.text(
            "SELECT 1 FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i "
            "LIMIT 1"
        ),
        {"t": table, "i": index},
    ).first()
    return row is not None


def upgrade() -> None:
    idx = [f"DROP INDEX `{n}`" for n in DROP_INDEXES if _index_exists('plugins', n)]
    cols = [f"DROP COLUMN `{c}`" for c in DROP_COLUMNS if _column_exists('plugins', c)]
    if not idx and not cols:
        print("[drop_plugin_legacy_fields] 索引与四列均已不在，跳过（幂等重跑）")
        return
    # 一条 ALTER 里先删索引再删列：列被删时其索引必须已不在，否则 MySQL 报错
    op.execute("ALTER TABLE plugins " + ", ".join(idx + cols))
    print(f"[drop_plugin_legacy_fields] 一次重建：删 {len(idx)} 个索引 + {len(cols)} 列")


def downgrade() -> None:
    # 逆序把列加回来（类型与 4aa349fe40bb 建表时一致）。
    # ⚠️ 只恢复结构，数据已丢失。
    # `platform` / `install_count` 原本是 NOT NULL，加回时先给 server_default
    # 以兼容已有行，随后再去掉默认值，使库结构与模型定义一致。
    if not _column_exists('plugins', 'platform'):
        op.add_column(
            'plugins',
            sa.Column('platform', sa.String(length=50), nullable=False, server_default='', comment='平台'),
        )
    if not _column_exists('plugins', 'version'):
        op.add_column('plugins', sa.Column('version', sa.String(length=20), nullable=True))
    if not _column_exists('plugins', 'rating'):
        op.add_column(
            'plugins',
            sa.Column('rating', sa.DECIMAL(precision=2, scale=1), nullable=True, comment='评分'),
        )
    if not _column_exists('plugins', 'install_count'):
        op.add_column(
            'plugins',
            sa.Column('install_count', sa.Integer(), nullable=False, server_default='0', comment='安装量'),
        )
    if not _index_exists('plugins', 'ix_plugins_install_count'):
        op.create_index('ix_plugins_install_count', 'plugins', ['install_count'])
    if not _index_exists('plugins', 'ix_plugins_rating_install'):
        op.create_index('ix_plugins_rating_install', 'plugins', ['rating', 'install_count'])

    op.alter_column('plugins', 'platform', existing_type=sa.String(length=50), nullable=False, server_default=None)
    op.alter_column('plugins', 'install_count', existing_type=sa.Integer(), nullable=False, server_default=None)
