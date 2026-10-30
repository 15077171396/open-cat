"""清理下线内容模块遗留：drop 7 张表 + 删除非 tutorial 历史分类

Revision ID: drop_legacy_content_tables
Revises: add_category_groups
Create Date: 2026-10-30

背景
----
Phase A 已把「资源 / 节点 / 扩展(插件) / 在线工具」四个领域从代码里彻底移除
（model / service / api / 权限 / 菜单 / 路由 / 种子全部删除，content_types 只剩 tutorial），
但数据库里仍留着它们的结构与历史数据：

    nodes / plugins / tools / resources                          -- 4 张主表
    plugin_sources / resource_netdisk_links / tool_usage_logs     -- 3 张从属表
    categories 里 module in (node, plugin, resource, tool) 的历史分类

（行数因库而异：参考库 ai_cat 里 7 张表共 79 行、历史分类 21 行；
 真环境为 open_cat，见下方「数据说明」。）

从属表均无 ORM 模型（Phase A 已删），且 plugin_sources -> plugins、
resource_netdisk_links -> resources 有外键，必须一并清理，否则 drop 主表会被外键挡住。
downloads 表虽含 resource_id 但**仍有 ORM 模型**（models/download.py）且无外键，保留；
tutorial_netdisk_links / tutorial_nodes 属于教程模块，保留。

数据说明
--------
drop 表不可逆：执行前已把 7 张表全部行 + 非 tutorial 历史分类导出到
.workbuddy/tmp/legacy_tables_backup.sql（不进版本库，仅本地兜底）。
downgrade 会重建**与 drop 前一致的空表结构**（含索引 / 全文索引 / 外键），
但不恢复被删的数据行 —— 它们属于已下线模块，恢复反而会让新库变脏；
需要数据请用上面的备份文件手工恢复。

往返验证
--------
upgrade -> downgrade -> upgrade 已验证：downgrade 重建的表可被再次 upgrade 正常 drop。
删除分类用**明确白名单**而非 `module <> 'tutorial'`：
未来新增第二种内容类型（如 webinar）时重跑本迁移不会误删它的分类。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "drop_legacy_content_tables"
down_revision: Union[str, None] = "add_category_groups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 下线模块键：只清理这四个，不碰 tutorial 与未来新增类型
_LEGACY_MODULES = ("node", "plugin", "resource", "tool")

# upgrade: 从属表（外键子表）先 drop，主表后 drop
_DROP_ORDER = ['tool_usage_logs', 'plugin_sources', 'resource_netdisk_links', 'nodes', 'tools', 'plugins', 'resources']

# downgrade: 主表先建（子表 DDL 内联 FOREIGN KEY 引用主表），从属表后建
_CREATE_DDLS = [
    '''
CREATE TABLE `resources` (
  `id` bigint NOT NULL,
  `name` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '资源名称',
  `description` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '简介',
  `resource_type` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'tool/model/source/doc/other',
  `file_size` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '显示大小(如12.5GB)',
  `content_list` json DEFAULT NULL COMMENT '包含内容列表',
  `usage_notes` json DEFAULT NULL COMMENT '使用说明',
  `download_count` int NOT NULL COMMENT '下载次数',
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `created_at` datetime NOT NULL DEFAULT (now()),
  `view_count` int NOT NULL DEFAULT '0' COMMENT '浏览次数',
  `content` text COLLATE utf8mb4_unicode_ci COMMENT '正文内容(Markdown/HTML)',
  `is_featured` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否精选推荐',
  `cover_image` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '封面图URL',
  `tags` json DEFAULT NULL COMMENT '资源标签',
  `is_pinned` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否置顶',
  `seo_title` varchar(200) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'SEO标题',
  `seo_description` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'SEO描述',
  `seo_keywords` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'SEO关键词(逗号分隔)',
  `author_name` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '发布作者',
  `author_avatar_url` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '作者头像URL',
  `author_id` bigint DEFAULT NULL COMMENT '发布作者用户ID',
  `is_published` tinyint(1) NOT NULL DEFAULT '1' COMMENT '是否已发布（0=草稿，前台不可见）',
  `deleted_at` datetime DEFAULT NULL COMMENT '软删除时间(回收站, NULL=正常)',
  PRIMARY KEY (`id`),
  KEY `ix_resources_resource_type` (`resource_type`),
  KEY `ix_resources_pin_featured_updated` (`is_pinned`,`is_featured`,`updated_at`),
  KEY `ix_resources_updated_at` (`updated_at`),
  KEY `ix_resources_pin_feat_download` (`is_pinned`,`is_featured`,`download_count`),
  KEY `ix_resources_author_id` (`author_id`),
  KEY `ix_resources_published_pinned_featured_updated` (`is_published`,`is_pinned`,`is_featured`,`updated_at`),
  KEY `ix_resources_published_pinned_featured_downloads` (`is_published`,`is_pinned`,`is_featured`,`download_count`),
  KEY `ix_resources_pub_pin_feat_dl_updated` (`is_published`,`is_pinned`,`is_featured`,`download_count`,`updated_at`),
  KEY `ix_resources_deleted_at` (`deleted_at`),
  FULLTEXT KEY `ft_resources_search` (`name`,`description`,`content`) /*!50100 WITH PARSER `ngram` */ 
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    ''',
    '''
CREATE TABLE `plugins` (
  `id` bigint NOT NULL,
  `name` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '插件名称',
  `category` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'comfyui/coze/other',
  `description` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '简介',
  `features` json DEFAULT NULL COMMENT '核心功能列表',
  `install_steps` json DEFAULT NULL COMMENT '安装步骤',
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `created_at` datetime NOT NULL DEFAULT (now()),
  `view_count` int NOT NULL DEFAULT '0' COMMENT '浏览次数',
  `content` mediumtext COLLATE utf8mb4_unicode_ci COMMENT '正文内容(Markdown/HTML)',
  `seo_title` varchar(200) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'SEO标题',
  `seo_description` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'SEO描述',
  `seo_keywords` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'SEO关键词(逗号分隔)',
  `author_name` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '发布作者',
  `author_avatar_url` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '作者头像URL',
  `author_id` bigint DEFAULT NULL COMMENT '发布作者用户ID',
  `is_published` tinyint(1) NOT NULL DEFAULT '1' COMMENT '是否已发布（0=草稿，前台不可见）',
  PRIMARY KEY (`id`),
  KEY `ix_plugins_category` (`category`),
  KEY `ix_plugins_created_at` (`created_at`),
  KEY `ix_plugins_view_count` (`view_count`,`id`),
  KEY `ix_plugins_updated_at` (`updated_at`),
  KEY `ix_plugins_author_id` (`author_id`),
  KEY `ix_plugins_published_created` (`is_published`,`created_at`),
  KEY `ix_plugins_published_views` (`is_published`,`view_count`),
  FULLTEXT KEY `ft_plugins_search` (`name`,`description`,`content`) /*!50100 WITH PARSER `ngram` */ 
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    ''',
    '''
CREATE TABLE `tools` (
  `id` bigint NOT NULL,
  `name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '工具名称',
  `description` varchar(500) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '简介',
  `category` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'prompt/image/comfyui/calc',
  `tool_type` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'ai/frontend',
  `ai_config` json DEFAULT NULL COMMENT 'AI模型配置(prompt模板/模型/参数)',
  `form_schema` json DEFAULT NULL COMMENT '表单字段定义',
  `is_published` tinyint(1) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `usage_count` int NOT NULL DEFAULT '0' COMMENT '使用次数',
  `access_mode` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'auth' COMMENT '使用权限 free/auth/points',
  `points_cost` int NOT NULL DEFAULT '1' COMMENT '积分模式下每次运行消耗的积分',
  PRIMARY KEY (`id`),
  KEY `ix_tools_category` (`category`),
  KEY `ix_tools_is_published` (`is_published`),
  KEY `ix_tools_access_mode` (`access_mode`),
  KEY `ix_tools_updated_at` (`updated_at`),
  KEY `ix_tools_published_created` (`is_published`,`created_at`),
  FULLTEXT KEY `ft_tools_search` (`name`,`description`) /*!50100 WITH PARSER `ngram` */ 
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    ''',
    '''
CREATE TABLE `nodes` (
  `id` bigint NOT NULL,
  `name` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '节点名称',
  `category` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '分类',
  `description` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '描述',
  `inputs` json DEFAULT NULL COMMENT '输入参数',
  `outputs` json DEFAULT NULL COMMENT '输出参数',
  `params` json DEFAULT NULL COMMENT '参数详解',
  `examples` json DEFAULT NULL COMMENT '使用示例',
  `view_count` int NOT NULL DEFAULT '0' COMMENT '浏览次数',
  `created_at` datetime DEFAULT (now()),
  `updated_at` datetime DEFAULT (now()),
  `content` mediumtext COLLATE utf8mb4_unicode_ci COMMENT '正文内容(Markdown/HTML)',
  `seo_title` varchar(200) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'SEO标题',
  `seo_description` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'SEO描述',
  `seo_keywords` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'SEO关键词(逗号分隔)',
  `cover_image` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '封面图URL',
  `author_name` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '发布作者',
  `author_avatar_url` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '作者头像URL',
  `author_id` bigint DEFAULT NULL COMMENT '发布作者用户ID',
  `is_published` tinyint(1) NOT NULL DEFAULT '1' COMMENT '是否已发布（0=草稿，前台不可见）',
  PRIMARY KEY (`id`),
  KEY `ix_nodes_category` (`category`),
  KEY `ix_nodes_updated_at` (`updated_at`),
  KEY `ix_nodes_view_count` (`view_count`),
  KEY `ix_nodes_author_id` (`author_id`),
  KEY `ix_nodes_published_updated` (`is_published`,`updated_at`),
  KEY `ix_nodes_published_views` (`is_published`,`view_count`),
  FULLTEXT KEY `ft_nodes_search` (`name`,`description`,`content`) /*!50100 WITH PARSER `ngram` */ 
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    ''',
    '''
CREATE TABLE `resource_netdisk_links` (
  `id` bigint NOT NULL,
  `resource_id` bigint NOT NULL COMMENT '资源ID',
  `platform` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'baidu/quark/mobile/aliyun/github/official/other',
  `url` varchar(1000) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '网盘链接',
  `extract_code` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '提取码',
  `sort_order` int NOT NULL COMMENT '排序权重',
  PRIMARY KEY (`id`),
  KEY `ix_resource_netdisk_links_resource_id` (`resource_id`),
  CONSTRAINT `resource_netdisk_links_ibfk_1` FOREIGN KEY (`resource_id`) REFERENCES `resources` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    ''',
    '''
CREATE TABLE `plugin_sources` (
  `id` bigint NOT NULL,
  `plugin_id` bigint NOT NULL,
  `source_name` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'github/quark/baidu/mobile/official',
  `url` varchar(1000) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '链接',
  `extract_code` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_plugin_sources_plugin_id` (`plugin_id`),
  CONSTRAINT `plugin_sources_ibfk_1` FOREIGN KEY (`plugin_id`) REFERENCES `plugins` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    ''',
    '''
CREATE TABLE `tool_usage_logs` (
  `id` bigint NOT NULL,
  `user_id` bigint NOT NULL COMMENT '用户ID',
  `tool_id` bigint NOT NULL COMMENT '工具ID',
  `duration_seconds` int NOT NULL COMMENT '本次使用时长',
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_tool_usage_logs_tool_id` (`tool_id`),
  KEY `ix_tool_usage_logs_user_id` (`user_id`),
  KEY `ix_tool_usage_logs_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    ''',
]


def upgrade() -> None:
    conn = op.get_bind()

    # 1) 历史分类 + 可能存在的历史分组：明确白名单，避免误伤未来新增内容类型
    placeholders = ", ".join(":" + m for m in _LEGACY_MODULES)
    conn.execute(
        sa.text(f"DELETE FROM categories WHERE module IN ({placeholders})"),
        {m: m for m in _LEGACY_MODULES},
    )
    # category_groups 由上一版迁移引入；若库里存过下线模块的分组，同样清掉
    conn.execute(
        sa.text(f"DELETE FROM category_groups WHERE module IN ({placeholders})"),
        {m: m for m in _LEGACY_MODULES},
    )

    # 2) drop 表：顺序见 _DROP_ORDER（先子后主，MySQL 才不会被外键挡住）
    for table in _DROP_ORDER:
        op.drop_table(table)


def downgrade() -> None:
    # 重建空表结构（数据不恢复，见模块 docstring）
    for ddl in _CREATE_DDLS:
        op.execute(ddl)
