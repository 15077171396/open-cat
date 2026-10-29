"""系统设置项的三端一致性守卫（缺口台账 P2-31）。

加一个系统设置原先要改三处，而漏改的任何一处都**不报错**：

- 后端 `SETTING_DEFINITIONS`（键与默认值的唯一事实来源）；
- `frontend/src/store/settingsStore.ts` 的第二份默认值 + `KNOWN_SETTING_KEYS` 白名单
  —— 白名单会**静默丢弃未知键**，所以「后端加了、前台读不到」表现为一次成功保存；
- 小程序页面里的硬编码（例如首页主视觉此前写死一个 COS 地址，永远不读 `/settings`）。

管理端表单是通用渲染的，所以后端加完键，后台就能看到并能保存 —— 这恰恰让"漏改"
最难被发现。本守卫把「必须显式决定这个键归哪一侧」变成测试失败，而不是靠人记。
"""

import io
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # backend/
ROOT = os.path.dirname(REPO)

# 有意**不**下发给公开站点的设置：仅管理端消费（开关、法务文书、AI 供应商配置）
ADMIN_ONLY_SETTINGS = {
    'invite_enabled',
    'invite_reward_points',
    'legal_privacy',
    'legal_agreement',
    'legal_disclaimer',
    # AI 模型/凭据/单模型单价已迁到 ai_models（「系统 → 模型管理」），
    # 不再属于系统设置键；这里只保留全站成本护栏。
    'ai_global_daily_limit',
    'ai_global_monthly_limit',
    'ai_price_input_per_mtok',
    'ai_price_output_per_mtok',
    # AI 写作（2026-10-28）：总开关与图片日限额只给后台/后端消费，不下发公开站点
    'ai_writing_enabled',
    'ai_writing_image_daily_limit',
    # 图片搜索（2026-10-29）：图源密钥与限额只给后台/后端消费
    'image_search_enabled',
    'image_search_pexels_key',
    'image_search_pixabay_key',
    'image_search_giphy_key',
    'image_search_memecub_key',
    'image_search_pexels_enabled',
    'image_search_pixabay_enabled',
    'image_search_giphy_enabled',
    'image_search_tenor_enabled',
    'image_search_so360_enabled',
    'image_search_baidu_enabled',
    'image_search_sogou_enabled',
    'image_search_duitang_enabled',
    'image_search_doutupk_enabled',
    'image_search_adoutu_enabled',
    'image_search_memecub_enabled',
    'image_search_emoji_enabled',
    'image_search_page_size',
    'image_search_admin_hourly_limit',
    'image_search_daily_import_limit',
    # 登录 / 短信 / 支付（2026-10-27）：全部只给后台与后端消费，不下发公开站点
    'phone_login_enabled',
    'phone_login_auto_register',
    'wechat_web_login_enabled',
    'wechat_login_auto_register',
    'wechat_mini_login_enabled',
    'wechat_web_appid',
    'wechat_web_secret',
    'wechat_web_redirect_uri',
    'sms_provider',
    'sms_code_ttl_seconds',
    'sms_send_interval_seconds',
    'sms_daily_limit_per_phone',
    'sms_daily_limit_per_ip',
    'sms_mock_code',
    'sms_tencent_secret_id',
    'sms_tencent_secret_key',
    'sms_tencent_sdk_app_id',
    'sms_tencent_sign_name',
    'sms_tencent_template_id',
    'sms_tencent_region',
    'sms_tencent_template_params',
    'sms_aliyun_access_key_id',
    'sms_aliyun_access_key_secret',
    'sms_aliyun_sign_name',
    'sms_aliyun_template_code',
    'sms_aliyun_region',
    'sms_aliyun_template_param_key',
    'payment_enabled',
    'payment_mock_enabled',
    'payment_order_expire_minutes',
    'wechat_pay_enabled',
    'wechat_pay_mch_id',
    'wechat_pay_appid',
    'wechat_pay_serial_no',
    'wechat_pay_api_v3_key',
    'wechat_pay_private_key',
    'wechat_pay_platform_public_key',
    'wechat_pay_notify_url',
    'alipay_enabled',
    'alipay_app_id',
    'alipay_private_key',
    'alipay_public_key',
    'alipay_notify_url',
    'alipay_return_url',
    'alipay_gateway',
    # 运行期可改配置（2026-10-19）：全部是后台/后端消费的运维项，前台不读
    'free_time_hours',
    'ai_daily_limit_per_user',
    'ai_retry_count',
    'ai_total_budget_seconds',
    'moderation_enabled',
    'moderation_action',
    'moderation_extra_words',
    'upload_max_file_mb',
    'upload_max_image_mb',
    'avatar_max_size_mb',
    'avatar_allowed_types',
    'image_webp_threshold_kb',
    'image_webp_quality',
    'rate_limit_guest',
    'rate_limit_user',
    'rate_limit_window',
    'search_fulltext_enabled',
    'search_max_keyword_len',
    'site_base_url',
    'baidu_push_token',
    'indexnow_key',
    'github_token',
    'github_proxy',
    'github_mirror_prefix',
    'hf_token',
    'article_proxy',
    'cos_secret_id',
    'cos_secret_key',
    'cos_bucket',
    'cos_region',
    'cos_cdn_domain',
    'wechat_mini_appid',
    'wechat_mini_secret',
    'smtp_host',
    'smtp_port',
    'smtp_username',
    'smtp_password',
    'smtp_use_tls',
    'smtp_from_email',
    'smtp_sender_name',
}


