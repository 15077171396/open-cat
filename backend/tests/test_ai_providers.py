"""多 AI 模型 Provider 的契约测试（缺口台账 P2-4）。

三家 Provider 共用同一份 OpenAI 兼容实现（`openai_compat.OpenAICompatProvider`），
本文件钉死的是**换了 Provider 就会失效、且不报错**的几件事：

1. **注册表必须齐全**：新增 Provider 却忘了加进 `_PROVIDER_MODULES`，运行时会被当成
   「未知模型」静默回退到 DeepSeek —— 工具照常返回结果，只是模型不对。
2. **未知模型按回退名缓存**：原实现会把实例缓存到「请求的那个未知名字」下，
   每个拼错的名字都会新建一个 httpx 客户端（连接池泄漏）。
3. **未配置 Key 必须明确报错**，不能发出必然 401 的请求、也不能静默换模型。
4. **协议细节不能漂移**：HTTP 200 + `{"error": ...}`（限流/风控）、`choices` 为空、
   `usage` 缺失时不得沿用上一次的 token 数 —— 这些坑当初只在 DeepSeek 上踩过，
   抽公共基类后必须对三家都成立。
5. **前端选项与后端注册表一致**：任一侧多/少一项都是静默失效（选不中，或选中后被回退）。
"""

import asyncio
import io
import json
import re
import types
import unittest
from pathlib import Path
from unittest import mock

import httpx

from app.config import settings
from app.core.exceptions import AppException
from app.services.ai import factory
from app.services.ai.deepseek_provider import DeepSeekProvider
from app.services.ai.openai_provider import OpenAIProvider
from app.services.ai.qwen_provider import QwenProvider

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_EDIT = REPO_ROOT / "admin-frontend" / "src" / "pages" / "Tools" / "Edit.tsx"

# sorted 后的注册名（registry 按名字排序返回）
# custom = 「自定义（OpenAI 兼容）」，2026-10-27 新增：接 Moonshot/智谱/Ollama 等
# dashscope_image = 通义万相文生图（image 模态，不能当文本模型用）
EXPECTED_PROVIDERS = ["custom", "custom_image", "dashscope_image", "deepseek", "openai", "qwen"]


