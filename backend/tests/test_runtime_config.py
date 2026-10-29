"""运行期可改配置（services/runtime_config.py）的回归测试。

这套机制的三个契约（写错任何一个都表现为「后台保存成功但线上没变化」）：

1. **env 是默认值**：后台清空某项后，下次刷新必须把 env 原值写回来；
2. **单位换算**：小时→秒、MB→字节，换算错会让「免费 3 小时」变成 3 秒；
3. **失败不覆盖**：DB/Redis 读不到时保持当前生效值，绝不能用「读不到」推导出
   「配置为空」—— 那会把限流阈值刷成 0，全站立刻 429。
"""

import unittest
from unittest import mock

from app.config import settings
from app.models.system_setting import SystemSetting
from app.services import runtime_config, setting_service
from tests.conftest import FakeRedis


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows, counter):
        self._rows = rows
        self._counter = counter

    async def __aenter__(self):
        self._counter["queries"] += 1
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        return _Rows(self._rows)


def _rows_for(values: dict[str, str]):
    return [SystemSetting(setting_key=k, value=v) for k, v in values.items()]


class RuntimeConfigTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_redis = runtime_config.redis_client
        self._orig_session = runtime_config.async_session
        self._orig_retry_after = runtime_config._db_retry_after
        self._orig_warned = runtime_config._db_warned
        self._orig_force = runtime_config._force_reload
        # 所有会被改写的 settings 属性：测试结束后必须还原，否则污染其它用例
        self._snapshot = {
            spec.attr: getattr(settings, spec.attr) for spec in runtime_config.RUNTIME_KEYS
        }
        self.redis = FakeRedis()
        self.counter = {"queries": 0}
        runtime_config.redis_client = self.redis
        runtime_config._db_retry_after = 0.0
        runtime_config._db_warned = True
        runtime_config._force_reload = False
        self._db({})

    def tearDown(self):
        runtime_config.redis_client = self._orig_redis
        runtime_config.async_session = self._orig_session
        runtime_config._db_retry_after = self._orig_retry_after
        runtime_config._db_warned = self._orig_warned
        runtime_config._force_reload = self._orig_force
        for attr, value in self._snapshot.items():
            setattr(settings, attr, value)

    def _db(self, values: dict[str, str]):
        runtime_config.async_session = lambda: _FakeSession(_rows_for(values), self.counter)

    # ---------- 1. 单位换算 ----------

    async def test_hours_are_converted_to_seconds(self):
        await runtime_config.apply_runtime_settings({"free_time_hours": "1.5"})
        self.assertEqual(settings.FREE_TIME_LIMIT, 5400)

    async def test_megabytes_are_converted_to_bytes(self):
        await runtime_config.apply_runtime_settings({"upload_max_file_mb": "30"})
        self.assertEqual(settings.COS_MAX_FILE_SIZE, 30 * 1024 * 1024)

    async def test_kilobytes_are_converted_to_bytes(self):
        """WebP 阈值：0 是合法值（= 关闭转码），不能被当成「未配置」回退默认"""
        await runtime_config.apply_runtime_settings({"image_webp_threshold_kb": "200"})
        self.assertEqual(settings.COS_IMAGE_WEBP_THRESHOLD, 200 * 1024)
        await runtime_config.apply_runtime_settings({"image_webp_threshold_kb": "0"})
        self.assertEqual(settings.COS_IMAGE_WEBP_THRESHOLD, 0)

    async def test_bool_and_select_values(self):
        await runtime_config.apply_runtime_settings(
            {"moderation_enabled": "0", "moderation_action": "mask", "smtp_use_tls": "false"}
        )
        self.assertFalse(settings.MODERATION_ENABLED)
        self.assertEqual(settings.MODERATION_ACTION, "mask")
        self.assertFalse(settings.SMTP_USE_TLS)

    # ---------- 2. env 是默认值 ----------

    async def test_clearing_a_key_restores_env_default(self):
        """后台清空 = 恢复默认。这里最容易写错成「保持上次覆盖值」"""
        env_default = runtime_config._ENV_DEFAULTS["free_time_hours"]
        await runtime_config.apply_runtime_settings({"free_time_hours": "1"})
        self.assertEqual(settings.FREE_TIME_LIMIT, 3600)

        await runtime_config.apply_runtime_settings({})
        self.assertEqual(settings.FREE_TIME_LIMIT, env_default)

    async def test_invalid_values_fall_back_to_env(self):
        """脏值（手改库 / 老版本遗留）不能让服务用上荒谬阈值"""
        env_default = runtime_config._ENV_DEFAULTS["rate_limit_guest"]
        await runtime_config.apply_runtime_settings(
            {"rate_limit_guest": "abc", "rate_limit_user": "-5"}
        )
        self.assertEqual(settings.RATE_LIMIT_GUEST, env_default)
        self.assertEqual(settings.RATE_LIMIT_USER, runtime_config._ENV_DEFAULTS["rate_limit_user"])

    async def test_out_of_range_values_fall_back_to_env(self):
        """上限/下限是**防御性**的：限流阈值填 0 会让全站立刻 429"""
        await runtime_config.apply_runtime_settings({"rate_limit_guest": "0"})
        self.assertEqual(
            settings.RATE_LIMIT_GUEST, runtime_config._ENV_DEFAULTS["rate_limit_guest"]
        )

    # ---------- 3. 失败不覆盖 ----------

    async def test_db_failure_keeps_current_values(self):
        await runtime_config.apply_runtime_settings({"free_time_hours": "2"})
        self.assertEqual(settings.FREE_TIME_LIMIT, 7200)

        def _boom():
            raise RuntimeError("db down")

        runtime_config.async_session = _boom
        runtime_config._force_reload = True
        runtime_config._db_retry_after = 0.0
        result = await runtime_config.apply_runtime_settings()
        self.assertEqual(result, {})
        # 关键：不能被「读不到」清成 env 默认值
        self.assertEqual(settings.FREE_TIME_LIMIT, 7200)

    async def test_db_failure_backs_off(self):
        def _boom():
            raise RuntimeError("db down")

        runtime_config.async_session = _boom
        await runtime_config.apply_runtime_settings()
        self.assertGreater(runtime_config._db_retry_after, 0.0)

        runtime_config.async_session = lambda: (_ for _ in ()).throw(
            AssertionError("退避期内不应再查库")
        )
        await runtime_config.apply_runtime_settings()

    # ---------- 4. 缓存 ----------

    async def test_second_load_hits_cache(self):
        await runtime_config.apply_runtime_settings()
        await runtime_config.apply_runtime_settings()
        self.assertEqual(self.counter["queries"], 1, "300s 内应只回源一次 DB")

    async def test_invalidate_forces_reload(self):
        self._db({"free_time_hours": "1"})
        await runtime_config.apply_runtime_settings()
        self.assertEqual(settings.FREE_TIME_LIMIT, 3600)

        self._db({"free_time_hours": "2"})
        await runtime_config.invalidate_runtime_settings_cache()
        await runtime_config.apply_runtime_settings()
        self.assertEqual(settings.FREE_TIME_LIMIT, 7200)

    # ---------- 5. 副作用 ----------

    async def test_extra_words_trigger_matcher_reload(self):
        """词库是模块级缓存的正则：不重建就永远用旧词库（改了配置但审核没变化）"""
        from app.services import moderation_service

        with mock.patch.object(moderation_service, "reload_words") as reload_words:
            await runtime_config.apply_runtime_settings({"moderation_extra_words": "测试违禁词"})
        reload_words.assert_called_once()

    async def test_other_keys_do_not_reload_matcher(self):
        from app.services import moderation_service

        with mock.patch.object(moderation_service, "reload_words") as reload_words:
            await runtime_config.apply_runtime_settings({"rate_limit_guest": "200"})
        reload_words.assert_not_called()

    # ---------- 6. 凭据类（库里是密文） ----------

    async def test_secret_value_is_decrypted_before_use(self):
        """库里存的是密文：不解密就把 COS/小程序的凭据设成密文串，功能直接全挂"""
        from app.core.crypto import encrypt_secret

        self._db({"cos_secret_id": encrypt_secret("AKIDtest123456")})
        await runtime_config.apply_runtime_settings()
        self.assertEqual(settings.COS_SECRET_ID, "AKIDtest123456")

    async def test_secret_ciphertext_never_cached_in_plaintext(self):
        """Redis 开了 AOF，明文密钥会随备份落盘（同 ai_config_service 的 P1-51 取舍）"""
        from app.core.crypto import encrypt_secret

        self._db({"cos_secret_key": encrypt_secret("super-secret-key")})
        await runtime_config.apply_runtime_settings()
        cached = await self.redis.get(runtime_config._CACHE_KEY)
        self.assertIsNotNone(cached)
        self.assertNotIn("super-secret-key", cached)

    async def test_undecryptable_secret_falls_back_to_env(self):
        """SECRET_KEY 轮换后旧密文解不开：回退 env，而不是把密文当密钥用"""
        env_default = runtime_config._ENV_DEFAULTS["cos_secret_key"]
        self._db({"cos_secret_key": "enc:v2:not-a-real-token"})
        await runtime_config.apply_runtime_settings()
        self.assertEqual(settings.COS_SECRET_KEY, env_default)