def _backend_keys():
    from app.services.setting_service import SETTING_DEFINITIONS

    return {d.key for d in SETTING_DEFINITIONS}


def _frontend_keys():
    txt = io.open(os.path.join(ROOT, 'frontend', 'src', 'store', 'settingsStore.ts'), encoding='utf-8').read()
    block = re.search(r'DEFAULT_SITE_SETTINGS[^{]*\{(.*?)\n\}\)', txt, re.S)
    assert block, '没能在 settingsStore.ts 里定位 DEFAULT_SITE_SETTINGS，守卫需重写'
    return set(re.findall(r'^\s*([a-z0-9_]+):', block.group(1), re.M))


def _miniprogram_sources():
    base = os.path.join(ROOT, 'miniprogram', 'pages')
    out = {}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != 'node_modules']
        for fn in filenames:
            if fn.endswith('.js'):
                out[os.path.relpath(os.path.join(dirpath, fn), ROOT)] = io.open(
                    os.path.join(dirpath, fn), encoding='utf-8'
                ).read()
    return out


class SettingsKeyParityTests(unittest.TestCase):
    def test_every_backend_key_is_classified(self):
        backend = _backend_keys()
        frontend = _frontend_keys()
        unclassified = backend - frontend - ADMIN_ONLY_SETTINGS
        self.assertFalse(
            unclassified,
            '后端新增了设置键，但没人决定它是否下发给公开站点：'
            f'{sorted(unclassified)}。要么加进 frontend/settingsStore.ts 的默认值表'
            '（前台会消费），要么登记进本文件的 ADMIN_ONLY_SETTINGS（仅管理端）。',
        )

    def test_classification_table_is_not_stale(self):
        backend = _backend_keys()
        frontend = _frontend_keys()
        stale = (ADMIN_ONLY_SETTINGS - backend) | (ADMIN_ONLY_SETTINGS & frontend)
        self.assertFalse(
            stale,
            f'ADMIN_ONLY_SETTINGS 里有已删除或已下发给前台的键：{sorted(stale)}，请从这里删掉',
        )

    def test_frontend_defaults_have_no_unknown_keys(self):
        """前端默认表里的键必须真实存在于后端，否则那份默认值是死代码。"""
        backend = _backend_keys()
        extra = _frontend_keys() - backend
        self.assertFalse(extra, f'前端默认值表里有后端不认识的键：{sorted(extra)}')

    def test_predicate_detects_an_unclassified_key(self):
        """反向对照：新增键没登记时必须被判红，否则本守卫恒绿。"""
        backend = {'site_name', 'hero_home', 'brand_new_switch'}
        frontend = {'site_name', 'hero_home'}
        self.assertEqual(backend - frontend - ADMIN_ONLY_SETTINGS, {'brand_new_switch'})
        self.assertEqual({'brand_new_switch'} - backend, set())  # 删键后陈旧项判空

    def test_hero_settings_are_consumed_by_miniprogram(self):
        """每个 hero_* 主视觉都得在小程序某页真的被读到。

        首页就是漏网之鱼：其它列表页都调 `api.getSettings()`，只有首页写死了一张
        COS 图的地址，于是后台换主视觉对小程序无效且没有任何信号（P2-31）。
        """
        heroes = {k for k in _backend_keys() if k.startswith('hero_')}
        self.assertGreaterEqual(len(heroes), 5, 'hero 键集合异常，守卫需检查')
        sources = _miniprogram_sources()
        unread = [k for k in sorted(heroes) if not any(k in body for body in sources.values())]
        self.assertFalse(
            unread,
            f'这些主视觉设置小程序从未读取：{unread}。'
            '请在对应页面用 api.getSettings() 覆盖兜底图，'
            '或确认它确实只给 PC 用并把键名加进白名单。',
        )