def _json_response(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _stub_client(provider, handler) -> httpx.AsyncClient:
    """把 provider 的 httpx 客户端换成 MockTransport（不产生任何真实网络请求）。

    刻意用与生产**相同**的 base_url / headers 构造，这样 URL 拼接与鉴权头
    也在测试覆盖范围内（base_url 少写/多写斜杠会让请求 404，且只在线上暴露）。
    """
    return httpx.AsyncClient(
        base_url=provider.base_url,
        headers={"Authorization": f"Bearer {provider.api_key}"},
        transport=httpx.MockTransport(handler),
    )


class _ProviderTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        factory._providers.clear()

    async def asyncTearDown(self):
        factory._providers.clear()


class TestRegistry(_ProviderTestBase):
    def test_all_providers_registered(self):
        self.assertEqual(factory.registered_provider_names(), EXPECTED_PROVIDERS)

    def test_default_model_is_a_registered_provider(self):
        self.assertIn(settings.DEFAULT_AI_MODEL, factory.registered_provider_names())

    async def test_get_provider_returns_matching_implementation(self):
        self.assertIsInstance(await factory.get_provider("deepseek"), DeepSeekProvider)
        self.assertIsInstance(await factory.get_provider("openai"), OpenAIProvider)
        self.assertIsInstance(await factory.get_provider("qwen"), QwenProvider)

    async def test_get_provider_is_singleton(self):
        self.assertIs(
            await factory.get_provider("qwen"), await factory.get_provider("qwen")
        )

    async def test_none_uses_default_model(self):
        provider = await factory.get_provider(None)
        default = await factory.get_provider(settings.DEFAULT_AI_MODEL)
        self.assertIsInstance(provider, type(default))

    async def test_unknown_model_falls_back_and_caches_under_fallback_name(self):
        """回退后必须按**回退名**缓存，否则每个拼错的名字都新建一个 httpx 客户端。"""
        fallback = await factory.get_provider(settings.DEFAULT_AI_MODEL)
        bogus = await factory.get_provider("gpt-4-turbo-typo")
        self.assertIs(bogus, fallback)
        self.assertNotIn("gpt-4-turbo-typo", factory._providers)
        self.assertIs(await factory.get_provider("gpt-4-turbo-typo"), fallback)


class TestConfiguration(_ProviderTestBase):
    def test_default_models_come_from_settings(self):
        self.assertEqual(DeepSeekProvider().default_model, settings.DEEPSEEK_MODEL)
        self.assertEqual(OpenAIProvider().default_model, settings.OPENAI_MODEL)
        self.assertEqual(QwenProvider().default_model, settings.QWEN_MODEL)

    def test_base_urls_come_from_settings(self):
        self.assertEqual(OpenAIProvider().base_url, settings.OPENAI_BASE_URL)
        self.assertEqual(QwenProvider().base_url, settings.QWEN_BASE_URL)

    async def test_unconfigured_provider_raises_clear_error(self):
        """未配 Key 时给明确业务错误，而不是发出必然 401 的请求。"""
        for cls in (DeepSeekProvider, OpenAIProvider, QwenProvider):
            with mock.patch.object(settings, "DEEPSEEK_API_KEY", ""), \
                 mock.patch.object(settings, "OPENAI_API_KEY", ""), \
                 mock.patch.object(settings, "QWEN_API_KEY", ""):
                provider = cls()
                self.assertFalse(provider.is_configured)
                called = False

                def handler(request):  # pragma: no cover - 不应被调用
                    nonlocal called
                    called = True
                    return _json_response({})

                provider.client = _stub_client(provider, handler)
                with self.assertRaises(AppException) as ctx:
                    await provider.chat([{"role": "user", "content": "hi"}])
                self.assertEqual(ctx.exception.code, 503)
                self.assertIn("未配置", ctx.exception.message)
                self.assertFalse(called, "未配置时不应发出任何 HTTP 请求")

    async def test_configured_flag_true_when_key_present(self):
        with mock.patch.object(settings, "QWEN_API_KEY", "sk-test"):
            self.assertTrue(QwenProvider().is_configured)


class TestProtocolContract(_ProviderTestBase):
    """协议契约对三家都成立 —— 这是抽出公共基类的意义。"""

    def _providers(self):
        with mock.patch.object(settings, "DEEPSEEK_API_KEY", "k"), \
             mock.patch.object(settings, "OPENAI_API_KEY", "k"), \
             mock.patch.object(settings, "QWEN_API_KEY", "k"):
            return [DeepSeekProvider(), OpenAIProvider(), QwenProvider()]

    async def test_request_shape_and_url_join(self):
        for provider in self._providers():
            captured = {}

            def handler(request: httpx.Request):
                captured["url"] = str(request.url)
                captured["auth"] = request.headers.get("authorization")
                captured["body"] = request.read().decode()
                return _json_response(
                    {"choices": [{"message": {"content": "你好"}}],
                     "usage": {"prompt_tokens": 3, "completion_tokens": 5}}
                )

            provider.client = _stub_client(provider, handler)
            out = await provider.chat([{"role": "user", "content": "hi"}], max_tokens=11)

            self.assertEqual(out, "你好")
            # base_url 拼接：少/多一个斜杠都会让线上请求 404
            self.assertEqual(
                captured["url"],
                provider.base_url.rstrip("/") + "/chat/completions",
            )
            self.assertEqual(captured["auth"], "Bearer k")
            body = json.loads(captured["body"])
            self.assertEqual(body["model"], provider.default_model)
            self.assertEqual(body["max_tokens"], 11)
            self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])
            self.assertEqual(provider.last_usage["prompt_tokens"], 3)

    async def test_explicit_model_overrides_default(self):
        for provider in self._providers():
            captured = {}

            def handler(request: httpx.Request):
                captured["body"] = request.read().decode()
                return _json_response({"choices": [{"message": {"content": "ok"}}]})

            provider.client = _stub_client(provider, handler)
            await provider.chat([{"role": "user", "content": "x"}], model="custom-model-1")
            self.assertEqual(json.loads(captured["body"])["model"], "custom-model-1")

    async def test_http_200_with_error_body_raises_503(self):
        """限流/风控时服务端以 HTTP 200 返回 {"error": ...}，裸取 choices 会落入全局 500。"""
        for provider in self._providers():
            provider.client = _stub_client(
                provider, lambda req: _json_response({"error": {"code": 429, "message": "rate"}})
            )
            with self.assertRaises(AppException) as ctx:
                await provider.chat([{"role": "user", "content": "x"}])
            self.assertEqual(ctx.exception.code, 503)
            self.assertEqual(provider.last_usage, {})

    async def test_empty_content_raises_503(self):
        for provider in self._providers():
            provider.client = _stub_client(
                provider, lambda req: _json_response({"choices": [{"message": {"content": ""}}]})
            )
            with self.assertRaises(AppException) as ctx:
                await provider.chat([{"role": "user", "content": "x"}])
            self.assertEqual(ctx.exception.code, 503)

    async def test_missing_usage_resets_last_usage(self):
        """一次「无 usage 的成功响应」不能被误记成上一条的 token 数。"""
        for provider in self._providers():
            provider.client = _stub_client(
                provider,
                lambda req: _json_response(
                    {"choices": [{"message": {"content": "a"}}],
                     "usage": {"prompt_tokens": 99, "completion_tokens": 1}}
                ),
            )
            await provider.chat([{"role": "user", "content": "x"}])
            self.assertEqual(provider.last_usage["prompt_tokens"], 99)

            provider.client = _stub_client(
                provider, lambda req: _json_response({"choices": [{"message": {"content": "b"}}]})
            )
            await provider.chat([{"role": "user", "content": "x"}])
            self.assertEqual(provider.last_usage, {})

    async def test_http_error_status_propagates(self):
        for provider in self._providers():
            provider.client = _stub_client(
                provider, lambda req: _json_response({"error": "unauthorized"}, status=401)
            )
            with self.assertRaises(httpx.HTTPStatusError):
                await provider.chat([{"role": "user", "content": "x"}])

    async def test_close_releases_client(self):
        for provider in self._providers():
            await provider.close()
            self.assertTrue(provider.client.is_closed)


