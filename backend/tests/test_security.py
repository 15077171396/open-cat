"""Unit tests for security-sensitive helpers that do not require MySQL/Redis."""

import ipaddress
import unittest
from unittest import mock

from pydantic import ValidationError
from fastapi import Request

from app.config import settings
from app.core import rate_limit
from app.core.security import MAX_PASSWORD_BYTES, validate_password_length
from app.schemas.auth import RegisterRequest
from app.services import setting_service
from app.services.redemption_service import mask_code
from app.utils.upload_validation import is_allowed_image


class PasswordValidationTests(unittest.TestCase):
    def test_accepts_password_at_bcrypt_byte_limit(self):
        validate_password_length("a" * MAX_PASSWORD_BYTES)

    def test_rejects_password_over_bcrypt_byte_limit(self):
        with self.assertRaises(ValueError):
            validate_password_length("a" * (MAX_PASSWORD_BYTES + 1))

    def test_registration_rejects_multibyte_password_over_byte_limit(self):
        with self.assertRaises(ValidationError):
            RegisterRequest(account="user@example.com", password="密" * 25, code="1234")


class UploadValidationTests(unittest.TestCase):
    def test_accepts_matching_signatures(self):
        self.assertTrue(is_allowed_image("image/png", b"\x89PNG\r\n\x1a\nrest"))
        self.assertTrue(is_allowed_image("image/jpeg", b"\xff\xd8\xff\xe0rest"))
        self.assertTrue(is_allowed_image("image/webp", b"RIFF\x00\x00\x00\x00WEBPdata"))

    def test_rejects_mismatched_or_unknown_content(self):
        self.assertFalse(is_allowed_image("image/png", b"<script>alert(1)</script>"))
        self.assertFalse(is_allowed_image("image/svg+xml", b"<svg/>"))


CDN_EGRESS = "203.0.113.7"   # TEST-NET-3：代表 EdgeOne 出口（公网可路由、默认不可信）
SPOOFABLE = "9.9.9.9"        # 客户端可任意伪造的最左段
REAL_CLIENT = "198.51.100.23"


def _http_request(*, peer: str, xff: str | None = None, x_real_ip: str | None = None) -> Request:
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    if x_real_ip is not None:
        headers.append((b"x-real-ip", x_real_ip.encode()))
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/home",
        "raw_path": b"/api/v1/home",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": (peer, 54321),
        "server": ("test", 80),
    })


def _trust(*networks: str):
    """临时替换可信网段（模块导入时已解析 TRUSTED_PROXIES，测试要能改）"""
    return mock.patch.object(
        rate_limit,
        "_TRUSTED_NETWORKS",
        [ipaddress.ip_network(n, strict=False) for n in networks],
    )


