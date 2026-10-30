"""`examples/frontend-minimal` 的接线守卫。

示例页面是「内容 API 能用」的活证明，也是最容易腐烂的资产：
接口路径一改，页面不会报错、不会有人提 issue，只会安静地白屏。
所以这里把页面里用到的每个接口路径，拿去和真实 OpenAPI 比对。

只做静态解析（不跑浏览器）：示例的取数逻辑全部走同一个 `get(path)`，
把它的字符串参数抓出来就能覆盖全部请求。
"""

import re
import unittest
from pathlib import Path

from app.api.content import CONTENT_PREFIX
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DIR = REPO_ROOT / "examples" / "frontend-minimal"
EXAMPLE_HTML = EXAMPLE_DIR / "index.html"

# get('...') / get(`...`)
_CALL_RE = re.compile(r"""get\(\s*(['"`])(.*?)\1\s*\)""", re.S)
# 模板字符串里的插值 → 路径参数占位
_INTERP_RE = re.compile(r"\$\{[^}]+\}")


class FrontendMinimalExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = EXAMPLE_HTML.read_text(encoding="utf-8")
        cls.schema_paths = set(app.openapi()["paths"])

    def _used_paths(self) -> set[str]:
        used = set()
        for match in _CALL_RE.finditer(self.html):
            literal = match.group(2).strip()
            # 只认路径：示例里还有 URLSearchParams.get('api') 这类同名调用
            if literal and not literal.startswith("/"):
                continue
            path = _INTERP_RE.sub("{param}", literal).split("?", 1)[0]
            used.add(f"{CONTENT_PREFIX}{path}" if path else CONTENT_PREFIX)
        return used

    def test_files_exist(self):
        self.assertTrue(EXAMPLE_HTML.exists(), "示例页面不存在")
        self.assertTrue((EXAMPLE_DIR / "README.md").exists(), "示例说明不存在")

    def test_parsed_some_calls(self):
        """解析不到说明示例改了写法（比如不再用 get()），守卫会静默失效"""
        self.assertGreaterEqual(len(self._used_paths()), 3, "没解析到足够的接口调用，守卫需重写")

    def test_every_used_path_exists(self):
        # OpenAPI 里是 {tutorial_id} 这样的具名参数，示例里统一成 {param}，比对时两边都归一
        normalized = {_INTERP_RE.sub("{param}", p) for p in self.schema_paths}
        normalized = {re.sub(r"\{[^}]+\}", "{param}", p) for p in normalized}

        missing = sorted(p for p in self._used_paths() if p not in normalized)
        self.assertEqual(
            missing,
            [],
            "示例调用了 OpenAPI 里不存在的路径（接口改名后页面会白屏）: "
            f"{missing}",
        )

    def test_example_does_not_use_login_only_detail(self):
        """详情接口要登录 —— 示例用 `.../intro` 代替（它匿名可读且已含章节树）。

        这条是踩坑记录：第一版示例直接调 `.../tutorials/{id}`，页面上线即 401，
        而 HTTP 200 的静态检查完全看不出来。谁要把它改回去，这里会拦住。
        """
        detail = f"{CONTENT_PREFIX}/tutorials/{{param}}"
        self.assertNotIn(
            detail,
            self._used_paths(),
            "示例调用了需要登录的详情接口；前台详情页请用 .../intro（匿名可读且含章节树）",
        )

    def test_example_only_touches_content_namespace(self):
        """示例是给外部开发者的样板，不该出现管理端接口（那需要登录态）"""
        self.assertNotIn("/api/v1/admin", self.html)

    def test_readme_points_at_live_docs(self):
        readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"{CONTENT_PREFIX}/docs", readme, "说明里没给出文档地址")
        self.assertIn("CORS_ORIGINS", readme, "说明里没写跨域配置（照做会直接白屏）")


if __name__ == "__main__":
    unittest.main()
