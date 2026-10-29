"""AI 配置（后台可配 Provider 凭据）契约测试（2026-10-10 新增）。

钉死的是**不报错但会出事**的几类问题：

1. **密钥绝不能出现在公开设置里** —— `/api/v1/settings` 是发给**所有访客**的，
   而它的过滤条件原本是「非 richtext 全放行」，新增密钥项后会原样外泄；
2. **密钥必须加密落库** —— DB 备份、慢查询日志、误导出都不该直接暴露 Key；
3. **管理端只该看到掩码**（末 4 位），不应拿到明文；
4. **提交掩码不得覆盖真实值** —— 前端回显值被误提交时不能把掩码写进库；
5. **后台未配置时逐项回退环境变量** —— 这是「升级零行为变更」的保证；
6. **解密失败（SECRET_KEY 轮换）回退而不是崩**；
7. **前端设置页必须登记 `ai` 分组** —— GROUP_ORDER 是白名单，
   漏登记会让整个 AI 面板**不显示且不报错**（同类坑：样式元数据 key 对不上）。
"""

import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import select

from app.config import settings
from app.core import crypto
from app.models.system_setting import SystemSetting
from app.services import ai_config_service as aicfg
from app.services import setting_service
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PAGE = REPO_ROOT / "admin-frontend" / "src" / "pages" / "Settings" / "index.tsx"
# 设置项的**渲染实现**在 2026-10-27 抽成了共享组件（系统设置与登录管理复用同一套
# 字段渲染 / 掩码 / 草稿逻辑）。前端的「必须这么写」类断言因此要同时扫这两个文件，
# 否则守卫会变成「扫了个空壳页面」而恒绿。
SETTINGS_FIELD = (
    REPO_ROOT / "admin-frontend" / "src" / "components" / "settings" / "SettingField.tsx"
)

SECRET_KEYS = {d.key for d in setting_service.SETTING_DEFINITIONS if d.secret}


class _SessionCtx:
    """把 MemorySession 包装成 async 上下文管理器（fake_db 未实现 __aenter__）"""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _failing_session():
    """让 async_session() 立即抛错，避免测试里去连不可达的 MySQL"""
    raise RuntimeError("DB 不可达（测试桩）")


class CryptoRoundTripTests(unittest.TestCase):
    def test_round_trip(self):
        cipher = crypto.encrypt_secret("sk-abcdef123456")
        self.assertEqual(crypto.decrypt_secret(cipher), "sk-abcdef123456")

    def test_ciphertext_hides_plaintext_and_is_prefixed(self):
        cipher = crypto.encrypt_secret("sk-verysecret")
        # v2 = PBKDF2 派生（第二轮增量审查 P2；v1 只保留解密能力，不再用于加密）
        self.assertTrue(cipher.startswith("enc:v2:"))
        self.assertNotIn("sk-verysecret", cipher)

    def test_legacy_v1_ciphertext_still_decrypts(self):
        """升级到 v2 后**存量 v1 密文必须仍可读** —— 否则所有已配置的 AI Key
        会在上线瞬间失效，必须人工重配（这是「零破坏迁移」的守门测试）"""
        legacy = "enc:v1:" + crypto._fernet_legacy().encrypt(b"sk-legacy").decode("ascii")
        self.assertTrue(crypto.is_encrypted(legacy))
        self.assertEqual(crypto.decrypt_secret(legacy), "sk-legacy")

    def test_empty_stays_empty(self):
        self.assertEqual(crypto.encrypt_secret(""), "")
        self.assertIsNone(crypto.decrypt_secret(""))
        self.assertIsNone(crypto.decrypt_secret(None))

    def test_mask_keeps_last_four(self):
        mask = crypto.mask_secret(crypto.encrypt_secret("sk-abcdef1234"))
        self.assertTrue(mask.startswith(crypto.MASK_PREFIX))
        self.assertTrue(mask.endswith("1234"))
        self.assertNotIn("abcdef", mask)

    def test_short_secret_masks_entirely(self):
        """短于 4 位时不能把整个密钥当末 4 位暴露出去"""
        self.assertEqual(crypto.mask_secret(crypto.encrypt_secret("ab")), crypto.MASK_PREFIX)

    def test_plaintext_history_is_still_readable(self):
        """本功能上线前写入的明文值（无前缀）必须仍能读到，否则会静默回退 env"""
        self.assertEqual(crypto.decrypt_secret("sk-legacy-plain"), "sk-legacy-plain")

    def test_decrypt_with_rotated_key_returns_none(self):
        """SECRET_KEY 轮换后解密失败必须返回 None（调用方回退），而不是抛异常"""
        cipher = crypto.encrypt_secret("sk-abcdef")
        # 密钥派生结果被 lru_cache 缓存（PBKDF2 开销刻意较大，不能每次加解密都算），
        # 故模拟轮换必须同时清缓存 —— 否则测试会继续用旧密钥而**假绿**
        crypto._fernet.cache_clear()
        try:
            with mock.patch.object(crypto.settings, "SECRET_KEY", "rotated-key-" + "x" * 32):
                self.assertIsNone(crypto.decrypt_secret(cipher))
        finally:
            crypto._fernet.cache_clear()