class ClientIpExtractionTests(unittest.TestCase):
    """XFF 必须**从右向左跳过可信代理**（第四轮全量审查 P1-52）

    生产链路是「客户端 → EdgeOne → nginx → app」，nginx 用
    `$proxy_add_x_forwarded_for` 追加的是它自己的对端 = CDN 出口 IP。
    旧实现取 `parts[-1]`，于是永远得到 CDN 出口地址：同一 POP 的匿名访客
    共用一个限流桶（可被低成本打穿），且审计记的是 CDN IP（无法归因）。
    """

    def test_ignores_forwarded_headers_when_peer_is_not_trusted(self):
        # 直连对端不可信（例如有人绕过 CDN 直接打到 8000）→ 任何代理头都不采信
        self.assertEqual(
            rate_limit.get_client_ip(_http_request(peer=SPOOFABLE, xff=REAL_CLIENT)),
            SPOOFABLE,
        )

    def test_cdn_chain_returns_real_client_not_cdn_egress(self):
        """配了 CDN 出口段后，必须拿到真实客户端而不是 POP 地址（这是本条修复的核心）"""
        with _trust("172.16.0.0/12", f"{CDN_EGRESS}/32"):
            got = rate_limit.get_client_ip(
                _http_request(
                    peer="172.19.0.3",
                    # 客户端伪造了一段在最左，EdgeOne 追加真实地址，nginx 再追加自己的对端
                    xff=f"{SPOOFABLE}, {REAL_CLIENT}, {CDN_EGRESS}",
                )
            )
        self.assertEqual(got, REAL_CLIENT)

    def test_unconfigured_cdn_range_never_falls_back_to_spoofable_leftmost(self):
        """没配 CDN 出口段时宁可分桶过粗（得到 POP 地址），也绝不采信最左的可伪造值"""
        with _trust("172.16.0.0/12"):
            got = rate_limit.get_client_ip(
                _http_request(peer="172.19.0.3", xff=f"{SPOOFABLE}, {CDN_EGRESS}")
            )
        self.assertEqual(got, CDN_EGRESS)
        self.assertNotEqual(got, SPOOFABLE)

    def test_all_trusted_chain_takes_rightmost(self):
        with _trust("127.0.0.0/8", "10.0.0.0/8"):
            self.assertEqual(
                rate_limit.get_client_ip(
                    _http_request(peer="10.0.0.1", xff="10.0.0.2, 127.0.0.1")
                ),
                "127.0.0.1",
            )

    def test_falls_back_to_x_real_ip_then_peer(self):
        with _trust("172.16.0.0/12"):
            self.assertEqual(
                rate_limit.get_client_ip(
                    _http_request(peer="172.19.0.3", x_real_ip=REAL_CLIENT)
                ),
                REAL_CLIENT,
            )
        # 直连对端不可信时，X-Real-IP 同样不采信
        self.assertEqual(
            rate_limit.get_client_ip(
                _http_request(peer=SPOOFABLE, x_real_ip=REAL_CLIENT)
            ),
            SPOOFABLE,
        )


