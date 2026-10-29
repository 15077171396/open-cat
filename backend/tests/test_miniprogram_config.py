"""小程序配置与列表分页的守卫（第五轮审查 MNT-15）。

三件事此前只能靠人记：

1. **局域网 IP 写死在 `app.js`**：每个开发者的内网地址都不同，写死等于让下一个人
   先改源码才能真机调试，而且那个地址会随源码进仓库、进上传包；
2. **本地调试文件必须既不进仓库、也不进上传包** —— 否则「不入库」只挡住了 git，
   上传包里仍然带着某人的内网 IP；
3. **列表追加分页只下发新增项**：整包重发会让 setData 的耗时随页数线性增长，
   在低端机上表现为「越翻越卡」，而这属于**不会报错**的性能退化。

全部离线（只读文件）。
"""

import io
import json
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
ROOT = os.path.dirname(REPO)
MP = os.path.join(ROOT, "miniprogram")

# 私有网段字面量：出现在小程序源码里就说明有人把自己的内网地址写死了
_PRIVATE_IP_RE = re.compile(
    r"https?://(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})"
)

# 第三方依赖目录不检查（体积大、与我们的代码无关）
_SKIP_DIRS = {"node_modules", "miniprogram_npm", ".mimosa"}

# 示例文件里刻意写了 `192.168.1.10` 作为「照着填」的样本（不参与打包、也不参与运行），
# 它本来就是文档而不是配置，排除出扫描
_SKIP_FILES = {"config.local.example.js"}


def _iter_mp_sources():
    for dirpath, dirnames, filenames in os.walk(MP):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if name in _SKIP_FILES:
                continue
            if name.endswith((".js", ".json", ".wxml", ".wxss")):
                yield os.path.join(dirpath, name)


class NoHardcodedPrivateAddressTests(unittest.TestCase):
    def test_no_private_ip_literal_in_source(self):
        hits = []
        for path in _iter_mp_sources():
            text = io.open(path, encoding="utf-8", errors="ignore").read()
            for match in _PRIVATE_IP_RE.finditer(text):
                rel = os.path.relpath(path, ROOT)
                hits.append(f"{rel}: {match.group(0)}")
        self.assertEqual(
            hits,
            [],
            "小程序源码里写死了私有网段地址：真机调试请用 miniprogram/config.local.js"
            "（已 gitignore 且排除出上传包），不要改源码：\n" + "\n".join(hits),
        )

    def test_local_config_is_gitignored(self):
        text = io.open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
        self.assertIn(
            "miniprogram/config.local.js", text,
            "config.local.js 未被 gitignore —— 开发者的内网 IP 会被提交进仓库",
        )

    def test_local_config_excluded_from_upload_package(self):
        cfg = json.load(io.open(os.path.join(MP, "project.config.json"), encoding="utf-8"))
        values = {item.get("value") for item in cfg.get("packOptions", {}).get("ignore", [])}
        for name in ("config.local.js", "config.local.example.js"):
            self.assertIn(
                name, values,
                f"{name} 未被 project.config.json 的 packOptions.ignore 排除 —— "
                "它会随上传包一起发布（内网 IP 泄漏 / 无用文件）",
            )

    def test_app_reads_local_config_defensively(self):
        """本地配置缺失时必须能启动：真机调试没配 IP 只是告警，不能让小程序白屏。"""
        src = io.open(os.path.join(MP, "app.js"), encoding="utf-8").read()
        self.assertIn("config.local", src, "app.js 没有读取本地配置")
        self.assertRegex(
            src, r"try\s*\{[\s\S]{0,200}?require\('\./config\.local'\)",
            "读取 config.local 必须包在 try/catch 里（文件不存在时要能降级启动）",
        )


class ListAppendUsesIndexedSetDataTests(unittest.TestCase):
    def test_append_only_sends_new_items(self):
        src = io.open(os.path.join(MP, "utils", "list-page.js"), encoding="utf-8").read()
        self.assertRegex(
            src,
            r"listKey\s*\+\s*'\['",
            "追加分页必须用「按下标」setData（list[10] 这种写法）只下发新增项；"
            "整包重发会让耗时随页数线性增长（越翻越卡，且不报错）",
        )
        # 不能出现「无条件下发整个列表」的写法
        self.assertNotIn(
            "this.setData({ [listKey]: merged", src,
            "仍然在整包下发合并后的列表",
        )


if __name__ == "__main__":
    unittest.main()
