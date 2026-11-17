"""Stripe 支付链路测试（全程 mock/本地替身，不访问外网、不依赖真实密钥）。

覆盖：Checkout 下单、查单、Webhook 验签、事件解析、金额/币种校验、
会员开通幂等、失败/取消/超时落库、默认渠道回退。
"""

import hashlib
import hmac
import json
import time
import unittest
from unittest import mock

from app.models.payment import PaymentOrder, PaymentProduct
from app.models.user import User
from app.services import payment_service
from app.services.integration_settings_service import IntegrationConfig, _DEFAULTS
from app.services.payments import PaymentError, PaymentIntent, PaymentNotification
from app.services.payments.stripe import StripeProvider
from tests.fake_db import MemorySession

STRIPE_SECRET = "sk_test_unit_probe"
STRIPE_PUBLIC = "pk_test_unit_probe"
WEBHOOK_SECRET = "whsec_unit_probe"


def _config(**overrides) -> IntegrationConfig:
    values = dict(_DEFAULTS)
    values.update(
        {
            "payment_enabled": "1",
            "payment_mock_enabled": "0",
            "stripe_enabled": "1",
            "stripe_secret_key": STRIPE_SECRET,
            "stripe_publishable_key": STRIPE_PUBLIC,
            "stripe_webhook_secret": WEBHOOK_SECRET,
            "stripe_currency": "cny",
            "stripe_mode": "test",
            "site_base_url": "https://example.com",
        }
    )
    values.update(overrides)
    return IntegrationConfig(values)


def _vip_product(**overrides) -> PaymentProduct:
    data = {
        "id": 10,
        "code": "vip_30",
        "name": "30 天会员",
        "kind": "vip",
        "amount_cents": 2900,
        "points": 0,
        "vip_days": 30,
        "is_enabled": True,
        "sort_order": 1,
    }
    data.update(overrides)
    return PaymentProduct(**data)


def _session(**overrides) -> dict:
    data = {
        "id": "cs_test_123",
        "object": "checkout.session",
        "status": "complete",
        "payment_status": "paid",
        "amount_total": 2900,
        "currency": "cny",
        "client_reference_id": "ORDER-1",
        "payment_intent": "pi_test_123",
        "metadata": {"order_no": "ORDER-1"},
    }
    data.update(overrides)
    return data


def _event(event_type: str, session: dict, event_id: str = "evt_test_1") -> bytes:
    return json.dumps(
        {"id": event_id, "type": event_type, "data": {"object": session}}
    ).encode()


