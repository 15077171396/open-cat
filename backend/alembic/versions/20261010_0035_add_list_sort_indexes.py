"""补齐列表排序与统计过滤缺失的索引

Revision ID: add_list_sort_indexes
Revises: add_admin_audit_log
Create Date: 2026-10-10

背景（全项目代码审查 2026-10-10，P1-1 / P1-2）：
    多个**公开列表接口**按「置顶 > 推荐 > 时间/热度」排序，但这些排序键
    在对应表上没有任何索引 → 每次翻页都走 filesort；`tool_usage_logs` 更是
    每次看板刷新都对一张**单调增长**的表做全表扫描。

    已逐条核对现有迁移（`grep create_index alembic/versions/*.py`）确认下列列
    **确实无索引**。特别说明：`resources` 的
    `(is_pinned, is_featured, updated_at)` 早在迁移 `add_resource_sort_index`(0020)
    就已建立，**不在本次范围内** —— 审查初稿曾误判此处缺索引，已更正。

本迁移新增 9 个索引（每条都对应一处真实查询）：

| 表 | 索引 | 服务的查询 |
| --- | --- | --- |
| `tool_usage_logs` | `(created_at)` | `stats_service` 统计「今日工具调用数」（表随每次 AI 工具运行增长） |
| `nodes` | `(updated_at)` | `node_service` 节点列表默认排序 |
| `nodes` | `(view_count)` | `node_service` 最热/推荐排序 |
| `plugins` | `(created_at)` | `plugin_service` 最新排序 |
| `plugins` | `(install_count)` | `plugin_service` 最热排序 |
| `plugins` | `(rating, install_count)` | `plugin_service` 综合排序（默认） |
| `tutorials` | `(is_pinned, is_recommended, published_at)` | `tutorial_service` 最新排序 |
| `tutorials` | `(is_pinned, is_recommended, view_count)` | `tutorial_service` 最热排序 |
| `resources` | `(is_pinned, is_featured, download_count)` | `resource_service` 按下载量排序（0020 的索引以 updated_at 收尾，覆盖不到） |

取舍说明：
    - 这几张表都是**读多写极少**（内容由后台编辑，非用户高频写入），
      索引的写入代价可忽略，收益是每次列表请求省掉一次 filesort。
    - 单列索引名沿用 SQLAlchemy 默认命名（`ix_<表>_<列>`），
      与模型里的 `index=True` 声明**逐字一致**，避免将来 autogenerate 重复建索引。
    - **未**为 `users.created_at` 建索引：该表规模与增长速率都远低于
      `tool_usage_logs`，且看板查询已有 900s 缓存，收益不足以抵消索引成本。
"""

from typing import Sequence, Union

from alembic import op


revision: str = 'add_list_sort_indexes'
down_revision: Union[str, None] = 'add_admin_audit_log'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (索引名, 表名, 列列表)
_INDEXES: list[tuple[str, str, list[str]]] = [
    ("ix_tool_usage_logs_created_at", "tool_usage_logs", ["created_at"]),
    ("ix_nodes_updated_at", "nodes", ["updated_at"]),
    ("ix_nodes_view_count", "nodes", ["view_count"]),
    ("ix_plugins_created_at", "plugins", ["created_at"]),
    ("ix_plugins_install_count", "plugins", ["install_count"]),
    ("ix_plugins_rating_install", "plugins", ["rating", "install_count"]),
    (
        "ix_tutorials_pin_reco_published",
        "tutorials",
        ["is_pinned", "is_recommended", "published_at"],
    ),
    (
        "ix_tutorials_pin_reco_views",
        "tutorials",
        ["is_pinned", "is_recommended", "view_count"],
    ),
    (
        "ix_resources_pin_feat_download",
        "resources",
        ["is_pinned", "is_featured", "download_count"],
    ),
]


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        op.create_index(name, table, columns)


def downgrade() -> None:
    # 逆序删除：与 upgrade 严格镜像（本迁移内索引互不依赖，逆序只为对称可读）
    for name, table, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
