"""支付链路契约测试：商品校验、下单、幂等结算、积分/VIP 发放、微信/支付宝验签解密。"""

import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.exceptions import AppException
from app.models.payment import PaymentOrder, PaymentProduct
from app.models.point_transaction import PointTransaction
from app.models.user import User
from app.services import payment_service
from app.services.integration_settings_service import IntegrationConfig, _DEFAULTS
from app.services.payments import PaymentError, PaymentIntent, PaymentNotification
from app.services.payments.wechat import WechatPayProvider
from app.services.payments.alipay import AlipayProvider
from tests.fake_db import MemorySession


def _config(**overrides) -> IntegrationConfig:
    values = dict(_DEFAULTS)
    values.update(overrides)
    return IntegrationConfig(values)


def _product(**overrides) -> PaymentProduct:
    data = {
        "id": 100,
        "code": "points_100",
        "name": "100 积分",
        "kind": "points",
        "amount_cents": 1000,
        "points": 100,
        "vip_days": 0,
        "is_enabled": True,
        "sort_order": 10,
    }
    data.update(overrides)
    return PaymentProduct(**data)


class ProductValidationTests(unittest.TestCase):
    def test_points_product_requires_points(self):
        payload = {
            "code": "p1",
            "name": "积分",
            "kind": "points",
            "amount_cents": 100,
            "points": 0,
            "vip_days": 0,
        }
        with self.assertRaises(AppException):
            payment_service._validate_product_payload(payload)

    def test_vip_product_requires_days(self):
        payload = {
            "code": "v1",
            "name": "VIP",
            "kind": "vip",
            "amount_cents": 100,
            "points": 0,
            "vip_days": 0,
        }
        with self.assertRaises(AppException):
            payment_service._validate_product_payload(payload)

    def test_amount_must_be_positive(self):
        with self.assertRaises(AppException):
            payment_service._validate_product_payload(
                {"code": "x", "name": "x", "kind": "points", "amount_cents": 0, "points": 1}
            )

    def test_valid_product_passes(self):
        out = payment_service._validate_product_payload(
            {"code": "p1", "name": "积分", "kind": "points", "amount_cents": 100, "points": 10}
        )
        self.assertEqual(out["amount_cents"], 100)
        self.assertEqual(out["points"], 10)


class OrderSettleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = MemorySession()
        self.user = User(
            id=1,
            account="buyer@example.com",
            password_hash="x",
            role="user",
            points=50,
        )
        self.db.seed(self.user)

    async def _order(self, **overrides):
        data = {
            "id": 200,
            "order_no": "ORDER-1",
            "user_id": 1,
            "product_code": "points_100",
            "product_name": "100 积分",
            "product_kind": "points",
            "amount_cents": 1000,
            "points": 100,
            "vip_days": 0,
            "channel": "mock",
            "status": "pending",
        }
        data.update(overrides)
        order = PaymentOrder(**data)
        self.db.seed(order)
        return order

    async def test_points_are_granted_once(self):
        await self._order()
        notification = PaymentNotification("ORDER-1", "T1", 1000, True, {"mock": True})
        self.assertTrue(await payment_service.settle_order(self.db, notification))
        self.assertEqual(self.db.all_rows(User)[0]["points"], 150)
        tx = self.db.all_rows(PointTransaction)
        self.assertEqual(len(tx), 1)
        self.assertEqual(tx[0]["type"], "recharge")
        # 重复回调不得重复加积分
        self.assertTrue(await payment_service.settle_order(self.db, notification))
        self.assertEqual(self.db.all_rows(User)[0]["points"], 150)
        self.assertEqual(len(self.db.all_rows(PointTransaction)), 1)

    async def test_amount_mismatch_does_not_grant(self):
        await self._order()
        notification = PaymentNotification("ORDER-1", "T1", 999, True, {})
        self.assertFalse(await payment_service.settle_order(self.db, notification))
        self.assertEqual(self.db.all_rows(User)[0]["points"], 50)
        self.assertEqual(self.db.all_rows(PaymentOrder)[0]["status"], "pending")

    async def test_closed_order_does_not_grant(self):
        await self._order(status="closed")
        notification = PaymentNotification("ORDER-1", "T1", 1000, True, {})
        self.assertFalse(await payment_service.settle_order(self.db, notification))
        self.assertEqual(self.db.all_rows(User)[0]["points"], 50)

    async def test_vip_extends_from_existing_expiry(self):
        current = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=10)
        self.db.seed(
            User(
                id=2,
                account="vip@example.com",
                password_hash="x",
                role="vip",
                vip_expire_time=current,
            )
        )
        await self._order(id=201, order_no="ORDER-2", user_id=2, product_kind="vip", points=0, vip_days=30)
        notification = PaymentNotification("ORDER-2", "T2", 1000, True, {})
        self.assertTrue(await payment_service.settle_order(self.db, notification))
        row = self.db.all_rows(User)
        vip = next(item for item in row if item["id"] == 2)
        self.assertEqual(vip["vip_expire_time"], current + timedelta(days=30))
        self.assertEqual(vip["role"], "vip")


class CreateOrderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = MemorySession()
        self.db.seed(_product())
        self.user = User(id=1, account="u@example.com", password_hash="x", role="user")
        self._patch_config = mock.patch.object(
            payment_service,
            "get_payment_config",
            new=mock.AsyncMock(
                return_value=_config(
                    payment_enabled="1",
                    payment_mock_enabled="1",
                    site_base_url="https://example.com",
                )
            ),
        )
        self._patch_config.start()
        self._patch_channels = mock.patch.object(
            payment_service,
            "available_channels",
            return_value=[{"value": "mock", "label": "模拟支付"}],
        )
        self._patch_channels.start()
        self._provider = mock.AsyncMock()
        self._provider.create_payment = mock.AsyncMock(
            return_value=PaymentIntent(pay_url="https://example.com/pay/mock?x=1")
        )
        self._patch_provider = mock.patch.object(
            payment_service,
            "get_payment_provider",
            new=mock.AsyncMock(return_value=self._provider),
        )
        self._patch_provider.start()

    async def asyncTearDown(self):
        self._patch_config.stop()
        self._patch_channels.stop()
        self._patch_provider.stop()

    async def test_mock_order_created_with_pay_url(self):
        result = await payment_service.create_order(
            self.db, self.user, product_code="points_100", channel="mock"
        )
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["pay_url"], "https://example.com/pay/mock?x=1")
        self.assertEqual(result["amount_cents"], 1000)
        self.assertEqual(len(self.db.all_rows(PaymentOrder)), 1)

    async def test_disabled_channel_is_rejected(self):
        with self.assertRaises(AppException):
            await payment_service.create_order(
                self.db, self.user, product_code="points_100", channel="wechat"
            )


def _rsa_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return key, private_pem, public_pem


class WechatNotifyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.merchant_key, self.merchant_pem, _ = _rsa_pair()
        self.platform_key, _, self.platform_pub = _rsa_pair()
        self.api_key = "0123456789abcdef0123456789abcdef"
        self.provider = WechatPayProvider(
            _config(
                wechat_pay_mch_id="m1",
                wechat_pay_appid="a1",
                wechat_pay_serial_no="serial",
                wechat_pay_api_v3_key=self.api_key,
                wechat_pay_private_key=self.merchant_pem,
                wechat_pay_platform_public_key=self.platform_pub,
                site_base_url="https://example.com",
            )
        )

    def _body(self):
        plain = {
            "mchid": "m1",
            "appid": "a1",
            "out_trade_no": "ORDER-1",
            "transaction_id": "T1",
            "trade_state": "SUCCESS",
            "amount": {"total": 1000},
        }
        nonce = "abcdefghijkl"
        aad = "transaction"
        cipher = AESGCM(self.api_key.encode()).encrypt(
            nonce.encode(), json.dumps(plain).encode(), aad.encode()
        )
        return json.dumps(
            {
                "resource": {
                    "algorithm": "AEAD_AES_256_GCM",
                    "ciphertext": base64.b64encode(cipher).decode(),
                    "nonce": nonce,
                    "associated_data": aad,
                }
            }
        ).encode()

    def _headers(self, body: bytes):
        timestamp = "1793000000"
        nonce = "headernonce"
        message = f"{timestamp}\n{nonce}\n{body.decode()}\n"
        signature = base64.b64encode(
            self.platform_key.sign(
                message.encode(), padding.PKCS1v15(), hashes.SHA256()
            )
        ).decode()
        return {
            "Wechatpay-Timestamp": timestamp,
            "Wechatpay-Nonce": nonce,
            "Wechatpay-Signature": signature,
        }

    async def test_valid_notify_is_decrypted(self):
        body = self._body()
        notification = await self.provider.parse_notify(
            body=body, headers=self._headers(body)
        )
        self.assertEqual(notification.order_no, "ORDER-1")
        self.assertEqual(notification.trade_no, "T1")
        self.assertEqual(notification.amount_cents, 1000)
        self.assertTrue(notification.success)

    async def test_tampered_notify_is_rejected(self):
        body = self._body()
        headers = self._headers(body)
        tampered = body.replace(b"AEAD_AES_256_GCM", b"AEAD_AES_256_GCMX")
        with self.assertRaises(PaymentError):
            await self.provider.parse_notify(body=tampered, headers=headers)


class AlipayNotifyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app_key, self.app_pem, _ = _rsa_pair()
        self.alipay_key, _, self.alipay_pub = _rsa_pair()
        self.provider = AlipayProvider(
            _config(
                alipay_app_id="a1",
                alipay_private_key=self.app_pem,
                alipay_public_key=self.alipay_pub,
                site_base_url="https://example.com",
            )
        )

    def _signed_form(self):
        form = {
            "app_id": "a1",
            "out_trade_no": "ORDER-1",
            "trade_no": "T1",
            "trade_status": "TRADE_SUCCESS",
            "total_amount": "10.00",
        }
        content = "&".join(f"{key}={form[key]}" for key in sorted(form))
        signature = base64.b64encode(
            self.alipay_key.sign(
                content.encode(), padding.PKCS1v15(), hashes.SHA256()
            )
        ).decode()
        return {**form, "sign": signature, "sign_type": "RSA2"}

    async def test_valid_notify_is_verified(self):
        notification = await self.provider.parse_notify(form=self._signed_form())
        self.assertEqual(notification.order_no, "ORDER-1")
        self.assertEqual(notification.amount_cents, 1000)
        self.assertTrue(notification.success)

    async def test_wrong_signature_is_rejected(self):
        form = self._signed_form()
        form["total_amount"] = "9999.00"
        with self.assertRaises(PaymentError):
            await self.provider.parse_notify(form=form)
