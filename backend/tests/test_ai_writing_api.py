"""AI 写作 HTTP 接口：SSE 事件、额度归还与提示词 CRUD 的路由级行为。

说明：brief 里的 ``make_test_app`` 在当前 conftest 中不存在，等价夹具是
``async_client``（同样覆盖 get_db/get_current_user 并 yield AsyncClient）。
"""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from app.api import admin_ai_writing
from app.core.exceptions import AppException
from app.services.ai_usage_service import ImageQuotaTicket
from app.services.ai_writing import prompts as prompts_module
from app.services.ai_writing import service
from tests.conftest import async_client, make_user


async def fake_run(req, *, usage_out, admin_id=None):
    usage_out.update({"prompt_tokens": 10, "completion_tokens": 3})
    yield "delta", {"text": "你好"}


async def finish_length_run(req, *, usage_out, admin_id=None):
    usage_out.update({"prompt_tokens": 10, "completion_tokens": 3, "finish_reason": "length"})
    yield "delta", {"text": "被截断的结果"}


async def empty_run(req, *, usage_out, admin_id=None):
    if False:  # pragma: no cover 仅为让函数成为异步生成器
        yield "delta", {}


async def slow_first_run(req, *, usage_out, admin_id=None):
    await asyncio.sleep(5)
    yield "delta", {"text": "迟到的首 token"}


def make_slow_after_first_run(closed: dict):
    async def _run(req, *, usage_out, admin_id=None):
        try:
            yield "delta", {"text": "先"}
            await asyncio.sleep(5)
            yield "delta", {"text": "后"}
        finally:
            closed["value"] = True

    return _run


async def fake_image_run(req, *, usage_out, admin_id=None):
    usage_out["images_cost"] = 0.4
    yield "image", {"index": 0, "url": "https://cdn.example.com/a.png"}
    yield "image", {"index": 1, "url": "https://cdn.example.com/b.png"}


async def boom_run(req, *, usage_out, admin_id=None):
    raise RuntimeError("upstream broken")
    yield "delta", {}  # pragma: no cover  仅为让函数成为异步生成器


async def status_then_fail_run(req, *, usage_out, admin_id=None):
    # status 只是过程事件（illustrate 的 generating）：上游还没产出任何内容就失败
    yield "status", {"stage": "generating"}
    raise AppException(code=503, message="图像生成失败，请稍后重试")


def _parse_sse(body: str) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = {}
    for block in body.split("\n\n"):
        name, data = "", None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if name:
            events.setdefault(name, []).append(data)
    return events


class WritingApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_options_requires_admin_and_returns_payload(self):
        async def fake_options(db):
            return {"enabled": True, "operations": [], "text_models": [], "image_model": None,
                    "styles": [], "sizes": [], "image_daily_limit": 30,
                    "image_used_today": 0, "hourly_limit": 60}

        with mock.patch.object(service, "get_options", new=fake_options):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.get("/api/v1/admin/ai/writing/options")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["data"]["enabled"])

    async def test_run_streams_sse_and_usage(self):
        with mock.patch.object(service, "is_enabled", new=mock.AsyncMock(return_value=True)), \
             mock.patch.object(service, "check_hourly_limit", new=mock.AsyncMock()), \
             mock.patch.object(service, "run", new=fake_run), \
             mock.patch("app.services.ai_usage_service.reserve_quota", new=mock.AsyncMock(return_value=None)), \
             mock.patch("app.services.ai_usage_service.record_writing_operation", new=mock.AsyncMock()), \
             mock.patch("app.services.ai_usage_service.get_usage_summary", new=mock.AsyncMock(return_value={
                 "limits": {"remaining_today": 10, "remaining_images": 10}
             })), \
             mock.patch("app.services.ai_usage_service.get_quota_config", new=mock.AsyncMock()), \
             mock.patch("app.services.ai_usage_service.estimate_usage_cost", return_value=None):
            async with async_client(user=make_user(role="admin")) as client:
                async with client.stream("POST", "/api/v1/admin/ai/writing/run", json={
                    "operation": "rewrite", "content_type": "node",
                    "selection": {"index": 0, "length": 2, "text": "原文"},
                    "context": {"title": "标题"},
                }) as response:
                    body = "".join([chunk async for chunk in response.aiter_text()])
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: delta", body)
        self.assertIn("event: done", body)

    async def test_done_event_carries_upstream_finish_reason(self):
        with mock.patch.object(service, "is_enabled", new=mock.AsyncMock(return_value=True)), \
             mock.patch.object(service, "check_hourly_limit", new=mock.AsyncMock()), \
             mock.patch.object(service, "run", new=finish_length_run), \
             mock.patch("app.services.ai_usage_service.reserve_quota", new=mock.AsyncMock(return_value=None)), \
             mock.patch("app.services.ai_usage_service.record_writing_operation", new=mock.AsyncMock()), \
             mock.patch("app.services.ai_usage_service.get_usage_summary", new=mock.AsyncMock(return_value={
                 "limits": {"remaining_today": 10, "remaining_images": 10}
             })), \
             mock.patch("app.services.ai_usage_service.get_quota_config", new=mock.AsyncMock()), \
             mock.patch("app.services.ai_usage_service.estimate_usage_cost", return_value=None):
            async with async_client(user=make_user(role="admin")) as client:
                async with client.stream("POST", "/api/v1/admin/ai/writing/run", json={
                    "operation": "rewrite", "content_type": "node",
                }) as response:
                    body = "".join([chunk async for chunk in response.aiter_text()])
        self.assertEqual(_parse_sse(body)["done"][0]["finish_reason"], "length")

    async def test_first_event_timeout_emits_504_and_releases_quota(self):
        ticket = object()
        with mock.patch.object(service, "is_enabled", new=mock.AsyncMock(return_value=True)), \
             mock.patch.object(service, "check_hourly_limit", new=mock.AsyncMock()), \
             mock.patch.object(service, "run", new=slow_first_run), \
             mock.patch.object(admin_ai_writing, "STREAM_FIRST_EVENT_TIMEOUT", 0.05), \
             mock.patch("app.services.ai_usage_service.reserve_quota",
                        new=mock.AsyncMock(return_value=ticket)), \
             mock.patch("app.services.ai_usage_service.release_quota",
                        new=mock.AsyncMock()) as release, \
             mock.patch("app.services.ai_usage_service.record_writing_operation",
                        new=mock.AsyncMock()):
            async with async_client(user=make_user(role="admin")) as client:
                async with client.stream("POST", "/api/v1/admin/ai/writing/run", json={
                    "operation": "rewrite", "content_type": "node",
                }) as response:
                    body = "".join([chunk async for chunk in response.aiter_text()])
        events = _parse_sse(body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(events["error"][0]["code"], 504)
        self.assertTrue(events["error"][0]["retryable"])
        self.assertNotIn("done", events)
        # 首 token 都没出来 → 本次调用没有产出，额度必须归还
        release.assert_awaited_once_with(ticket)

    async def test_total_budget_timeout_emits_504_and_closes_generator(self):
        closed = {"value": False}
        ticket = object()
        with mock.patch.object(service, "is_enabled", new=mock.AsyncMock(return_value=True)), \
             mock.patch.object(service, "check_hourly_limit", new=mock.AsyncMock()), \
             mock.patch.object(service, "run", new=make_slow_after_first_run(closed)), \
             mock.patch.object(admin_ai_writing, "STREAM_FIRST_EVENT_TIMEOUT", 5.0), \
             mock.patch.object(admin_ai_writing, "STREAM_TOTAL_TIMEOUT", 0.1), \
             mock.patch("app.services.ai_usage_service.reserve_quota",
                        new=mock.AsyncMock(return_value=ticket)), \
             mock.patch("app.services.ai_usage_service.release_quota",
                        new=mock.AsyncMock()) as release, \
             mock.patch("app.services.ai_usage_service.record_writing_operation",
                        new=mock.AsyncMock()):
            async with async_client(user=make_user(role="admin")) as client:
                async with client.stream("POST", "/api/v1/admin/ai/writing/run", json={
                    "operation": "rewrite", "content_type": "node",
                }) as response:
                    body = "".join([chunk async for chunk in response.aiter_text()])
        events = _parse_sse(body)
        self.assertEqual(events["error"][0]["code"], 504)
        self.assertNotIn("done", events)
        # 已产出过内容：不能归还额度（上游费用已发生）
        release.assert_not_awaited()
        # aclosing 必须在超时后关闭上游生成器（其 finally 已执行）
        self.assertTrue(closed["value"])

    async def test_empty_text_stream_is_error_not_empty_done(self):
        ticket = object()
        with mock.patch.object(service, "is_enabled", new=mock.AsyncMock(return_value=True)), \
             mock.patch.object(service, "check_hourly_limit", new=mock.AsyncMock()), \
             mock.patch.object(service, "run", new=empty_run), \
             mock.patch("app.services.ai_usage_service.reserve_quota",
                        new=mock.AsyncMock(return_value=ticket)), \
             mock.patch("app.services.ai_usage_service.release_quota",
                        new=mock.AsyncMock()) as release, \
             mock.patch("app.services.ai_usage_service.record_writing_operation",
                        new=mock.AsyncMock()):
            async with async_client(user=make_user(role="admin")) as client:
                async with client.stream("POST", "/api/v1/admin/ai/writing/run", json={
                    "operation": "rewrite", "content_type": "node",
                }) as response:
                    body = "".join([chunk async for chunk in response.aiter_text()])
        events = _parse_sse(body)
        self.assertEqual(events["error"][0]["code"], 503)
        self.assertIn("AI 返回空内容", events["error"][0]["message"])
        self.assertNotIn("done", events)
        release.assert_awaited_once_with(ticket)

    async def test_run_rejects_when_disabled(self):
        with mock.patch.object(service, "is_enabled", new=mock.AsyncMock(return_value=False)):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.post("/api/v1/admin/ai/writing/run", json={
                    "operation": "rewrite", "content_type": "node",
                })
        self.assertEqual(res.status_code, 503)

    async def test_run_releases_call_quota_when_image_reservation_fails(self):
        ticket = object()
        with mock.patch.object(service, "is_enabled", new=mock.AsyncMock(return_value=True)), \
             mock.patch.object(service, "check_hourly_limit", new=mock.AsyncMock()), \
             mock.patch("app.services.ai_usage_service.reserve_quota",
                        new=mock.AsyncMock(return_value=ticket)), \
             mock.patch("app.services.ai_usage_service.reserve_image_quota",
                        new=mock.AsyncMock(side_effect=AppException(code=429, message="图片上限"))), \
             mock.patch("app.services.ai_usage_service.release_quota",
                        new=mock.AsyncMock()) as release, \
             mock.patch("app.services.ai_usage_service.record_writing_operation",
                        new=mock.AsyncMock()) as record_op:
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.post("/api/v1/admin/ai/writing/run", json={
                    "operation": "illustrate", "content_type": "node",
                    "image": {"prompt": "一只猫", "count": 2},
                })
        self.assertEqual(res.status_code, 429)
        release.assert_awaited_once_with(ticket)
        record_op.assert_not_awaited()

    async def test_run_releases_full_image_ticket_after_stream(self):
        ticket = ImageQuotaTicket(reserve_key="ai_usage:imgres:day:test", count=2)
        with mock.patch.object(service, "is_enabled", new=mock.AsyncMock(return_value=True)), \
             mock.patch.object(service, "check_hourly_limit", new=mock.AsyncMock()), \
             mock.patch.object(service, "run", new=fake_image_run), \
             mock.patch("app.services.ai_usage_service.reserve_quota",
                        new=mock.AsyncMock(return_value=None)), \
             mock.patch("app.services.ai_usage_service.reserve_image_quota",
                        new=mock.AsyncMock(return_value=ticket)), \
             mock.patch("app.services.ai_usage_service.release_image_quota",
                        new=mock.AsyncMock()) as release_image, \
             mock.patch("app.services.ai_usage_service.release_quota",
                        new=mock.AsyncMock()) as release_call, \
             mock.patch("app.services.ai_usage_service.record_writing_operation",
                        new=mock.AsyncMock()), \
             mock.patch("app.services.ai_usage_service.get_usage_summary", new=mock.AsyncMock(return_value={
                 "limits": {"remaining_today": 10, "remaining_images": 8}
             })), \
             mock.patch("app.services.ai_usage_service.get_quota_config", new=mock.AsyncMock()):
            async with async_client(user=make_user(role="admin")) as client:
                async with client.stream("POST", "/api/v1/admin/ai/writing/run", json={
                    "operation": "illustrate", "content_type": "node",
                    "image": {"prompt": "一只猫", "count": 2},
                }) as response:
                    body = "".join([chunk async for chunk in response.aiter_text()])
        self.assertEqual(response.status_code, 200)
        events = _parse_sse(body)
        self.assertEqual(len(events["image"]), 2)
        self.assertEqual(events["usage"][0]["images"], 2)
        self.assertAlmostEqual(events["usage"][0]["cost"], 0.4)
        # 整张票据归还（不是 count - 成功数）：成功张数已由 pipeline 单独记入实耗
        release_image.assert_awaited_once_with(ticket)
        release_call.assert_not_awaited()

    async def test_run_streams_error_event_on_unexpected_exception(self):
        ticket = object()
        with mock.patch.object(service, "is_enabled", new=mock.AsyncMock(return_value=True)), \
             mock.patch.object(service, "check_hourly_limit", new=mock.AsyncMock()), \
             mock.patch.object(service, "run", new=boom_run), \
             mock.patch("app.services.ai_usage_service.reserve_quota",
                        new=mock.AsyncMock(return_value=ticket)), \
             mock.patch("app.services.ai_usage_service.release_quota",
                        new=mock.AsyncMock()) as release, \
             mock.patch("app.services.ai_usage_service.record_writing_operation",
                        new=mock.AsyncMock()):
            async with async_client(user=make_user(role="admin")) as client:
                async with client.stream("POST", "/api/v1/admin/ai/writing/run", json={
                    "operation": "rewrite", "content_type": "node",
                }) as response:
                    body = "".join([chunk async for chunk in response.aiter_text()])
        self.assertEqual(response.status_code, 200)
        error = _parse_sse(body)["error"][0]
        self.assertEqual(error["code"], 500)
        self.assertTrue(error["retryable"])
        # 没有任何事件产出 → 本次调用未发生，必须归还全局额度
        release.assert_awaited_once_with(ticket)

    async def test_status_event_then_failure_releases_call_quota(self):
        """status 只是过程事件：上游没产出内容就失败，不能把调用额度留在账上"""
        call_ticket = object()
        image_ticket = ImageQuotaTicket(reserve_key="ai_usage:imgres:day:test", count=2)
        with mock.patch.object(service, "is_enabled", new=mock.AsyncMock(return_value=True)), \
             mock.patch.object(service, "check_hourly_limit", new=mock.AsyncMock()), \
             mock.patch.object(service, "run", new=status_then_fail_run), \
             mock.patch("app.services.ai_usage_service.reserve_quota",
                        new=mock.AsyncMock(return_value=call_ticket)), \
             mock.patch("app.services.ai_usage_service.reserve_image_quota",
                        new=mock.AsyncMock(return_value=image_ticket)), \
             mock.patch("app.services.ai_usage_service.release_quota",
                        new=mock.AsyncMock()) as release_call, \
             mock.patch("app.services.ai_usage_service.release_image_quota",
                        new=mock.AsyncMock()) as release_image, \
             mock.patch("app.services.ai_usage_service.record_writing_operation",
                        new=mock.AsyncMock()):
            async with async_client(user=make_user(role="admin")) as client:
                async with client.stream("POST", "/api/v1/admin/ai/writing/run", json={
                    "operation": "illustrate", "content_type": "node",
                    "image": {"prompt": "一只猫", "count": 2},
                }) as response:
                    body = "".join([chunk async for chunk in response.aiter_text()])
        events = _parse_sse(body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(events["status"][0]["stage"], "generating")
        self.assertNotIn("done", events)
        self.assertEqual(events["error"][0]["code"], 503)
        release_call.assert_awaited_once_with(call_ticket)
        release_image.assert_awaited_once_with(image_ticket)

    async def test_pre_stream_failure_releases_both_tickets(self):
        """预占成功但流开始前的记账失败：两张票据都要归还，且不进入流"""
        call_ticket = object()
        image_ticket = ImageQuotaTicket(reserve_key="ai_usage:imgres:day:test", count=2)
        with mock.patch.object(service, "is_enabled", new=mock.AsyncMock(return_value=True)), \
             mock.patch.object(service, "check_hourly_limit", new=mock.AsyncMock()), \
             mock.patch("app.services.ai_usage_service.reserve_quota",
                        new=mock.AsyncMock(return_value=call_ticket)), \
             mock.patch("app.services.ai_usage_service.reserve_image_quota",
                        new=mock.AsyncMock(return_value=image_ticket)), \
             mock.patch("app.services.ai_usage_service.release_quota",
                        new=mock.AsyncMock()) as release_call, \
             mock.patch("app.services.ai_usage_service.release_image_quota",
                        new=mock.AsyncMock()) as release_image, \
             mock.patch("app.services.ai_usage_service.record_writing_operation",
                        new=mock.AsyncMock(side_effect=AppException(code=500, message="统计写入失败"))):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.post("/api/v1/admin/ai/writing/run", json={
                    "operation": "illustrate", "content_type": "node",
                    "image": {"prompt": "一只猫", "count": 2},
                })
        self.assertEqual(res.status_code, 500)
        release_call.assert_awaited_once_with(call_ticket)
        release_image.assert_awaited_once_with(image_ticket)

    async def test_prompts_list_maps_rows(self):
        row = SimpleNamespace(
            operation="rewrite", display_name="改写", description=None,
            system_prompt="sys", user_template="{{selection}}", output_type="markdown",
            model_code=None, temperature=0.7, max_tokens=2000, max_input_chars=6000,
            is_enabled=True, sort_order=10,
        )
        with mock.patch.object(prompts_module, "ensure_default_prompts", new=mock.AsyncMock()), \
             mock.patch("app.api.admin_ai_writing.list_prompts",
                        new=mock.AsyncMock(return_value=[row])):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.get("/api/v1/admin/ai/writing/prompts")
        self.assertEqual(res.status_code, 200)
        item = res.json()["data"]["items"][0]
        self.assertEqual(item["operation"], "rewrite")
        self.assertEqual(item["description"], "")
        self.assertEqual(item["model_code"], "")
        self.assertEqual(item["temperature"], 0.7)

    async def test_prompt_update_translates_value_error_to_400(self):
        with mock.patch("app.api.admin_ai_writing.update_prompt",
                        new=mock.AsyncMock(side_effect=ValueError("操作不存在"))):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.put(
                    "/api/v1/admin/ai/writing/prompts/nope", json={"display_name": "新名字"}
                )
        self.assertEqual(res.status_code, 400)
        self.assertIn("操作不存在", res.json()["message"])
