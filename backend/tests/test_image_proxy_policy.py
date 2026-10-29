"""图片代理的主机/类型决策（缺口台账 P2-37② 的行为基线）。

`api/images.py` 同时是「路由」和「GitHub 图片的安全策略」两件事的宿主：白名单主机、
可中转的候选源、可返回的 MIME 精确集都写在路由文件里。这属于放错层（限流在
`core/rate_limit`、SSRF 护栏在 `third_party_fetch`，图片代理却没有归属地），
但**直接搬动安全边界代码的风险高于收益**，所以先按第四轮审查的规矩办：

1. 先把「今天到底放行了什么」钉成可失败断言（本文件）；
2. 搬完之后同一组断言必须仍然绿 —— 这才是"搬迁未改行为"的证据，而不是读一遍代码说没改。

钉住的三条最要紧的：
- **主机精确相等**才放行：`github.com.evil.com` 这类后缀仿冒、`http://github.com@evil.com`
  这类 userinfo 伪装都必须被拒；
- **只放行 http/https**：`file://` / `gopher://` 是 SSRF 的经典跳板；
- **MIME 是精确白名单而不是 `image/` 前缀**：`image/svg+xml` 可携带脚本，
  从本站同源返回时脚本能读登录态 Cookie（SEC-015，等价账号劫持）。
"""

import unittest
from unittest import mock

from app.api import images
from app.config import settings
from app.services.github_readme_service import GITHUB_IMAGE_HOSTS


class AllowedHostTests(unittest.TestCase):
    def test_every_listed_host_is_accepted(self):
        for host in sorted(GITHUB_IMAGE_HOSTS):
            self.assertTrue(
                images._is_allowed_url(f"https://{host}/a.png"),
                f"白名单主机 {host} 应当可中转（改了就要同步本用例）",
            )

    def test_host_must_match_exactly_not_by_suffix(self):
        rejected = [
            "https://github.com.evil.com/x.png",                 # 后缀仿冒
            "https://evilgithub.com/x.png",                      # 前缀仿冒
            "https://raw.githubusercontent.com@evil.com/x.png",  # userinfo 伪装
            "https://evil.com/#https://github.com/x.png",
            "https://user-images.githubusercontent.com.evil.io/x",
        ]
        for url in rejected:
            self.assertFalse(images._is_allowed_url(url), f"必须拒绝：{url}")

    def test_only_http_and_https_schemes(self):
        for scheme in ("file", "gopher", "ftp", "dict"):
            self.assertFalse(
                images._is_allowed_url(f"{scheme}://github.com/x"),
                f"{scheme}:// 是 SSRF 经典跳板，必须拒",
            )
        self.assertFalse(images._is_allowed_url("github.com/x.png"), "缺协议同样要拒")

    def test_unparseable_and_empty_input_are_denied(self):
        for bad in ("", None, "   ", "http://", "//github.com/x"):
            with self.subTest(bad=bad):
                self.assertFalse(images._is_allowed_url(bad or ""))


class SafeMimeTests(unittest.TestCase):
    def test_svg_is_not_safe(self):
        """SVG 能带脚本；同源返回时等于把登录态交给攻击者（SEC-015）。"""
        self.assertNotIn("image/svg+xml", images._SAFE_IMAGE_TYPES)

    def test_prefix_check_is_not_used(self):
        """白名单必须是精确集合：任何 `image/` 前缀下的新类型都不该自动被放行。"""
        self.assertTrue(all("/" in t and not t.endswith("/") for t in images._SAFE_IMAGE_TYPES))
        for must_pass in ("image/png", "image/webp", "image/avif"):
            self.assertIn(must_pass, images._SAFE_IMAGE_TYPES)


class CandidateTests(unittest.TestCase):
    def test_raw_url_gets_jsdelivr_variant(self):
        url = "https://raw.githubusercontent.com/o/r/main/docs/a.png"
        got = images._fetch_candidates(url)
        self.assertEqual(got[0], url, "原始 URL 必须仍是第一候选")
        self.assertIn("cdn.jsdelivr.net", " ".join(got))

    def test_non_raw_url_has_only_itself(self):
        url = "https://avatars.githubusercontent.com/u/1"
        self.assertEqual(images._fetch_candidates(url), [url])

    def test_mirror_prefix_appends_wrapped_candidate(self):
        url = "https://github.com/o/r/raw/main/a.png"
        with mock.patch.object(settings, "GITHUB_MIRROR_PREFIX", "https://mirror.example.com/"):
            got = images._fetch_candidates(url)
        self.assertIn("https://mirror.example.com/" + url, got)
        self.assertEqual(len(got), len(set(got)), "候选不得重复（重复等于白跑一次上游）")

    def test_no_mirror_configured_keeps_single_candidate(self):
        url = "https://github.com/o/r/raw/main/a.png"
        with mock.patch.object(settings, "GITHUB_MIRROR_PREFIX", ""):
            self.assertEqual(images._fetch_candidates(url), [url])