class SettingUrlValidationTests(unittest.TestCase):
    """url 类设置的校验（第四轮审查 P1-51：AI base_url 会带着 API Key 被服务端请求）"""

    def _defn(self, key: str, label: str) -> setting_service.SettingDefinition:
        return setting_service.SettingDefinition(
            key=key, label=label, type="url", group="ai", group_label="AI 模型", default=""
        )

    def _check(self, key: str, value: str) -> str:
        return setting_service._validate_setting_value(self._defn(key, key), value)

    def test_accepts_official_provider_hosts(self):
        self.assertEqual(
            self._check("ai_deepseek_base_url", "https://api.deepseek.com/v1"),
            "https://api.deepseek.com/v1",
        )
        self._check("ai_qwen_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    def test_rejects_provider_lookalike_hosts(self):
        for host in ("deepseek.com.evil.io", "api.deepseek.com.attacker.net", "evil-deepseek.com"):
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    self._check("ai_deepseek_base_url", f"https://{host}/v1")

    def test_rejects_credential_prefix_lookalike(self):
        """`startswith("http://")` 会放过这种形态：真实主机是 evil.com"""
        with self.assertRaises(ValueError):
            self._check("ai_deepseek_base_url", "http://api.deepseek.com@evil.com/v1")

    def test_rejects_internal_and_metadata_addresses_even_for_non_ai_urls(self):
        for value in (
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:8000/api/v1/home",
            "http://172.17.0.1:7897/",
            "http://10.0.0.5/x",
            "http://localhost:3000/",
            "http://[::1]:8000/",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self._check("social_github", value)

    def test_rejects_non_http_scheme(self):
        with self.assertRaises(ValueError):
            self._check("social_github", "javascript:alert(1)")

    def test_self_hosted_proxy_works_after_extending_allowlist(self):
        """白名单可配（不必改代码）——这是它写成 Settings 项而不是硬编码的原因"""
        with mock.patch.object(
            settings, "AI_BASE_URL_ALLOWED_HOSTS", "deepseek.com,api.my-proxy.com"
        ):
            self._check("ai_openai_base_url", "https://api.my-proxy.com/v1")
            with self.assertRaises(ValueError):
                self._check("ai_openai_base_url", "https://evil.com/v1")

    def test_empty_allowlist_disables_domain_lock_but_keeps_internal_block(self):
        with mock.patch.object(settings, "AI_BASE_URL_ALLOWED_HOSTS", ""):
            self._check("ai_deepseek_base_url", "https://any.public.host/v1")
            with self.assertRaises(ValueError):
                self._check("ai_deepseek_base_url", "http://127.0.0.1:8000/")


class RedemptionCodeMaskTests(unittest.TestCase):
    """只读角色拿到掩码（第四轮审查 P1-54）"""

    def test_keeps_last_group_only(self):
        self.assertEqual(mask_code("ABCD-EFGH-JKLM"), "****-****-JKLM")

    def test_masks_non_grouped_codes(self):
        self.assertEqual(mask_code("ABCDEFGHIJ"), "****GHIJ")
        self.assertEqual(mask_code("AB"), "****")
        self.assertEqual(mask_code(None), "")

    def test_list_codes_masks_by_default(self):
        """安全默认：调用方（路由）必须显式传 reveal 才能拿到明文"""
        import inspect

        from app.services import redemption_service

        params = inspect.signature(redemption_service.list_codes).parameters
        self.assertIs(params["reveal"].default, False)
        params = inspect.signature(redemption_service.list_code_records).parameters
        self.assertIs(params["reveal"].default, False)


# 真实可解码的 1×1 PNG（校验像素闸用的基准样本）
_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\r\xefF\xb8"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _png_with_size(width: int, height: int) -> bytes:
    """把 1×1 PNG 的 IHDR 宽高改掉并重算 CRC：只改头部，不解码像素数据"""
    import zlib

    buf = bytearray(_PNG_1X1)
    buf[16:20] = width.to_bytes(4, "big")
    buf[20:24] = height.to_bytes(4, "big")
    buf[29:33] = (zlib.crc32(bytes(buf[12:29])) & 0xFFFFFFFF).to_bytes(4, "big")
    return bytes(buf)


class UploadPixelBombGuardTests(unittest.TestCase):
    """解压炸弹：魔数与体积都合格的图，解码时可能吃掉几百 MB（第五轮审查 SEC-02）。

    Pillow 在 89M~178M 像素之间只告警不抛错，`convert("RGB")` 会真分配约 430MB；
    容器 mem_limit 768m 时足以 OOM。所以在「读文件头」阶段就要拦。
    """

    def test_small_image_passes(self):
        from app.utils.upload_validation import validate_image_pixels

        self.assertEqual(validate_image_pixels(_PNG_1X1), (1, 1))

    def test_oversized_pixels_rejected_without_decoding(self):
        from app.utils.upload_validation import validate_image_pixels

        with self.assertRaises(ValueError):
            validate_image_pixels(_png_with_size(6000, 6000))  # 36M 像素 > 20M 上限

    def test_garbage_rejected(self):
        from app.utils.upload_validation import validate_image_pixels

        with self.assertRaises(ValueError):
            validate_image_pixels(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


class SettingDefinitionsHideSecretDefaults(unittest.TestCase):
    """管理端设置接口不得回显 .env 明文密钥（第五轮审查 SEC-01）。

    `values` 走的是掩码通道，但 `definitions[].default` 是另一条通道，
    此前把 COS SecretKey / SMTP 授权码 / 小程序 AppSecret 原样发给前端；
    而内置只读角色 viewer 拥有全部 `*:view`（含 setting:view）。
    """

    def test_secret_defaults_are_blank(self):
        from app.services.setting_service import SETTING_DEFINITIONS, definitions_payload

        payload = {item["key"]: item for item in definitions_payload()}
        secret_keys = [d.key for d in SETTING_DEFINITIONS if d.secret]
        self.assertTrue(secret_keys, "没有 secret 定义，用例失去意义")

        for key in secret_keys:
            self.assertEqual(payload[key]["default"], "", f"{key} 的默认值仍在回显明文密钥")
            self.assertIn("has_default", payload[key], f"{key} 缺少 has_default 标记")

    def test_public_defaults_preserved(self):
        """非密钥项仍需带默认值：管理端用它做 placeholder 与「恢复默认」文案"""
        from app.services.setting_service import SETTING_DEFINITIONS, definitions_payload

        payload = {item["key"]: item for item in definitions_payload()}
        for defn in SETTING_DEFINITIONS:
            if not defn.secret and defn.default:
                self.assertEqual(payload[defn.key]["default"], defn.default)
                break
        else:  # pragma: no cover - 数据异常时给出明确提示
            self.fail("没有找到带默认值的非密钥项")


if __name__ == "__main__":
    unittest.main()