def _signature(body: bytes, timestamp: int | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(
        WEBHOOK_SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return f"t={ts},v1={digest}"


class StripeProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = StripeProvider(_config())

    async def test_missing_keys_rejected(self):
        with self.assertRaises(PaymentError):
            StripeProvider(_config(stripe_secret_key="", stripe_publishable_key=""))

    async def test_create_checkout_session(self):
        order = PaymentOrder(
            id=1,
            order_no="ORDER-1",
            user_id=1,
            product_code="vip_30",
            product_name="30 天会员",
            product_kind="vip",
            amount_cents=2900,
            currency="CNY",
            points=0,
            vip_days=30,
            channel="stripe",
            status="pending",
        )
        with mock.patch.object(
            StripeProvider,
            "_request",
            new=mock.AsyncMock(return_value={"id": "cs_test_123", "url": "https://checkout.stripe.test/1"}),
        ) as request:
            intent = await self.provider.create_payment(order)

        form = request.await_args.kwargs["data"]
        self.assertEqual(intent.pay_url, "https://checkout.stripe.test/1")
        self.assertEqual(intent.provider_payload["session_id"], "cs_test_123")
        self.assertEqual(form["client_reference_id"], "ORDER-1")
        self.assertEqual(form["line_items[0][price_data][unit_amount]"], "2900")
        self.assertEqual(form["line_items[0][price_data][currency]"], "cny")
        self.assertIn("{CHECKOUT_SESSION_ID}", form["success_url"])
        self.assertNotIn("Authorization", json.dumps(form))

    async def test_query_paid_session(self):
        order = PaymentOrder(
            id=2, order_no="ORDER-1", user_id=1, amount_cents=2900,
            channel="stripe", status="pending", provider_session_id="cs_test_123",
        )
        with mock.patch.object(
            StripeProvider,
            "_request",
            new=mock.AsyncMock(return_value=_session()),
        ):
            notification = await self.provider.query_order(order)
        assert notification is not None
        self.assertTrue(notification.success)
        self.assertEqual(notification.amount_cents, 2900)
        self.assertEqual(notification.currency, "cny")

    async def test_query_expired_session(self):
        order = PaymentOrder(
            id=3, order_no="ORDER-1", user_id=1, amount_cents=2900,
            channel="stripe", status="pending", provider_session_id="cs_test_123",
        )
        with mock.patch.object(
            StripeProvider,
            "_request",
            new=mock.AsyncMock(return_value=_session(status="expired", payment_status="unpaid")),
        ):
            notification = await self.provider.query_order(order)
        assert notification is not None
        self.assertFalse(notification.success)
        self.assertEqual(notification.raw["reason"], "expired")

    async def test_query_open_session_returns_none(self):
        order = PaymentOrder(
            id=4, order_no="ORDER-1", user_id=1, amount_cents=2900,
            channel="stripe", status="pending", provider_session_id="cs_test_123",
        )
        with mock.patch.object(
            StripeProvider,
            "_request",
            new=mock.AsyncMock(return_value=_session(status="open", payment_status="unpaid")),
        ):
            self.assertIsNone(await self.provider.query_order(order))

    async def test_valid_webhook_signature(self):
        body = _event("checkout.session.completed", _session())
        notification = await self.provider.parse_notify(
            body=body, headers={"stripe-signature": _signature(body)}
        )
        self.assertTrue(notification.success)
        self.assertEqual(notification.order_no, "ORDER-1")
        self.assertEqual(notification.amount_cents, 2900)

    async def test_tampered_webhook_rejected(self):
        body = _event("checkout.session.completed", _session())
        tampered = body.replace(b"2900", b"1")
        with self.assertRaises(PaymentError):
            await self.provider.parse_notify(
                body=tampered, headers={"stripe-signature": _signature(body)}
            )

    async def test_expired_event_is_failure(self):
        body = _event("checkout.session.expired", _session(status="expired", payment_status="unpaid"))
        notification = await self.provider.parse_notify(
            body=body, headers={"stripe-signature": _signature(body)}
        )
        self.assertFalse(notification.success)
        self.assertEqual(notification.raw["reason"], "expired")

    async def test_async_failed_event_is_failure(self):
        body = _event("checkout.session.async_payment_failed", _session())
        notification = await self.provider.parse_notify(
            body=body, headers={"stripe-signature": _signature(body)}
        )
        self.assertFalse(notification.success)
        self.assertEqual(notification.raw["reason"], "failed")

    async def test_completed_but_unpaid_is_ignored(self):
        body = _event("checkout.session.completed", _session(payment_status="unpaid"))
        notification = await self.provider.parse_notify(
            body=body, headers={"stripe-signature": _signature(body)}
        )
        self.assertFalse(notification.success)
        self.assertTrue(notification.raw["ignored"])

    async def test_other_event_is_ignored(self):
        body = _event("payment_intent.succeeded", {"id": "pi_1"})
        notification = await self.provider.parse_notify(
            body=body, headers={"stripe-signature": _signature(body)}
        )
        self.assertTrue(notification.raw["ignored"])


class StripeOrderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = MemorySession()
        self.db.seed(_vip_product())
        self.user = User(id=1, account="vip@example.com", password_hash="x", role="user")
        self.db.seed(self.user)

        self.available = [{"value": "stripe", "label": "Stripe"}]
        self.patch_config = mock.patch.object(
            payment_service, "get_payment_config",
            new=mock.AsyncMock(return_value=_config()),
        )
        self.patch_channels = mock.patch.object(
            payment_service, "available_channels", return_value=self.available
        )
        self.provider = mock.AsyncMock()
        self.provider.create_payment = mock.AsyncMock(
            return_value=PaymentIntent(
                pay_url="https://checkout.stripe.test/1",
                provider_payload={"session_id": "cs_test_123", "currency": "cny"},
            )
        )
        self.patch_provider = mock.patch.object(
            payment_service, "get_payment_provider",
            new=mock.AsyncMock(return_value=self.provider),
        )
        for patcher in (self.patch_config, self.patch_channels, self.patch_provider):
            patcher.start()

    async def asyncTearDown(self):
        for patcher in (self.patch_channels, self.patch_provider, self.patch_config):
            patcher.stop()

    async def test_default_channel_is_stripe(self):
        result = await payment_service.create_order(
            self.db, self.user, product_code="vip_30"
        )
        self.assertEqual(result["channel"], "stripe")
        self.assertEqual(result["provider"], "stripe")
        self.assertEqual(result["currency"], "CNY")
        self.assertEqual(result["session_id"], "cs_test_123")
        self.assertEqual(result["pay_url"], "https://checkout.stripe.test/1")

    async def test_membership_checkout_rejects_points_product(self):
        self.db.seed(
            PaymentProduct(
                id=11, code="points_100", name="100 积分", kind="points",
                amount_cents=1000, points=100, vip_days=0, is_enabled=True,
            )
        )
        with self.assertRaises(Exception):
            await payment_service.create_membership_checkout(
                self.db, self.user, product_code="points_100", channel="stripe"
            )

    async def test_cancel_closes_pending_order(self):
        result = await payment_service.create_order(
            self.db, self.user, product_code="vip_30", channel="stripe"
        )
        state = await payment_service.cancel_membership_checkout(
            self.db, self.user, result["session_id"]
        )
        self.assertEqual(state["order"]["status"], "closed")
        self.assertEqual(
            self.db.all_rows(PaymentOrder)[0]["provider_payload"]["close_reason"], "cancel"
        )
        # 重复取消不再改动
        again = await payment_service.cancel_membership_checkout(
            self.db, self.user, result["session_id"]
        )
        self.assertEqual(again["order"]["status"], "closed")

    async def test_expired_webhook_closes_order(self):
        result = await payment_service.create_order(
            self.db, self.user, product_code="vip_30", channel="stripe"
        )
        notification = PaymentNotification(
            order_no=result["order_no"], trade_no=None, amount_cents=None,
            success=False, raw={"reason": "expired"},
        )
        self.assertTrue(await payment_service.mark_notification_failure(self.db, notification))
        self.assertEqual(
            self.db.all_rows(PaymentOrder)[0]["status"], "closed"
        )

    async def test_failed_webhook_marks_failed(self):
        result = await payment_service.create_order(
            self.db, self.user, product_code="vip_30", channel="stripe"
        )
        notification = PaymentNotification(
            order_no=result["order_no"], trade_no="pi_1", amount_cents=2900,
            success=False, raw={"reason": "failed"},
        )
        self.assertTrue(await payment_service.mark_notification_failure(self.db, notification))
        self.assertEqual(
            self.db.all_rows(PaymentOrder)[0]["status"], "failed"
        )


class StripeSettleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = MemorySession()
        self.user = User(id=1, account="vip@example.com", password_hash="x", role="user")
        self.db.seed(self.user)

    async def _order(self, **overrides):
        data = {
            "id": 200,
            "order_no": "ORDER-1",
            "user_id": 1,
            "product_code": "vip_30",
            "product_name": "30 天会员",
            "product_kind": "vip",
            "amount_cents": 2900,
            "currency": "CNY",
            "points": 0,
            "vip_days": 30,
            "channel": "stripe",
            "status": "pending",
            "provider_session_id": "cs_test_123",
        }
        data.update(overrides)
        order = PaymentOrder(**data)
        self.db.seed(order)
        return order

    async def test_success_opens_vip_once(self):
        await self._order()
        notification = PaymentNotification(
            "ORDER-1", "pi_test_123", 2900, True, {"event": "checkout.session.completed"}, "cny"
        )
        self.assertTrue(await payment_service.settle_order(self.db, notification))
        vip = self.db.all_rows(User)[0]
        self.assertEqual(vip["role"], "vip")
        self.assertIsNotNone(vip["vip_expire_time"])
        # 重复 webhook 不重复延期
        before = vip["vip_expire_time"]
        self.assertTrue(await payment_service.settle_order(self.db, notification))
        self.assertEqual(self.db.all_rows(User)[0]["vip_expire_time"], before)

    async def test_currency_mismatch_does_not_grant(self):
        await self._order()
        notification = PaymentNotification(
            "ORDER-1", "pi_test_123", 2900, True, {}, "usd"
        )
        self.assertFalse(await payment_service.settle_order(self.db, notification))
        self.assertEqual(self.db.all_rows(User)[0]["role"], "user")

    async def test_amount_mismatch_does_not_grant(self):
        await self._order()
        notification = PaymentNotification("ORDER-1", "pi_test_123", 1, True, {}, "cny")
        self.assertFalse(await payment_service.settle_order(self.db, notification))
        self.assertEqual(self.db.all_rows(User)[0]["role"], "user")