class DefinitionTests(unittest.TestCase):
    def test_secret_items_are_marked(self):
        """凡是「凭据类」的项都必须标 secret（加密落库 + 只回显掩码 + 不进公开设置）

        这份清单同时是**安全审计表**：新增任何凭据类设置项都要登记进来，
        否则它会被明文存进 system_settings、并以明文出现在管理端接口里。
        """
        self.assertEqual(
            SECRET_KEYS,
            {
                # 收录推送 / 第三方导入
                "baidu_push_token",
                "indexnow_key",
                "github_token",
                "hf_token",
                # 对象存储 / 小程序 / 邮件
                "cos_secret_id",
                "cos_secret_key",
                "wechat_mini_secret",
                "smtp_password",
                # 登录 / 短信 / 支付（2026-10-27）
                "wechat_web_secret",
                "sms_tencent_secret_id",
                "sms_tencent_secret_key",
                "sms_aliyun_access_key_secret",
                "wechat_pay_api_v3_key",
                "wechat_pay_private_key",
                "alipay_private_key",
                # 图片搜索图源密钥（2026-10-29）
                "image_search_pexels_key",
                "image_search_pixabay_key",
                "image_search_giphy_key",
                "image_search_memecub_key",
            },
        )

    def test_definitions_payload_exposes_secret_flag(self):
        payload = {d["key"]: d for d in setting_service.definitions_payload()}
        self.assertTrue(payload["cos_secret_key"]["secret"])
        self.assertFalse(payload["cos_region"]["secret"])

    def test_ai_group_is_registered(self):
        """AI 模型已迁到「模型管理」，但成本护栏仍留在系统设置，分组不能消失"""
        groups = {d.group for d in setting_service.SETTING_DEFINITIONS}
        self.assertIn("ai", groups)

    def test_model_credentials_are_no_longer_setting_definitions(self):
        """模型/凭据必须只存在于 ai_models，不能又冒出第二套设置键"""
        keys = {d.key for d in setting_service.SETTING_DEFINITIONS}
        moved = {
            "ai_default_model",
            "ai_model_prices",
            "ai_deepseek_api_key",
            "ai_deepseek_base_url",
            "ai_deepseek_model",
            "ai_openai_api_key",
            "ai_openai_base_url",
            "ai_openai_model",
            "ai_qwen_api_key",
            "ai_qwen_base_url",
            "ai_qwen_model",
        }
        self.assertEqual(keys & moved, set())


class PublicSettingsLeakTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig_redis = setting_service.redis_client
        setting_service.redis_client = FakeRedis()
        self._orig_session = setting_service.async_session
        setting_service.async_session = _failing_session

    async def asyncTearDown(self):
        setting_service.redis_client = self._orig_redis
        setting_service.async_session = self._orig_session

    async def test_public_settings_never_contain_secrets(self):
        """核心安全断言：公开设置（发给所有访客）不得含任何密钥项"""
        merged = await setting_service.get_public_settings()
        leaked = SECRET_KEYS & set(merged)
        self.assertEqual(leaked, set(), f"密钥类配置泄漏到公开设置: {leaked}")

    async def test_public_settings_still_contain_normal_items(self):
        """确认上一条不是因为「整个接口都空了」而通过（防假绿）"""
        merged = await setting_service.get_public_settings()
        self.assertIn("site_name", merged)
        self.assertIn("site_logo", merged)

    async def test_ai_ops_config_is_not_public(self):
        """AI 的 base_url / model 属运维配置，不该发给访客

        非密钥但同样不该外发 —— 公开 base_url 等于告诉所有人「是否用了中转服务商」。
        靠 SettingDefinition.public=False 排除。
        """
        merged = await setting_service.get_public_settings()
        leaked = {k for k in merged if k.startswith("ai_")}
        self.assertEqual(leaked, set(), f"AI 运维配置泄漏到公开设置: {leaked}")


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = MemorySession()

    async def _rows(self):
        return {
            r.setting_key: r.value
            for r in (await self.db.execute(select(SystemSetting))).scalars().all()
        }

    async def test_secret_is_encrypted_in_db(self):
        await setting_service.update_settings(
            self.db, {"cos_secret_key": "sk-live-abcdef123456"}
        )
        await self.db.commit()
        stored = (await self._rows())["cos_secret_key"]
        self.assertTrue(crypto.is_encrypted(stored))
        self.assertNotIn("sk-live-abcdef123456", stored)
        self.assertEqual(crypto.decrypt_secret(stored), "sk-live-abcdef123456")

    async def test_mask_submission_is_skipped(self):
        """把掩码提交上来必须被忽略，不能覆盖真实值"""
        await setting_service.update_settings(self.db, {"cos_secret_key": "sk-real-key-1"})
        await self.db.commit()
        before = (await self._rows())["cos_secret_key"]

        changed = await setting_service.update_settings(
            self.db, {"cos_secret_key": crypto.mask_secret(before)}
        )
        await self.db.commit()
        self.assertEqual(changed, 0, "掩码被当成了修改")
        self.assertEqual((await self._rows())["cos_secret_key"], before)

    async def test_unchanged_secret_is_not_rewritten(self):
        """同样的明文再次提交不应算作变更（密文含随机 IV，直接比密文会误判）"""
        await setting_service.update_settings(self.db, {"cos_secret_key": "sk-same-key"})
        await self.db.commit()
        changed = await setting_service.update_settings(
            self.db, {"cos_secret_key": "sk-same-key"}
        )
        self.assertEqual(changed, 0)

    async def test_empty_value_clears_and_falls_back(self):
        await setting_service.update_settings(self.db, {"cos_secret_key": "sk-temp"})
        await self.db.commit()
        await setting_service.update_settings(self.db, {"cos_secret_key": ""})
        await self.db.commit()
        self.assertNotIn("cos_secret_key", await self._rows())

    async def test_stored_settings_masks_secret(self):
        await setting_service.update_settings(
            self.db, {"cos_secret_key": "sk-live-abcdef9999"}
        )
        await self.db.commit()
        values = await setting_service.get_stored_settings(self.db)
        self.assertTrue(values["cos_secret_key"].startswith(crypto.MASK_PREFIX))
        self.assertNotIn("abcdef", values["cos_secret_key"])

    async def test_get_secret_setting_returns_plaintext(self):
        """内部取明文的口子必须存在（否则运行时拿不到 Key）"""
        await setting_service.update_settings(self.db, {"cos_secret_key": "sk-internal"})
        await self.db.commit()
        self.assertEqual(
            await setting_service.get_secret_setting(self.db, "cos_secret_key"),
            "sk-internal",
        )

    async def test_get_secret_setting_rejects_non_secret_key(self):
        with self.assertRaises(ValueError):
            await setting_service.get_secret_setting(self.db, "site_name")


class ProviderConfigTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        aicfg._LOCAL_CACHE.clear()
        self._orig_redis = aicfg.redis_client
        aicfg.redis_client = FakeRedis()
        self._orig_session = aicfg.async_session

    async def asyncTearDown(self):
        aicfg.redis_client = self._orig_redis
        aicfg.async_session = self._orig_session
        aicfg._LOCAL_CACHE.clear()

    async def test_falls_back_to_env_when_db_unreachable(self):
        """后台没配 + 库读不到 → 必须回退环境变量（零行为变更）"""
        aicfg.async_session = _failing_session
        cfg = await aicfg.get_provider_config("deepseek")
        self.assertEqual(cfg.api_key, settings.DEEPSEEK_API_KEY)
        self.assertEqual(cfg.base_url, settings.DEEPSEEK_BASE_URL)

    async def test_db_overrides_env(self):
        db = MemorySession()
        db.add(SystemSetting(setting_key="ai_deepseek_api_key", value=crypto.encrypt_secret("sk-from-db")))
        db.add(SystemSetting(setting_key="ai_deepseek_model", value="deepseek-reasoner"))
        # fake_db 的 add 只进 pending，必须 commit（触发 flush）后才可查到
        await db.commit()
        aicfg.async_session = lambda: _SessionCtx(db)

        cfg = await aicfg.get_provider_config("deepseek")
        self.assertEqual(cfg.api_key, "sk-from-db")
        self.assertEqual(cfg.default_model, "deepseek-reasoner")
        # 未配置的项仍回退环境变量
        self.assertEqual(cfg.base_url, settings.DEEPSEEK_BASE_URL)

    async def test_redis_cache_never_holds_plaintext_key(self):
        """Redis 侧只能出现**密文**（第四轮全量审查 P1-51）

        生产 compose 里 Redis 开了 `--appendonly yes` —— 写进缓存的东西会随 AOF
        落到宿主卷与备份。原实现缓存的是解密后的明文 API Key，等于绕过
        `core/crypto.py` 整套「密钥不落明文存储」的设计；解密只应发生在
        `_materialize`，明文只存在于进程内。
        """
        db = MemorySession()
        db.add(SystemSetting(
            setting_key="ai_deepseek_api_key", value=crypto.encrypt_secret("sk-live-leakcheck")
        ))
        await db.commit()
        aicfg.async_session = lambda: _SessionCtx(db)
        aicfg._LOCAL_CACHE.clear()
        fake = aicfg.redis_client

        cfg = await aicfg.get_provider_config("deepseek")
        # 行为不变：调用方仍拿到解密后的可用 Key
        self.assertEqual(cfg.api_key, "sk-live-leakcheck")

        cached = str(fake._store)
        self.assertNotIn("sk-live-leakcheck", cached, "明文密钥进入了 Redis")
        self.assertIn("api_key_enc", cached)

    async def test_legacy_plaintext_cache_entry_is_treated_as_stale(self):
        """升级窗口：旧格式的缓存条目（含明文 api_key）必须视为失效并回源"""
        db = MemorySession()
        db.add(SystemSetting(
            setting_key="ai_deepseek_api_key", value=crypto.encrypt_secret("sk-from-db")
        ))
        await db.commit()
        aicfg.async_session = lambda: _SessionCtx(db)
        aicfg._LOCAL_CACHE.clear()
        await aicfg.redis_client.set("ai_cfg:deepseek", '{"api_key": "sk-legacy-plain", "base_url": "", "default_model": ""}', ex=60)

        cfg = await aicfg.get_provider_config("deepseek")
        self.assertEqual(cfg.api_key, "sk-from-db", "旧明文条目被当成了有效缓存")
        self.assertNotIn("sk-legacy-plain", str(aicfg.redis_client._store))

    async def test_unknown_provider_raises_instead_of_silent_fallback(self):
        """未注册的名字必须抛错（P1-40，no silent fallback）

        原实现静默回退到 DEFAULT_AI_MODEL —— 一个「只登记了一半」的新
        Provider 会用 DeepSeek 的 key/base_url 构造出真实实例，不报错、
        不打日志，只在账单与回答质量上体现。未知名字属调用方 bug。
        """
        aicfg.async_session = _failing_session
        with self.assertRaises(ValueError):
            await aicfg.get_provider_config("no-such-provider")

    async def test_local_cache_avoids_repeat_probe(self):
        """进程内缓存命中后不应再探测（Redis/DB 都不可用时这是关键）"""
        aicfg.async_session = _failing_session
        await aicfg.get_provider_config("qwen")
        aicfg.async_session = _failing_session
        with mock.patch.object(aicfg, "_cache_get", side_effect=AssertionError("不该再探测")):
            cfg = await aicfg.get_provider_config("qwen")
        self.assertTrue(cfg.base_url)


