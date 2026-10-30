"""写操作幂等（`Idempotency-Key`）的回归测试。

幂等做错的两种典型表现都不报错：
- **太弱**：同一个 key 第二次还是执行了一遍 —— 用户以为「只是重试」，实际多删了一次；
- **太强**：把 4xx/5xx 也缓存了 —— 用户改完参数重试，拿到的还是上次那条失败响应，
  于是「怎么改都不行」，只能换个账号来试。

所以这里用一个只含计数器的迷你应用把契约钉死，不依赖数据库与真实端点。
"""

import json
import unittest

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.core import idempotency
from app.core.idempotency import IdempotencyMiddleware
from tests.conftest import FakeRedis

ADMIN_PATH = "/api/v1/admin/thing"


def _build_app(counter: dict) -> FastAPI:
    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware)

    @app.post(ADMIN_PATH)
    async def create_thing():
        counter["n"] += 1
        return {"code": 0, "message": "ok", "data": {"n": counter["n"]}}

    @app.post("/api/v1/public/thing")
    async def create_public():
        counter["public"] += 1
        return {"code": 0, "message": "ok", "data": {"n": counter["public"]}}

    @app.post("/api/v1/admin/boom")
    async def boom():
        counter["boom"] += 1
        return JSONResponseError()

    @app.post("/api/v1/admin/stream")
    async def stream():
        counter["stream"] += 1

        async def gen():
            yield b"data: a\n\n"
            yield b"data: b\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def JSONResponseError():
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=500, content={"code": 500, "message": "boom", "data": None})


class IdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRedis()
        self._orig = idempotency.redis_client
        idempotency.redis_client = self.fake
        self.addCleanup(self._restore)
        self.counter = {"n": 0, "public": 0, "boom": 0, "stream": 0}
        self.client = TestClient(_build_app(self.counter), raise_server_exceptions=False)

    def _restore(self):
        idempotency.redis_client = self._orig

    def _post(self, path=ADMIN_PATH, key=None, **kwargs):
        headers = {"Idempotency-Key": key} if key else {}
        return self.client.post(path, headers=headers, **kwargs)

    # ---------- 基本语义 ----------

    def test_same_key_executes_once(self):
        first = self._post(key="k1")
        second = self._post(key="k1")

        self.assertEqual(self.counter["n"], 1, "同一个 key 的第二次请求不该再执行业务逻辑")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), second.json(), "重放应返回与首次完全一致的响应")
        self.assertEqual(second.headers.get("x-idempotent-replay"), "true")

    def test_different_keys_execute_separately(self):
        self._post(key="k1")
        self._post(key="k2")
        self.assertEqual(self.counter["n"], 2)

    def test_without_key_behavior_unchanged(self):
        """不带 key = 完全不生效：这是「可选增强」，不能悄悄改变既有调用方"""
        self._post()
        self._post()
        self.assertEqual(self.counter["n"], 2)

    def test_non_admin_path_is_not_deduplicated(self):
        self._post(path="/api/v1/public/thing", key="k1")
        self._post(path="/api/v1/public/thing", key="k1")
        self.assertEqual(self.counter["public"], 2, "内容/认证接口不该被管理端幂等中间件影响")

    def test_key_is_scoped_to_credential(self):
        """不同会话用同一个 key 互不干扰（否则别人的 key 能挡住你的写操作）"""
        self._post(key="shared")
        res = self.client.post(
            ADMIN_PATH, headers={"Idempotency-Key": "shared", "Authorization": "Bearer other-token"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.counter["n"], 2)

    # ---------- 失败与重试 ----------

    def test_failed_response_is_not_cached(self):
        """5xx 不缓存：清掉占位符，允许调用方重试（缓存失败会让重试永远拿到同一条错误）"""
        first = self._post(path="/api/v1/admin/boom", key="k1")
        second = self._post(path="/api/v1/admin/boom", key="k1")
        self.assertEqual(first.status_code, 500)
        self.assertEqual(second.status_code, 500)
        self.assertEqual(self.counter["boom"], 2, "失败响应不该被幂等缓存挡住重试")

    def test_processing_marker_returns_409(self):
        """占位符还在 = 上一个同 key 请求还没跑完，此时应让调用方等，而不是再执行一遍"""
        import asyncio

        key = idempotency._key_for(
            {"headers": [(b"authorization", b""), (b"cookie", b"")]}, "inflight"
        )
        asyncio.run(self.fake.set(key, json.dumps({"state": "processing"}), ex=60))

        res = self._post(key="inflight")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(self.counter["n"], 0, "处理中的请求不该触发第二次执行")

    # ---------- 流式响应 ----------

    def test_streaming_response_is_passed_through(self):
        """SSE 一旦被缓冲就等于「生成完才返回」，必须透传且**不缓存**"""
        with self.client.stream("POST", "/api/v1/admin/stream", headers={"Idempotency-Key": "s1"}) as res:
            chunks = [chunk for chunk in res.iter_bytes()]
        self.assertEqual(res.status_code, 200)
        self.assertEqual(b"".join(chunks), b"data: a\n\ndata: b\n\n")

        # 第二次同 key 仍然真的执行：流式响应没有被缓存成一条静态 body
        with self.client.stream("POST", "/api/v1/admin/stream", headers={"Idempotency-Key": "s1"}) as res2:
            list(res2.iter_bytes())
        self.assertEqual(self.counter["stream"], 2)
        self.assertIsNone(res2.headers.get("x-idempotent-replay"))


if __name__ == "__main__":
    unittest.main()