class RuntimeKeyDefinitionConsistencyTests(unittest.TestCase):
    def test_secret_flag_matches_setting_definitions(self):
        """secret 标记有两处（运行期配置 / 设置项定义），漂移会导致「后台加密存、
        运行期不解密」这种最难查的组合，用测试钉住"""
        defs = {d.key: d for d in setting_service.SETTING_DEFINITIONS}
        mismatched = [
            key
            for key, spec in runtime_config.BY_SETTING_KEY.items()
            if bool(defs[key].secret) != spec.secret
        ]
        self.assertFalse(mismatched, f"secret 标记不一致：{mismatched}")


class RuntimeSettingValidationTests(unittest.TestCase):
    """后台表单的值必须先过校验，否则脏值只能等运行期静默回退"""

    def _check(self, key: str, value):
        defn = next(d for d in setting_service.SETTING_DEFINITIONS if d.key == key)
        return setting_service._validate_setting_value(defn, value)

    def test_bool_accepts_common_forms_and_normalizes(self):
        for raw in ("1", "true", "TRUE", "on", "yes"):
            with self.subTest(raw=raw):
                self.assertEqual(self._check("moderation_enabled", raw), "1")
        for raw in ("0", "false", "off", "no"):
            with self.subTest(raw=raw):
                self.assertEqual(self._check("moderation_enabled", raw), "0")

    def test_bool_rejects_garbage(self):
        with self.assertRaises(ValueError):
            self._check("moderation_enabled", "maybe")

    def test_select_rejects_unknown_option(self):
        self.assertEqual(self._check("moderation_action", "mask"), "mask")
        with self.assertRaises(ValueError):
            self._check("moderation_action", "delete-everything")

    def test_textarea_accepts_long_words_and_blank(self):
        words = "违禁词1\n违禁词2,违禁词3"
        self.assertEqual(self._check("moderation_extra_words", words), words)
        self.assertEqual(self._check("moderation_extra_words", "  "), "")

    def test_new_groups_are_registered(self):
        groups = {d.group for d in setting_service.SETTING_DEFINITIONS}
        for group in (
            "business",
            "moderation",
            "upload",
            "ratelimit",
            "search",
            "seo",
            "fetch",
            "storage",
            "miniapp",
            "mail",
        ):
            with self.subTest(group=group):
                self.assertIn(group, groups)

    def test_switch_like_settings_use_bool_type(self):
        """「开关」类配置必须用 bool 类型 —— 后台会渲染成 Switch

        用 text 类型时后台是一个文本框，让运营填「1 / 0」，既不好理解也容易填错
        （曾出现「邀请返利开关（1=开启，0=关闭）」这种表单）。
        """
        offenders = [
            d.key
            for d in setting_service.SETTING_DEFINITIONS
            if "开关" in d.label and d.type != "bool"
        ]
        self.assertFalse(offenders, f"这些开关项应改为 bool 类型：{offenders}")

    def test_invite_switch_is_bool_and_reward_is_number(self):
        defs = {d.key: d for d in setting_service.SETTING_DEFINITIONS}
        self.assertEqual(defs["invite_enabled"].type, "bool")
        self.assertEqual(defs["invite_reward_points"].type, "number")
        # 存储格式必须保持 "1"/"0" 与十进制串：invite_service 按 `== "1"` 判断
        self.assertEqual(self._check("invite_enabled", "true"), "1")
        self.assertEqual(self._check("invite_reward_points", "80"), "80")

    def test_every_runtime_key_has_a_definition(self):
        """登记进 RUNTIME_KEYS 却没定义 = 后台看不到、也永远改不了（静默失效）"""
        defined = {d.key for d in setting_service.SETTING_DEFINITIONS}
        missing = sorted(set(runtime_config.BY_SETTING_KEY) - defined)
        self.assertFalse(missing, f"这些运行期配置没有对应的设置项定义：{missing}")


class RateLimitRefreshTests(unittest.TestCase):
    def test_middleware_reads_limits_per_request(self):
        """后台改了限流阈值后不重启也要生效：中间件不能在 __init__ 里把值钉死"""
        from app.core.rate_limit import RateLimitMiddleware

        mw = RateLimitMiddleware(app=None)
        original = settings.RATE_LIMIT_GUEST
        try:
            settings.RATE_LIMIT_GUEST = original + 1
            mw._refresh_limits()
            self.assertEqual(mw.guest_limit, original + 1)
        finally:
            settings.RATE_LIMIT_GUEST = original

    def test_explicit_constructor_args_win(self):
        """测试/特殊部署显式传入的阈值不能被 settings 覆盖"""
        from app.core.rate_limit import RateLimitMiddleware

        mw = RateLimitMiddleware(app=None, guest_limit=7)
        mw._refresh_limits()
        self.assertEqual(mw.guest_limit, 7)


if __name__ == "__main__":
    unittest.main()