class SettingGroupDeclarationTests(unittest.TestCase):
    """分组声明守卫（第五轮审查 EXT-05）。

    收敛前「分组顺序 + 分组说明」只存在于管理端的两个常量里，而 tab 渲染是
    `GROUP_ORDER.filter(g => map.has(g))` —— 后端新增一个 group 而前端没同步时，
    **整组设置静默消失**：接口返回了、后台看不到、没有任何报错。
    现在分组由后端 `SETTING_GROUPS` 声明，前端按下发顺序渲染。

    这里钉住：定义的分组必须已声明、标题唯一、没有空分组、顺序稳定。
    """

    def test_every_definition_group_is_declared(self):
        from app.services.setting_service import SETTING_DEFINITIONS, SETTING_GROUPS

        declared = {g.key for g in SETTING_GROUPS}
        unknown = sorted({d.group for d in SETTING_DEFINITIONS} - declared)
        self.assertEqual(
            unknown, [],
            f"以下分组未在 SETTING_GROUPS 声明，后台不会渲染该组（整组设置消失）：{unknown}",
        )

    def test_group_labels_are_unique(self):
        """同一分组只能有一个标题：两处写不同标题会让后台出现同名 tab 或标题跳变。"""
        from app.services.setting_service import SETTING_DEFINITIONS, SETTING_GROUPS

        declared = {g.key: g.label for g in SETTING_GROUPS}
        for d in SETTING_DEFINITIONS:
            self.assertEqual(
                d.group_label, declared[d.group],
                f"{d.key} 的 group_label={d.group_label!r} 与声明 {declared[d.group]!r} 不一致",
            )

    def test_no_empty_groups(self):
        from app.services.setting_service import SETTING_DEFINITIONS, SETTING_GROUPS

        used = {d.group for d in SETTING_DEFINITIONS}
        empty = [g.key for g in SETTING_GROUPS if g.key not in used]
        self.assertEqual(empty, [], f"以下分组没有任何设置项，后台会留下空 tab：{empty}")

    def test_groups_payload_order_and_shape(self):
        from app.services.setting_service import SETTING_GROUPS, groups_payload

        payload = groups_payload()
        self.assertEqual([g["key"] for g in payload], [g.key for g in SETTING_GROUPS])
        self.assertEqual(len({g["key"] for g in payload}), len(payload), "分组 key 重复")
        for item in payload:
            self.assertTrue(item["label"], f"{item['key']} 缺 label")
            self.assertIn("description", item)


class RuntimeRegistryConsistencyTests(unittest.TestCase):
    """`RUNTIME_KEYS`（后台可改 → 覆盖 settings）与另外两处注册表的一致性。

    这条链有三段：`system_settings` 的键（SETTING_DEFINITIONS）、覆盖目标属性
    （`Settings` 的字段）、以及运行期注册表本身。任一段写错都不报错 ——
    表现为「后台保存成功但完全不生效」，且只有重启后才可能被发现。
    """

    def test_runtime_setting_keys_exist_in_definitions(self):
        from app.services.runtime_config import RUNTIME_KEYS
        from app.services.setting_service import SETTING_DEFINITIONS

        defs = {d.key for d in SETTING_DEFINITIONS}
        missing = sorted({k.setting_key for k in RUNTIME_KEYS} - defs)
        self.assertEqual(
            missing, [],
            f"以下运行期键没有对应设置定义，后台改不到它们（改了也不生效）：{missing}",
        )

    def test_runtime_attrs_exist_on_settings(self):
        from app.config import Settings
        from app.services.runtime_config import RUNTIME_KEYS

        fields = set(Settings.model_fields)
        missing = sorted({k.attr for k in RUNTIME_KEYS} - fields)
        self.assertEqual(
            missing, [],
            f"以下覆盖目标不是 Settings 的字段，写入会静默失败（AttributeError 被吞）：{missing}",
        )

    def test_runtime_keys_are_unique(self):
        from app.services.runtime_config import RUNTIME_KEYS

        keys = [k.setting_key for k in RUNTIME_KEYS]
        self.assertEqual(len(keys), len(set(keys)), "RUNTIME_KEYS 里有重复的 setting_key")


if __name__ == '__main__':
    unittest.main()
