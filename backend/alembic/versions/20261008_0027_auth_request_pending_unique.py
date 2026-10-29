"""认证申请：并发提交唯一约束 (P1-8)

Revision ID: auth_request_pending_unique
Revises: seed_moderation_permissions
Create Date: 2026-10-08

背景：
    ``submit_auth_request`` 原为「先查 pending → 无则插入」。两个并发请求可以同时
    查到「无 pending」后各自插入，产生同一用户多条待审核申请——管理端审核队列出现
    重复条目，管理员通过其中一条后，其余仍挂在队列里，且用户侧 ``pending_auth_code``
    被后提交的值覆盖，状态自相矛盾。

做法：
    新增 ``pending_key`` 列（pending 时填 'pending'，终态置 NULL），配合
    (user_id, pending_key) 唯一约束。MySQL 唯一索引中 **NULL 不参与冲突判定**，
    因此天然实现「同一用户最多一条 pending，历史终态记录条数不限」。

⚠️ 建索引前必须先去重：
    存量数据里若已存在同一用户多条 pending（正是历史上并发钻出来的），
    直接建唯一索引会失败。这里保留 id 最大的一条为 pending，其余置为 rejected
    并写明合并原因（终态记录保留，供统计与审计；同时避免它们继续占据审核队列）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'auth_request_pending_unique'
down_revision: Union[str, None] = 'seed_moderation_permissions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 一次性合并重复 pending 时的驳回原因（写入 reject_reason，便于事后追溯）
_MERGE_REASON = "系统自动合并：同一用户仅保留一条待审核申请"

# 分批大小，避免 id 列表过长拼出超长 SQL
_BATCH = 500


def _dedupe_pending_pending_rows() -> int:
    """把同一用户的多条 pending 合并为一条（保留 id 最大的），返回被合并条数。

    合并掉的行置为 rejected 而非删除：认证申请是审计材料，且删除会让
    「用户提交过几次」这一事实凭空消失。
    """
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, user_id FROM auth_requests WHERE status = 'pending' ORDER BY user_id, id DESC")
    ).fetchall()

    seen: set = set()
    dup_ids = []
    for row_id, user_id in rows:
        if user_id in seen:
            dup_ids.append(row_id)
        else:
            seen.add(user_id)

    for start in range(0, len(dup_ids), _BATCH):
        chunk = dup_ids[start:start + _BATCH]
        conn.execute(
            sa.text(
                "UPDATE auth_requests SET status = 'rejected', "
                "reject_reason = :reason, reviewed_at = NOW() WHERE id IN :ids"
            ).bindparams(
                sa.bindparam("ids", value=chunk, expanding=True),
                reason=_MERGE_REASON,
            )
        )
    return len(dup_ids)


def upgrade() -> None:
    op.add_column(
        'auth_requests',
        sa.Column('pending_key', sa.String(length=20), nullable=True,
                  comment="待审核唯一键: pending 时填 'pending'，终态为 NULL"),
    )

    merged = _dedupe_pending_pending_rows()
    if merged:
        # migration 里不引 loguru（避免为数据修复引入应用依赖），用 print 供运维留痕
        print(f"[auth_request_pending_unique] 合并重复待审核申请 {merged} 条（保留最新一条）")

    op.execute("UPDATE auth_requests SET pending_key = 'pending' WHERE status = 'pending'")

    op.create_unique_constraint(
        'uq_auth_request_pending', 'auth_requests', ['user_id', 'pending_key'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_auth_request_pending', 'auth_requests', type_='unique')
    op.drop_column('auth_requests', 'pending_key')