class SizeCapTests(unittest.TestCase):
    def test_cap_matches_third_party_guardrail(self):
        """本端点把图片整包读进内存，上限必须与全仓第三方响应护栏同口径（P1-39）。"""
        from app.services.github_readme_service import MAX_RESPONSE_SIZE

        self.assertEqual(images._MAX_IMAGE_SIZE, MAX_RESPONSE_SIZE)


class OwnershipTests(unittest.TestCase):
    """搬迁后的归属检查（P2-37②）：策略在 service，路由只留传输与响应。"""

    def test_route_module_delegates_the_policy(self):
        import inspect

        src = inspect.getsource(images)
        # 白名单集合与候选构造不应再在路由文件里定义，只应引用 service
        self.assertNotIn("_ALLOWED_HOSTS = ", src, "主机白名单不该定义在路由里")
        self.assertNotIn("def _fetch_candidates", src, "候选构造不该定义在路由里")

    def test_service_is_the_single_source(self):
        from app.services import image_proxy

        self.assertIs(images._is_allowed_url, image_proxy.is_allowed_url)
        self.assertIs(images._fetch_candidates, image_proxy.fetch_candidates)
        self.assertIs(images._SAFE_IMAGE_TYPES, image_proxy.SAFE_IMAGE_TYPES)


class OwnCosObjectKeyTests(unittest.TestCase):
    """本站 COS 通道的边界：只认自己的桶，key 必须是对象名而不是任意 URL。"""

    def _patch_cos(self, prefixes=("https://bkt.cos.ap-guangzhou.myqcloud.com/",)):
        from app.services import cos_service

        return mock.patch.object(cos_service, "is_configured", return_value=True), mock.patch.object(
            cos_service, "public_url_prefixes", return_value=prefixes
        )

    def test_own_bucket_url_returns_key(self):
        from app.services import image_proxy

        c1, c2 = self._patch_cos()
        with c1, c2:
            key = image_proxy.own_cos_object_key(
                "https://bkt.cos.ap-guangzhou.myqcloud.com/images/2026/10/19/abc.webp"
            )
        self.assertEqual(key, "images/2026/10/19/abc.webp")

    def test_query_string_is_dropped(self):
        """前台会给 COS 图追加 ?imageMogr2…，取 key 时必须忽略查询串"""
        from app.services import image_proxy

        c1, c2 = self._patch_cos()
        with c1, c2:
            key = image_proxy.own_cos_object_key(
                "https://bkt.cos.ap-guangzhou.myqcloud.com/images/a.webp?imageMogr2/format/webp"
            )
        self.assertEqual(key, "images/a.webp")

    def test_foreign_host_is_rejected(self):
        """别的桶 / 别的域一律不走这条通道（否则就是开放代理）"""
        from app.services import image_proxy

        c1, c2 = self._patch_cos()
        with c1, c2:
            self.assertIsNone(
                image_proxy.own_cos_object_key(
                    "https://evil.cos.ap-guangzhou.myqcloud.com/images/a.webp"
                )
            )
            self.assertIsNone(image_proxy.own_cos_object_key("https://github.com/a.png"))

    def test_path_traversal_key_is_rejected(self):
        from app.services import image_proxy

        c1, c2 = self._patch_cos()
        with c1, c2:
            self.assertIsNone(
                image_proxy.own_cos_object_key(
                    "https://bkt.cos.ap-guangzhou.myqcloud.com/images/../../etc/passwd"
                )
            )

    def test_unconfigured_cos_rejects_everything(self):
        from app.services import cos_service, image_proxy

        with mock.patch.object(cos_service, "is_configured", return_value=False):
            self.assertIsNone(
                image_proxy.own_cos_object_key(
                    "https://bkt.cos.ap-guangzhou.myqcloud.com/images/a.webp"
                )
            )


if __name__ == "__main__":
    unittest.main()
