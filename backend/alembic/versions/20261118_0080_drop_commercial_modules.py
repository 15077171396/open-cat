"""通用化清理：下线商业化闭环（支付/积分/实名/邀请）与产品特定模块的遗留表与列

Revision ID: 0080_drop_commercial_modules
Revises: add_stripe_payment_fields
Create Date: 2026-11-18

下线内容（代码已随本次清理物理删除，本迁移负责数据结构对齐）：
- 表：payment_products / payment_orders / point_transactions / redemption_codes /
  redemption_records / auth_requests / invite_relations / content_views /
  tutorial_purchases / tutorial_netdisk_links / downloads
- users 列：points / vip_expire_time / free_used_seconds / free_started_at /
  pending_auth_code / invite_code / referrer_id / wechat_openid / wechat_unionid /
  wechat_web_openid（role='vip' 一并降级为 'user'）
- tutorials 列：access_mode / points_price（回归纯内容模型）
- rbac：删除上述模块的权限码及其角色绑定（role_permissions 级联清理）
- system_settings：删除上述模块的配置键

回滚（downgrade）只重建结构，不恢复数据——下线是单向决策，回滚用于全新库对齐旧结构。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0080_drop_commercial_modules"
down_revision: Union[str, None] = "add_stripe_payment_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DROPPED_TABLES = (
    "tutorial_netdisk_links",
    "tutorial_purchases",
    "content_views",
    "invite_relations",
    "auth_requests",
    "redemption_records",
    "redemption_codes",
    "point_transactions",
    "payment_orders",
    "payment_products",
    "downloads",
)

_USERS_COLUMNS = (
    "points",
    "vip_expire_time",
    "free_used_seconds",
    "free_started_at",
    "pending_auth_code",
    "invite_code",
    "referrer_id",
    "wechat_openid",
    "wechat_unionid",
    "wechat_web_openid",
)

_TUTORIAL_COLUMNS = ("access_mode", "points_price")

_REMOVED_PERMISSIONS = (
    "payment_product:view", "payment_product:create", "payment_product:edit",
    "payment_product:delete", "payment_order:view",
    "points:view",
    "redeem_code:view", "redeem_code:create", "redeem_code:disable",
    "auth_request:view", "auth_request:review",
    "payment_setting:view", "payment_setting:update",
)

_REMOVED_SETTING_KEYS = (
    "invite_enabled", "invite_reward_points",
    "free_time_hours",
    "baidu_push_token", "github_proxy", "github_mirror_prefix", "hf_token",
    "article_proxy",
    "hero_home", "hero_tutorials", "hero_resources", "hero_tools",
    "hero_nodes", "hero_plugins",
    "default_resource_cover", "default_node_cover",
    "community_qrcode", "star_auth_qrcode", "service_qrcode", "wechat_qrcode",
    "social_douyin", "social_bilibili", "social_github", "footer_sites",
    "phone_login_enabled", "phone_login_auto_register",
    "wechat_web_login_enabled", "wechat_login_auto_register",
    "wechat_mini_login_enabled", "wechat_web_appid", "wechat_web_secret",
    "wechat_web_redirect_uri", "wechat_mini_appid", "wechat_mini_secret",
    "sms_provider", "sms_code_ttl_seconds", "sms_send_interval_seconds",
    "sms_daily_limit_per_phone", "sms_daily_limit_per_ip", "sms_mock_code",
    "sms_tencent_secret_id", "sms_tencent_secret_key", "sms_tencent_sdk_app_id",
    "sms_tencent_sign_name", "sms_tencent_template_id", "sms_tencent_region",
    "sms_tencent_template_params", "sms_aliyun_access_key_id",
    "sms_aliyun_access_key_secret", "sms_aliyun_sign_name",
    "sms_aliyun_template_code", "sms_aliyun_region", "sms_aliyun_template_param_key",
    "payment_enabled", "payment_mock_enabled", "payment_order_expire_minutes",
    "wechat_pay_enabled", "wechat_pay_mch_id", "wechat_pay_appid",
    "wechat_pay_serial_no", "wechat_pay_api_v3_key", "wechat_pay_private_key",
    "wechat_pay_platform_public_key", "wechat_pay_notify_url",
    "stripe_enabled", "stripe_mode", "stripe_secret_key", "stripe_publishable_key",
    "stripe_webhook_secret", "stripe_currency", "stripe_success_url",
    "stripe_cancel_url",
    "alipay_enabled", "alipay_app_id", "alipay_private_key", "alipay_public_key",
    "alipay_notify_url", "alipay_return_url", "alipay_gateway",
    "image_search_baidu_enabled", "image_search_sogou_enabled",
)


def _table_exists(bind, name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :name"
            ),
            {"name": name},
        ).scalar()
    )


def _column_exists(bind, table: str, column: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    )



# downgrade: 与 drop 前一致的空表结构（含索引/外键）
_CREATE_DDLS = (
    r'''CREATE TABLE `payment_products` (
  `id` bigint NOT NULL COMMENT '主键',
  `code` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '套餐标识',
  `name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '展示名称',
  `description` varchar(300) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '副标题/说明',
  `kind` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '权益类型 points/vip',
  `amount_cents` int NOT NULL COMMENT '售价（分，CNY）',
  `points` int NOT NULL DEFAULT '0' COMMENT '到账积分',
  `vip_days` int NOT NULL DEFAULT '0' COMMENT 'VIP 天数',
  `badge` varchar(30) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '角标',
  `is_enabled` tinyint(1) NOT NULL DEFAULT '1' COMMENT '是否上架',
  `sort_order` int NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_payment_products_code` (`code`),
  KEY `ix_payment_products_kind` (`kind`),
  KEY `ix_payment_products_is_enabled` (`is_enabled`),
  KEY `ix_payment_products_updated_at` (`updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    r'''CREATE TABLE `payment_orders` (
  `id` bigint NOT NULL COMMENT '主键',
  `order_no` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '商户订单号',
  `user_id` bigint NOT NULL COMMENT '用户ID',
  `product_code` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '商品 code 快照',
  `product_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '商品名称快照',
  `product_kind` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'points/vip',
  `amount_cents` int NOT NULL COMMENT '应付金额（分）',
  `points` int NOT NULL DEFAULT '0' COMMENT '积分快照',
  `vip_days` int NOT NULL DEFAULT '0' COMMENT 'VIP 天数快照',
  `channel` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'mock/wechat/alipay',
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending' COMMENT 'pending/paid/closed/refunded/failed',
  `provider_trade_no` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '第三方交易号',
  `provider_payload` json DEFAULT NULL COMMENT '下单/回调摘要（不含密钥）',
  `paid_at` datetime DEFAULT NULL COMMENT '支付时间',
  `expires_at` datetime DEFAULT NULL COMMENT '订单过期时间',
  `client_ip` varchar(45) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '下单 IP',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
  `currency` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'CNY' COMMENT '币种（ISO 4217）',
  `provider_session_id` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '第三方会话号（Stripe Checkout Session）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_payment_orders_order_no` (`order_no`),
  UNIQUE KEY `ix_payment_orders_provider_trade_no` (`provider_trade_no`),
  KEY `ix_payment_orders_user_id` (`user_id`),
  KEY `ix_payment_orders_channel` (`channel`),
  KEY `ix_payment_orders_status` (`status`),
  KEY `ix_payment_orders_updated_at` (`updated_at`),
  KEY `ix_payment_orders_user_created` (`user_id`,`created_at`),
  KEY `ix_payment_orders_status_created` (`status`,`created_at`),
  KEY `ix_payment_orders_provider_session_id` (`provider_session_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    r'''CREATE TABLE `point_transactions` (
  `id` bigint NOT NULL,
  `user_id` bigint NOT NULL COMMENT '用户ID',
  `points` int NOT NULL COMMENT '变动值(正=获得,负=消耗)',
  `balance_after` int NOT NULL COMMENT '变动后余额',
  `type` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'consume' COMMENT '类型 consume/admin_set/admin_grant/admin_deduct',
  `description` varchar(200) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '描述',
  `operator_id` bigint DEFAULT NULL COMMENT '操作管理员ID(后台调整时)',
  `created_at` datetime NOT NULL DEFAULT (now()) COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `ix_point_transactions_user_id` (`user_id`),
  KEY `ix_point_transactions_type` (`type`),
  KEY `ix_point_tx_user_created` (`user_id`,`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    r'''CREATE TABLE `redemption_codes` (
  `id` bigint NOT NULL,
  `code` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '兑换码',
  `points` int NOT NULL COMMENT '每份兑换积分',
  `max_uses` int NOT NULL COMMENT '最大兑换人数(单人码=1)',
  `used_count` int NOT NULL DEFAULT '0' COMMENT '已兑换人数',
  `status` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'active' COMMENT 'active/disabled',
  `expires_at` datetime DEFAULT NULL COMMENT '过期时间(空=永不过期)',
  `note` varchar(200) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '备注',
  `created_by` bigint DEFAULT NULL COMMENT '创建管理员ID',
  `created_at` datetime NOT NULL DEFAULT (now()) COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_redemption_codes_code` (`code`),
  KEY `ix_redemption_codes_status_created` (`status`,`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    r'''CREATE TABLE `redemption_records` (
  `id` bigint NOT NULL,
  `code_id` bigint NOT NULL COMMENT '兑换码ID',
  `code` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '兑换码快照(便于追溯)',
  `user_id` bigint NOT NULL COMMENT '用户ID',
  `points` int NOT NULL COMMENT '获得积分',
  `created_at` datetime NOT NULL DEFAULT (now()) COMMENT '兑换时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_redemption_code_user` (`code_id`,`user_id`),
  KEY `ix_redemption_records_code_id` (`code_id`),
  KEY `ix_redemption_records_user_id` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    r'''CREATE TABLE `auth_requests` (
  `id` bigint NOT NULL,
  `user_id` bigint NOT NULL COMMENT '用户ID',
  `star_code` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '知识星球号',
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'pending/approved/rejected',
  `vip_duration` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '1month/3month/1year/custom',
  `reviewed_by` bigint DEFAULT NULL COMMENT '审核管理员ID',
  `reviewed_at` datetime DEFAULT NULL COMMENT '审核时间',
  `reject_reason` text COLLATE utf8mb4_unicode_ci COMMENT '驳回原因',
  `created_at` datetime NOT NULL DEFAULT (now()) COMMENT '提交时间',
  `pending_key` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '待审核唯一键: pending 时填 ''pending''，终态为 NULL',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_auth_request_pending` (`user_id`,`pending_key`),
  KEY `ix_auth_requests_status` (`status`),
  KEY `ix_auth_requests_user_id` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    r'''CREATE TABLE `invite_relations` (
  `id` bigint NOT NULL,
  `inviter_id` bigint NOT NULL COMMENT '邀请人ID',
  `invitee_id` bigint NOT NULL COMMENT '被邀请人ID(一人仅可被邀请一次)',
  `reward_points` int NOT NULL DEFAULT '0' COMMENT '邀请人获得的积分',
  `created_at` datetime NOT NULL DEFAULT (now()) COMMENT '绑定时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_invite_relations_invitee` (`invitee_id`),
  KEY `ix_invite_relations_inviter_id` (`inviter_id`),
  KEY `ix_invite_relations_invitee_id` (`invitee_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    r'''CREATE TABLE `content_views` (
  `id` bigint NOT NULL COMMENT '主键',
  `user_id` bigint NOT NULL COMMENT '浏览用户ID',
  `target_type` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '内容类型: tutorial/resource/node/plugin',
  `target_id` bigint NOT NULL COMMENT '内容ID',
  `view_date` date NOT NULL COMMENT '浏览日期(按天去重)',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次浏览时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_content_view_daily` (`user_id`,`target_type`,`target_id`,`view_date`),
  KEY `ix_content_views_target` (`target_type`,`target_id`,`view_date`),
  KEY `ix_content_views_view_date` (`view_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    r'''CREATE TABLE `tutorial_purchases` (
  `id` bigint NOT NULL,
  `user_id` bigint NOT NULL COMMENT '用户ID',
  `tutorial_id` bigint NOT NULL COMMENT '教程ID',
  `points_paid` int NOT NULL COMMENT '购买时消耗的积分',
  `created_at` datetime NOT NULL DEFAULT (now()) COMMENT '购买时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_tutorial_purchase_user_tutorial` (`user_id`,`tutorial_id`),
  KEY `ix_tutorial_purchases_user_id` (`user_id`),
  KEY `ix_tutorial_purchase_tutorial` (`tutorial_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    r'''CREATE TABLE `tutorial_netdisk_links` (
  `id` bigint NOT NULL,
  `tutorial_id` bigint NOT NULL COMMENT '教程ID',
  `platform` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'baidu/quark/mobile/aliyun/github/other',
  `url` varchar(1000) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '网盘链接',
  `extract_code` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '提取码',
  PRIMARY KEY (`id`),
  KEY `ix_tutorial_netdisk_links_tutorial_id` (`tutorial_id`),
  CONSTRAINT `tutorial_netdisk_links_ibfk_1` FOREIGN KEY (`tutorial_id`) REFERENCES `tutorials` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    r'''CREATE TABLE `downloads` (
  `id` bigint NOT NULL,
  `user_id` bigint NOT NULL COMMENT '用户ID',
  `resource_id` bigint NOT NULL COMMENT '资源ID',
  `file_size` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '资源大小',
  `created_at` datetime NOT NULL DEFAULT (now()) COMMENT '下载时间',
  `resource_name` varchar(200) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '资源名称快照',
  `file_type` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '文件类型: pdf/archive/exe/other',
  PRIMARY KEY (`id`),
  KEY `ix_downloads_resource_id` (`resource_id`),
  KEY `ix_downloads_user_id` (`user_id`),
  KEY `ix_downloads_created_at` (`created_at`),
  KEY `ix_downloads_user_created` (`user_id`,`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
)

_ADD_USERS_COLUMNS = (
    "ALTER TABLE `users` ADD COLUMN `points` INT NOT NULL DEFAULT 0 COMMENT '积分余额'",
    "ALTER TABLE `users` ADD COLUMN `vip_expire_time` DATETIME NULL COMMENT 'VIP过期时间'",
    "ALTER TABLE `users` ADD COLUMN `free_used_seconds` INT NOT NULL COMMENT '免费已用时长(秒)'",
    "ALTER TABLE `users` ADD COLUMN `free_started_at` DATETIME NULL COMMENT '免费时长起点(注册时间)'",
    "ALTER TABLE `users` ADD COLUMN `pending_auth_code` VARCHAR(50) NULL COMMENT '待审核星球号'",
    "ALTER TABLE `users` ADD COLUMN `invite_code` VARCHAR(16) NULL COMMENT '专属邀请码'",
    "ALTER TABLE `users` ADD COLUMN `referrer_id` BIGINT NULL COMMENT '邀请人ID'",
    "ALTER TABLE `users` ADD COLUMN `wechat_openid` VARCHAR(64) NULL COMMENT '微信小程序 openid'",
    "ALTER TABLE `users` ADD COLUMN `wechat_unionid` VARCHAR(64) NULL COMMENT '微信 UnionID'",
    "ALTER TABLE `users` ADD COLUMN `wechat_web_openid` VARCHAR(64) NULL COMMENT '微信开放平台网站应用 openid'",
    "ALTER TABLE `users` ADD UNIQUE INDEX `uq_users_invite_code` (`invite_code`)",
    "ALTER TABLE `users` ADD INDEX `ix_users_referrer_id` (`referrer_id`)",
    "ALTER TABLE `users` ADD UNIQUE INDEX `uq_users_wechat_openid` (`wechat_openid`)",
    "ALTER TABLE `users` ADD UNIQUE INDEX `uq_users_wechat_unionid` (`wechat_unionid`)",
    "ALTER TABLE `users` ADD UNIQUE INDEX `uq_users_wechat_web_openid` (`wechat_web_openid`)",
)

_ADD_TUTORIAL_COLUMNS = (
    "ALTER TABLE `tutorials` ADD COLUMN `access_mode` VARCHAR(20) NOT NULL DEFAULT 'free' COMMENT '访问模式: free/vip'",
    "ALTER TABLE `tutorials` ADD COLUMN `points_price` INT NOT NULL DEFAULT 0 COMMENT '积分购买价'",
)

def upgrade() -> None:
    bind = op.get_bind()

    # 1) VIP 角色降级（drop 列前先归位，避免丢角色信息）
    bind.execute(
        sa.text("UPDATE users SET role = 'user' WHERE role = 'vip'")
    )

    # 2) 删表（自关联/外键多的先删：netdisk/purchase 先于 tutorials 主表无碍，
    #    但 payment_orders 依赖 payment_products，子表先行）
    for table in _DROPPED_TABLES:
        if _table_exists(bind, table):
            op.drop_table(table)

    # 3) 删列
    for column in _USERS_COLUMNS:
        if _column_exists(bind, "users", column):
            op.drop_column("users", column)
    for column in _TUTORIAL_COLUMNS:
        if _column_exists(bind, "tutorials", column):
            op.drop_column("tutorials", column)

    # 4) 权限码与角色绑定：先删绑定（role_permissions），再删权限定义
    bind.execute(
        sa.text(
            "DELETE rp FROM role_permissions rp "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE p.code IN :codes"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": list(_REMOVED_PERMISSIONS)},
    )
    bind.execute(
        sa.text("DELETE FROM permissions WHERE code IN :codes").bindparams(
            sa.bindparam("codes", expanding=True)
        ),
        {"codes": list(_REMOVED_PERMISSIONS)},
    )

    # 5) 下线模块的系统设置键
    bind.execute(
        sa.text("DELETE FROM system_settings WHERE setting_key IN :keys").bindparams(
            sa.bindparam("keys", expanding=True)
        ),
        {"keys": list(_REMOVED_SETTING_KEYS)},
    )


def downgrade() -> None:
    # 重建空表结构、列、权限点与设置键（数据不恢复，沿用 0069 迁移的口径）
    bind = op.get_bind()

    # 1) 重建表（顺序：主表先建，子表 DDL 内联外键引用主表）
    for ddl in _CREATE_DDLS:
        table = ddl.strip().split("`")[1]
        exists = bool(bind.execute(sa.text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = :name"), {"name": table}).scalar())
        if not exists:
            op.execute(ddl)

    # 2) 回补列与索引（幂等：不存在才执行）
    for statement in _ADD_USERS_COLUMNS:
        if "ADD COLUMN" in statement:
            column = statement.split("`")[3]
            exists = bool(bind.execute(sa.text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'users' AND column_name = :c"),
                {"c": column}).scalar())
            if not exists:
                op.execute(statement)
        else:
            exists = bool(bind.execute(sa.text(
                "SELECT COUNT(*) FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = 'users' AND index_name = :n"),
                {"n": statement.split("`")[3]}).scalar())
            if not exists:
                op.execute(statement)
    for statement in _ADD_TUTORIAL_COLUMNS:
        column = statement.split("`")[3]
        exists = bool(bind.execute(sa.text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'tutorials' AND column_name = :c"),
            {"c": column}).scalar())
        if not exists:
            op.execute(statement)

    # 3) 回补权限点（角色绑定不回补：内置角色派生由代码层种子逻辑在启动时对齐）
    import secrets as _secrets
    for code, name, ptype, module, module_name, sort in _REMOVED_PERMISSIONS:
        exists = bool(bind.execute(sa.text(
            "SELECT COUNT(*) FROM permissions WHERE code = :code"), {"code": code}).scalar())
        if exists:
            continue
        bind.execute(sa.text(
            "INSERT INTO permissions (id, code, name, type, module, module_name, sort) "
            "VALUES (:id, :code, :name, :type, :module, :module_name, :sort)"), {
            "id": _secrets.randbelow(9_000_000_000) + 1_000_000_000,
            "code": code, "name": name, "type": ptype, "module": module,
            "module_name": module_name, "sort": sort,
        })

    # 4) 回补设置键默认值（只恢复键与下线时的声明默认值）
    setting_defaults = {
        "invite_enabled": "1", "invite_reward_points": "50", "free_time_hours": "3",
        "baidu_push_token": "", "github_proxy": "", "github_mirror_prefix": "",
        "hf_token": "", "article_proxy": "",
        "payment_enabled": "0", "payment_mock_enabled": "1",
        "payment_order_expire_minutes": "30",
        "wechat_pay_enabled": "0",
        "stripe_enabled": "1", "stripe_mode": "test",
        "alipay_enabled": "0",
        "phone_login_enabled": "0", "phone_login_auto_register": "1",
        "wechat_web_login_enabled": "0", "wechat_login_auto_register": "1",
        "wechat_mini_login_enabled": "0",
        "sms_provider": "mock", "login_captcha_enabled": "0",
    }
    for key, value in setting_defaults.items():
        exists = bool(bind.execute(sa.text(
            "SELECT COUNT(*) FROM system_settings WHERE setting_key = :k"), {"k": key}).scalar())
        if not exists:
            bind.execute(sa.text(
                "INSERT INTO system_settings (setting_key, value) VALUES (:k, :v)"),
                {"k": key, "v": value})