class TestFrontendModelOptions(unittest.TestCase):
    """工具编辑页的模型下拉必须来自模型管理接口，而不是前端写死的供应商清单。

    写死清单会与 ai_models 漂移：后台新增模型选不到、删除的模型仍显示，
    且不会报错。这里钉死「必须请求 /admin/ai-models/options」与「不允许硬编码清单」。
    """

    @classmethod
    def setUpClass(cls):
        cls.source = io.open(TOOLS_EDIT, encoding="utf-8").read()

    def test_tool_editor_fetches_model_options(self):
        self.assertIn("/admin/ai-models/options", self.source)

    def test_tool_editor_has_no_hardcoded_provider_list(self):
        self.assertIsNone(
            re.search(r"const AI_MODEL_OPTIONS = \[", self.source),
            "工具编辑页又出现了硬编码 AI_MODEL_OPTIONS —— 会与模型管理漂移",
        )


class TestUsageOutIsolation(_ProviderTestBase):
    """用量必须按**调用**取回，不能靠读单例上的共享属性（第三轮增量审查 P1-44）。

    背景：provider 是进程级单例（`factory` 按名字缓存），而翻译会并发复用同一实例。
    旧实现把 usage 写在 `self.last_usage` 上，只有在「`await chat()` 返回后**立刻**读」
    时才正确 —— 一旦中间插入 await、或把调用包进新 Task
    （**Python ≤3.11 的 `asyncio.wait_for` 就是 `ensure_future` 包 Task**，
    而 CI / 生产的 Python 正是 3.11），并发调用就会互相覆盖，
    配额与成本看板在 AI 流量最高时静默失真。
    """

    def _provider(self) -> DeepSeekProvider:
        with mock.patch.object(settings, "DEEPSEEK_API_KEY", "k"):
            provider = DeepSeekProvider()
        # 响应里的 usage 由请求里的提示词决定，便于分辨「这份 usage 是谁的」
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.read().decode())
            n = int(body["messages"][0]["content"])
            return _json_response({
                "choices": [{"message": {"content": f"ok-{n}"}}],
                "usage": {"prompt_tokens": n, "completion_tokens": n + 1},
            })

        provider.client = _stub_client(provider, handler)
        return provider

    async def test_concurrent_calls_keep_usage_isolated(self):
        """并发（且各自跨任务边界）时，每份 usage_out 只能拿到自己的值"""
        provider = self._provider()
        sinks: dict[int, dict] = {}

        async def one(n: int) -> str:
            sink: dict = {}
            sinks[n] = sink
            # 刻意用 create_task 跨任务边界 —— 复现 ≤3.11 上 wait_for 的行为
            return await asyncio.create_task(
                provider.chat([{"role": "user", "content": str(n)}], usage_out=sink)
            )

        results = await asyncio.gather(one(11), one(22), one(33))

        self.assertEqual(results, ["ok-11", "ok-22", "ok-33"])
        self.assertEqual(
            {n: s.get("prompt_tokens") for n, s in sinks.items()},
            {11: 11, 22: 22, 33: 33},
            "usage 被并发调用互相覆盖了 —— 记账会静默算错",
        )

    async def test_usage_out_is_cleared_when_response_has_no_usage(self):
        """无 usage 的成功响应必须把出参清空，不能留下上一次的 token 数"""
        provider = self._provider()
        sink: dict = {"prompt_tokens": 999}

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"choices": [{"message": {"content": "x"}}]})

        provider.client = _stub_client(provider, handler)
        await provider.chat([{"role": "user", "content": "1"}], usage_out=sink)
        self.assertEqual(sink, {})


