"""内容发布状态（is_published）的守卫测试（全离线，静态源码检查）。

背景：
    教程（tutorials）与工具（tools）早就有 `is_published`，但**百科 / 扩展 / 资源一直
    没有** —— 后台保存即公开，既无草稿态也无下架能力。2026-10-18 给三张表补上该列后，
    最危险的不是「忘了写过滤」，而是**写了一半**：列表过滤了但详情没过滤，于是草稿拿到
    ID 就能直接访问；或者公开侧过滤了而 sitemap 没过滤，草稿照样被推给搜索引擎。

    这类漂移和 `test_list_parity.py` 管的字段漂移是同一性质，同样没有任何运行时症状
    （不报错、不 500，只是「不该看见的被看见了」），因此同样用静态检查钉住。

覆盖的不变量：
    1. 公开列表 / 详情 / 热门 / 最新 / 下载链接都必须要求 `is_published == True`
    2. sitemap 的实体查询同样过滤（草稿不得进站点地图）
    3. 后台 admin_* 的列表**不得**过滤（运营要能看到并编辑草稿）
    4. 迁移必须给存量数据默认 1（否则部署后线上内容会全部消失）
"""

import ast
import io
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
SVC = os.path.join(REPO, 'app', 'services')
VERSIONS = os.path.join(REPO, 'alembic', 'versions')

# (文件, 函数) —— 公开侧：每一处都必须带 is_published 过滤。
#
# ⚠️ 这份清单此前只登记了 node / plugin / resource 三个域，**tool 与 tutorial 完全没被覆盖**：
# 它们的列表/详情就算哪天丢了 `is_published` 过滤，草稿会直接出现在前台，而 CI 全绿
# （第五轮审查 MNT-11）。下面的 `test_every_public_read_function_is_guarded` 现在会
# 自动发现「引用了 is_published 却没登记」的公开读函数，新增域时漏登记直接红。
PUBLIC_SITES = [
    # ---- 教程（唯一的示例业务域）----
    ('tutorial_service.py', 'get_tutorials'),
    ('tutorial_service.py', 'get_tutorial_intro'),
    ('tutorial_service.py', 'get_tutorial_detail'),
    ('tutorial_service.py', 'list_purchased_tutorials'),
    # 单章正文：必须校验所属教程已发布，否则拿到教程 id 就能读草稿章节
    ('tutorial_node_service.py', '_get_tutorial_or_404'),
    # 免鉴权且被 LLM 爬虫长期抓取：草稿标题/简介一旦上榜就收不回来
    ('llms_txt_service.py', 'build_llms_txt'),
]

# 后台侧：列表不过滤（草稿要能看见）
ADMIN_SITES = [
    ('admin_tutorial_service.py', 'list_tutorials'),
]

# 后台服务文件（完整性守卫的扫描范围）
ADMIN_SITES_OWNERS = (
    'admin_tutorial_service.py',
)

# 公开读函数所在的服务文件（完整性守卫的扫描范围）
PUBLIC_SERVICE_FILES = (
    'tutorial_service.py',
    'tutorial_node_service.py',
)


def _func_source(filename: str, funcname: str) -> str:
    """取指定函数的源码文本（找不到返回空串）"""
    path = os.path.join(SVC, filename)
    src = io.open(path, encoding='utf-8').read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == funcname:
            return ast.get_source_segment(src, node) or ''
    return ''


def _sitemap_source() -> str:
    return io.open(os.path.join(SVC, 'sitemap_service.py'), encoding='utf-8').read()


class PublicSideFiltersDrafts(unittest.TestCase):
    """公开接口必须只出已发布内容"""

    def test_public_functions_require_published(self):
        for filename, funcname in PUBLIC_SITES:
            src = _func_source(filename, funcname)
            self.assertTrue(src, f"{filename}:{funcname} 未找到（函数改名了？）")
            self.assertIn(
                "is_published", src,
                f"{filename}::{funcname} 没有过滤 is_published —— 草稿会泄漏到前台",
            )

    def test_sitemap_requires_published(self):
        """sitemap 只能收已发布内容：草稿 URL 一旦被搜索引擎抓走就收不回来"""
        src = _sitemap_source()
        self.assertRegex(
            src, r"Tutorial\.is_published\s*==\s*True",
            "sitemap 的教程查询没有过滤 is_published",
        )

class AdminSideStillSeesDrafts(unittest.TestCase):
    """后台必须能看到草稿，否则保存后无法编辑"""

    def test_admin_lists_do_not_hard_filter_drafts(self):
        """后台列表不能**硬过滤**草稿，但可以按调用方传入的 `is_published` 筛选。

        区别很重要：`Tutorial.is_published == is_published`（参数化筛选，后台的
        「发布状态」下拉框要用）是正常功能；`is_published == True`（写死只出已发布）
        才是 bug —— 那样后台就再也看不到草稿，存了也编辑不了。
        """
        for filename, funcname in ADMIN_SITES:
            src = _func_source(filename, funcname)
            self.assertTrue(src, f"{filename}:{funcname} 未找到")
            self.assertNotIn(
                "is_published == True", src,
                f"{filename}::{funcname} 硬过滤了 is_published —— 后台将看不到草稿，"
                "保存后就再也编辑不了",
            )


class MigrationKeepsExistingContentVisible(unittest.TestCase):
    """迁移必须让存量内容保持已发布"""

    def test_existing_rows_default_to_published(self):
        # 按**文件名**识别这份迁移，而不是文件内容：后续迁移的 down_revision 里
        # 也会出现 `add_content_publish_state`，按内容匹配会误判成「有 2 份」
        hits = []
        for name in os.listdir(VERSIONS):
            if not name.endswith('.py') or 'add_content_publish_state' not in name:
                continue
            src = io.open(os.path.join(VERSIONS, name), encoding='utf-8').read()
            hits.append((name, src))
        self.assertEqual(len(hits), 1, "发布状态迁移文件数为 0 或不止 1")

        name, src = hits[0]
        self.assertIn('server_default', src, "迁移缺少 server_default")
        # 默认必须是 1：若为 0，部署后线上所有百科/扩展/资源会同时消失
        self.assertRegex(
            src, r"server_default\s*=\s*sa\.text\(\s*[\"']1[\"']",
            f"{name}: server_default 不是 1 —— 存量内容会被判为草稿而集体消失",
        )
        for table in ("nodes", "plugins", "resources"):
            self.assertIn(f'"{table}"', src, f"{name} 没有覆盖 {table}")

    def test_model_default_matches_migration(self):
        """模型侧 default=True 与迁移 server_default=1 必须同向，否则新建走 Python、
        迁移走 DB 会不一致"""
        for filename, model in (
            ("app/models/tutorial.py", "Tutorial"),
        ):
            src = io.open(os.path.join(REPO, filename), encoding='utf-8').read()
            self.assertRegex(
                src, r"is_published: Mapped\[bool\] = mapped_column\(",
                f"{model} 缺少 is_published 字段定义",
            )
            # 字段块内必须 default=True
            block = re.search(
                r"is_published: Mapped\[bool\] = mapped_column\((.*?)\)\n", src, re.S
            )
            self.assertIsNotNone(block, f"{model} 的 is_published 写法无法解析")
            self.assertIn("default=True", block.group(1), f"{model}.is_published 的 default 不是 True")
