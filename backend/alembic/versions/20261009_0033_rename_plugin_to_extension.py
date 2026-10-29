"""插件 → 扩展 全面改名（台账 P2-2）

Revision ID: rename_plugin_to_extension
Revises: add_node_plugin_fulltext
Create Date: 2026-10-09

背景：
    产品决策把「插件推荐」这个栏目统一改名为「扩展」——它和《产品规划》6.3 里的
    「扩展模块」本来就是同一个东西，此前只是叫法没统一。界面上要彻底不再出现
    「插件」二字，**只改前端文案是不够的**，因为有两处显示文案存在数据库里：

      1. **分类名**：分类筛选按钮的文案来自 `categories` 表
         （`GET /categories?module=plugin`）。只改前端兜底常量不生效——
         接口成功时优先用接口数据，结果是「导航叫扩展、筛选按钮还叫插件」。
      2. **权限显示名**：管理端角色权限树里的「插件查看 / 插件管理」等来自
         `permissions.name` / `permissions.module_name`。

变更内容：
    1. `categories`（module='plugin'）：'Coze 插件' → 'Coze 扩展'，'其他插件' → '其他扩展'；
    2. `plugins.category`：同上两处；
    3. `permissions`：把 `module='plugin'` 的权限显示名按代码目录
       （`services/rbac_catalog.py`）刷新一遍。

⚠️ 唯一约束 `uq_category_module_name(module, name)`：
    若目标名字已存在（例如运营已手工把分类改成「Coze 扩展」），直接 UPDATE 会违反唯一约束
    并把整条迁移打挂。因此分类改名走「先探测目标是否已存在」——已存在则删除旧行
    （等价于合并，`plugins` 已指向新名），不存在才改名。两条 UPDATE 都只匹配旧名字，
    **重复执行是安全的空操作**。

⚠️ 为什么权限名要 import 应用代码的目录：
    权限目录本身就是「权限点元信息的唯一事实来源」，在迁移里抄一份名字必然漂移
    （新增权限时没人会记得同步迁移）。这与 0026 / 0030 的做法一致。
    本迁移**只刷新 module='plugin' 的条目**，不碰其它模块（避免误改运营手工调整过的名字）。

⚠️ 历史迁移 `20260731_0001_add_categories.py` 保持原样不动（迁移是历史快照）：
    全新部署时它先建出「Coze 插件」，再由本迁移改成「Coze 扩展」，最终状态一致。

⚠️ 代码标识符（表名 `plugins` / 路由 `/plugins` / 模型 `Plugin` / 权限码 `plugin:*`）
    **一律不改**：改路由会掉已收录页面（需 301），改权限码会牵动全部 RBAC 规则。
    「改名」只发生在**展示层**。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.services.rbac_catalog import PERMISSION_CATALOG


revision: str = 'rename_plugin_to_extension'
down_revision: Union[str, None] = 'add_node_plugin_fulltext'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (旧名, 新名) —— 只涉及含「插件」二字的两个分类
_RENAMES = (
    ("Coze 插件", "Coze 扩展"),
    ("其他插件", "其他扩展"),
)


def _category_exists(conn, name: str) -> bool:
    row = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM categories WHERE module = 'plugin' AND name = :n"
        ).bindparams(n=name)
    ).scalar()
    return bool(row)


def _rename_category(conn, old: str, new: str) -> None:
    # 插件行的分类值：无唯一约束，直接改（条件匹配旧名，天然幂等）
    conn.execute(
        sa.text("UPDATE plugins SET category = :new WHERE category = :old").bindparams(
            new=new, old=old
        )
    )
    # 分类表：先探测目标是否已存在，避免撞 uq_category_module_name
    if _category_exists(conn, new):
        # 目标已存在 → 删掉旧行（等价于合并，plugins 已指向新名）
        conn.execute(
            sa.text(
                "DELETE FROM categories WHERE module = 'plugin' AND name = :old"
            ).bindparams(old=old)
        )
        return
    conn.execute(
        sa.text(
            "UPDATE categories SET name = :new WHERE module = 'plugin' AND name = :old"
        ).bindparams(new=new, old=old)
    )


def _sync_plugin_permission_names(conn) -> None:
    """按代码目录刷新 plugin 模块的权限显示名（不存在则跳过，不新增权限点）。"""
    for p in PERMISSION_CATALOG:
        if p.module != "plugin":
            continue
        conn.execute(
            sa.text(
                "UPDATE permissions SET name = :name, module_name = :module_name "
                "WHERE code = :code"
            ).bindparams(name=p.name, module_name=p.module_name, code=p.code)
        )


def upgrade() -> None:
    conn = op.get_bind()
    for old, new in _RENAMES:
        _rename_category(conn, old, new)
    _sync_plugin_permission_names(conn)


def downgrade() -> None:
    conn = op.get_bind()
    for old, new in _RENAMES:
        _rename_category(conn, new, old)
    # 权限名回滚不做：目录（代码）已是新名字，回滚名字会与代码不一致，
    # 且旧名属于纯展示文案，回滚没有实际收益。