class ProviderRebuildRaceTests(_ProviderTestBase):
    """配置轮换时的重建竞态（第四轮审查 P2-46）。

    `get_provider` 的「比指纹 → close 旧实例 → 建新 → 写回 dict」中间有 await。
    没有锁时两个并发请求都读到旧实例、都 close、都新建，后写的把前一个覆盖掉，
    而**被覆盖那个实例的 httpx.AsyncClient（池上限 100 套接字）永不 aclose**。
    管理员每保存一次 Key（`invalidate_ai_config_cache`）就漏一个 client/worker。
    """

    class _NoLock:
        """把锁退化成直通 —— 用来证明本用例真的能抓到竞态，而不是恒绿"""

        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    def _install_stub_factory(self, created: list, closed: list):
        class _StubProvider:
            provider_name = "stub"
            config_fingerprint: tuple = ()

            def __init__(self, config):
                self.config = config

            async def close(self):
                # 真实 close 是 httpx aclose，必然让出事件循环 —— 这正是竞态的插入点
                await asyncio.sleep(0)
                closed.append(self)

            def __repr__(self):
                return f"<Stub {self.config_fingerprint}>"

        def fake_provider_factory(name: str, modality: str = "text"):
            def build(config):
                inst = _StubProvider(config)
                created.append(inst)
                return inst

            return build

        async def fake_config(ref: str | None, modality: str = "text"):
            name = ref or settings.DEFAULT_AI_MODEL
            return types.SimpleNamespace(
                fingerprint=("rotated-key",),
                provider=name,
                code=name,
                modality=modality,
                image_price=0.0,
            )

        return (
            mock.patch.object(factory, "provider_factory", fake_provider_factory),
            mock.patch.object(factory, "get_runtime_config", fake_config),
            _StubProvider,
        )

    async def _gather_two(self, name: str):
        return await asyncio.gather(factory.get_provider(name), factory.get_provider(name))

    async def test_concurrent_rebuild_creates_exactly_one_instance(self):
        created: list = []
        closed: list = []
        patch_factory, patch_config, Stub = self._install_stub_factory(created, closed)
        stale = Stub(("old-key",))
        stale.config_fingerprint = ("old-key",)
        factory._providers["deepseek"] = stale

        with patch_factory, patch_config:
            a, b = await self._gather_two("deepseek")

        self.assertIs(a, b, "并发请求拿到两个不同实例，说明重建没串行化")
        self.assertEqual(len(created), 1, f"多建了 client（实际新建 {len(created)} 个）→ 会被覆盖并泄漏")
        self.assertEqual(closed, [stale], "旧实例必须且只能被关闭一次")
        self.assertIs(factory._providers["deepseek"], a)

    async def test_without_the_lock_the_race_leaks_an_instance(self):
        """自证有牙：把锁换成直通，同一场景必须复现「建两个、盖掉一个」。"""
        created: list = []
        closed: list = []
        patch_factory, patch_config, Stub = self._install_stub_factory(created, closed)
        stale = Stub(("old-key",))
        stale.config_fingerprint = ("old-key",)
        factory._providers["deepseek"] = stale
        real_lock = factory._providers_lock

        factory._providers_lock = self._NoLock()
        try:
            with patch_factory, patch_config:
                a, b = await self._gather_two("deepseek")
        finally:
            factory._providers_lock = real_lock

        self.assertEqual(len(created), 2, "无锁路径没有复现竞态 → 上面那条用例是恒绿的假守卫")
        self.assertIsNot(a, b)
        leaked = {id(x) for x in created} - {id(factory._providers["deepseek"])}
        self.assertEqual(len(leaked), 1, "被覆盖的实例仍在 dict 之外，且没有任何代码会关它")


if __name__ == "__main__":
    unittest.main()