class FrontendGuardTests(unittest.TestCase):
    """设置页必须登记 ai 分组 —— 漏登记会让整个 AI 面板不显示且不报错"""

    @classmethod
    def setUpClass(cls):
        # 页面 + 共享渲染组件一起读：断言的是「设置页整体怎么渲染」，而不是某个文件名
        cls.source = SETTINGS_PAGE.read_text(encoding="utf-8") + SETTINGS_FIELD.read_text(
            encoding="utf-8"
        )

    def test_ai_group_is_declared_by_backend(self):
        """AI 分组必须由后端声明（EXT-05 之后分组顺序/说明由 `SETTING_GROUPS` 下发）。

        收敛前这里检查的是管理端源码里的 `GROUP_ORDER` 常量 —— 而那份常量与后端
        分组是两处事实：后端加了组、前端没加，整组设置会**静默不显示**。
        现在前端不再持有顺序表，改为断言后端声明里包含 ai 分组。
        """
        from app.services.setting_service import SETTING_GROUPS

        keys = [g.key for g in SETTING_GROUPS]
        self.assertIn("ai", keys, "后端 SETTING_GROUPS 未声明 ai 分组 —— AI 配置面板不会显示")

    def test_frontend_no_longer_hardcodes_group_order(self):
        """前端不得再出现手写分组顺序：它会把后端新增的分组整组丢掉。"""
        self.assertNotRegex(
            self.source,
            r"const\s+GROUP_ORDER\s*=",
            "管理端又出现了手写的 GROUP_ORDER —— 后端新增分组时该组会被静默丢弃",
        )

    def test_legal_editor_toolbar_sticks(self):
        """法律文书编辑器必须开 `autoHeight`，否则工具栏吸顶行程被定高容器限死

        定高（360px）时 `.ql-container` 自己滚动，sticky 行程只有约 318px，
        页面再往下滚工具栏就跟着走了。法律文书动辄几千字，必须让行程覆盖整篇。
        """
        self.assertRegex(
            self.source,
            r"<QuillEditor[\s\S]{0,400}?autoHeight",
            "法律文书编辑器未开启 autoHeight —— 工具栏只能吸顶一小段距离",
        )

    def test_password_type_is_supported(self):
        """密钥字段必须有 password 分支，且**刻意不用** antd 的 `Input.Password`。

        历史：该字段原本用 `Input.Password`，但它会渲染出 `<input type="password">`，
        触发 Chrome 的「Password field is not contained in a form」告警，
        并诱使密码管理器把 API Key 当登录密码保存/填充（对配置项是错误行为）。
        现改为普通 `Input` + `-webkit-text-security: disc` 打码（提交 38b4b725）。

        ⚠️ 本断言在 38b4b725 之后**未同步更新**，导致测试长期红着无人发现
        （代码审查 2026-10-10 发现）。现反转为「必须不用 Input.Password」，
        这样将来有人「顺手改回去」时会被立刻拦住。
        """
        self.assertIn("'password'", self.source, "设置页缺少 password 类型分支")
        self.assertNotIn(
            "Input.Password",
            self.source,
            "密钥输入框不得使用 Input.Password —— 会触发 Chrome 表单告警并干扰密码管理器",
        )
        self.assertIn(
            "WebkitTextSecurity",
            self.source,
            "密钥打码应走 CSS -webkit-text-security（见 38b4b725）",
        )


if __name__ == "__main__":
    unittest.main()
