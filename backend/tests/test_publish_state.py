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
    # ---- 百科 ----
    ('node_service.py', 'list_nodes'),
    ('node_service.py', 'get_node_detail'),
    # 分类计数也必须同口径，否则「Tab 显示 30 个、点进去 28 条」（第二轮复审 C-03）
    ('node_service.py', 'get_node_stats'),
    # ---- 扩展 ----
    ('plugin_service.py', 'list_plugins'),
    ('plugin_service.py', 'get_featured_plugins'),
    ('plugin_service.py', 'get_plugin_detail'),
    # ---- 资源 ----
    ('resource_service.py', 'get_resources'),
    ('resource_service.py', 'get_resource_detail'),
    ('resource_service.py', 'get_download_links'),
    # 旁路查询：不经过主列表函数，最容易漏
    ('resource_service.py', 'get_hot_resources'),
    ('resource_service.py', 'get_latest_resources'),
    # 下载历史是用户自己的流水，但目标已下架时不该再出现在列表里
    ('resource_service.py', 'get_download_history'),
    # 分类计数同口径（第二轮复审 C-03）
    ('resource_service.py', 'get_resource_stats'),
    # ---- 工具（此前无守卫）----
    ('tool_service.py', 'get_tools'),
    ('tool_service.py', 'get_tool_detail'),
    # ---- 教程（此前无守卫）----
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
    ('admin_node_service.py', 'list_nodes'),
    ('admin_plugin_service.py', 'list_plugins'),
    ('admin_resource_service.py', 'list_resources'),
    ('admin_tool_service.py', 'list_tools'),
    ('admin_tutorial_service.py', 'list_tutorials'),
]

# 后台服务文件（完整性守卫的扫描范围）
ADMIN_SITES_OWNERS = (
    'admin_node_service.py',
    'admin_plugin_service.py',
    'admin_resource_service.py',
    'admin_tool_service.py',
    'admin_tutorial_service.py',
)

# 公开读函数所在的服务文件（完整性守卫的扫描范围）
PUBLIC_SERVICE_FILES = (
    'node_service.py',
    'plugin_service.py',
    'resource_service.py',
    'tool_service.py',
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
        src = _sitemap_source()
        for model in ("Node", "Plugin", "Resource"):
            # 形如 _entity_query(Model, Model.id, ..., where=Model.is_published == True)
            pattern = rf"_entity_query\(\s*{model}\b[^)]*is_published"
            self.assertRegex(src, pattern, f"sitemap 的 {model} 查询没有过滤 is_published")

    def test_hot_and_latest_queries_filtered(self):
        """热门 / 最新这类旁路查询容易漏 —— 它们不经过主列表函数"""
        src = io.open(os.path.join(SVC, 'resource_service.py'), encoding='utf-8').read()
        # 热门资源与最新资源各一处 .where(Resource.is_published == True)
        self.assertGreaterEqual(
            src.count("Resource.is_published == True"), 4,
            "resource_service 的 is_published 过滤点少于预期（列表/详情/下载/热门/最新）",
        )
        plugin_src = io.open(os.path.join(SVC, 'plugin_service.py'), encoding='utf-8').read()
        self.assertGreaterEqual(
            plugin_src.count("Plugin.is_published == True"), 3,
            "plugin_service 的 is_published 过滤点少于预期（列表/精选/详情）",
        )


class GuardListsAreComplete(unittest.TestCase):
    """两份清单本身必须「自动发现漏登记」（MNT-11）。

    原清单是手抄的：新增一个域时忘加一行，该域的公开接口就**完全不受发布态检查**，
    而 CI 依然全绿 —— 这类「守卫自己漏了」比没有守卫更危险，因为它给了虚假的安全感。

    判定规则：一个公开服务函数只要满足「名字以 list_/get_ 开头」且「源码里出现
    `is_published`」，它就是在做发布态过滤，必须登记进 PUBLIC_SITES。
    写/改类函数（`run_tool` / `purchase_tutorial` / `check_tutorial_read_access`）
    不做这个判定 —— 它们检查发布态是权限判断，不是「过滤草稿」。
    """

    def _public_read_funcs(self, filename: str):
        src = io.open(os.path.join(SVC, filename), encoding='utf-8').read()
        tree = ast.parse(src)
        found = []
        for node in tree.body:
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if not node.name.startswith(('list_', 'get_')):
                continue
            seg = ast.get_source_segment(src, node) or ''
            if 'is_published' in seg:
                found.append(node.name)
        return found

    def test_every_public_read_function_is_guarded(self):
        registered = set(PUBLIC_SITES)
        missing = []
        for filename in PUBLIC_SERVICE_FILES:
            for func in self._public_read_funcs(filename):
                if (filename, func) not in registered:
                    missing.append(f"('{filename}', '{func}')")
        self.assertEqual(
            missing,
            [],
            "以下公开读函数在过滤 is_published，却没有登记进 PUBLIC_SITES —— "
            "它们的过滤条件被改坏时不会有任何测试报警：\n  " + "\n  ".join(missing),
        )

    def test_every_admin_list_function_is_guarded(self):
        """后台每个 `list_*` 都必须登记（否则「后台不该过滤草稿」这条不会被验证）。"""
        registered = set(ADMIN_SITES)
        missing = []
        for filename in ADMIN_SITES_OWNERS:
            src = io.open(os.path.join(SVC, filename), encoding='utf-8').read()
            tree = ast.parse(src)
            for node in tree.body:
                if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name.startswith('list_'):
                    if (filename, node.name) not in registered:
                        missing.append(f"('{filename}', '{node.name}')")
        self.assertEqual(
            missing, [],
            "以下后台列表函数未登记进 ADMIN_SITES：\n  " + "\n  ".join(missing),
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
            ("app/models/node.py", "Node"),
            ("app/models/node.py", "Plugin"),
            ("app/models/resource.py", "Resource"),
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


class UpdateRequestsAcceptPublishState(unittest.TestCase):
    """编辑页的「发布」开关必须真的能改到库。

    回归背景（第五轮审查 EXT-01）：前端三个编辑页都提交了 `is_published`，后端 service
    也写了完整处理分支（含下架通知收藏者），但三个 Update 请求模型都没声明该字段 ——
    pydantic 默认 `extra=ignore` 把字段静默丢弃，`updates.get("is_published")` 恒为 None，
    于是「关掉发布 → 保存成功 → 内容仍在线上」。全程不报错、不 500，测试也拦不住。
    """

    def test_update_models_declare_is_published(self):
        from app.api.admin_nodes import NodeUpdateRequest
        from app.api.admin_plugins import PluginUpdateRequest
        from app.api.admin_resources import ResourceUpdateRequest

        for model in (NodeUpdateRequest, PluginUpdateRequest, ResourceUpdateRequest):
            self.assertIn(
                "is_published", model.model_fields,
                f"{model.__name__} 缺 is_published —— 编辑页的下架/存草稿会被静默丢弃",
            )
            # 必须是「不传就不改」，不能用 True/False 覆盖既有状态
            self.assertIsNone(
                model.model_fields["is_published"].default,
                f"{model.__name__}.is_published 默认值必须是 None（不传即不改动）",
            )

    def test_create_models_declare_it_once(self):
        """Create 模型里同一字段声明两次时后者覆盖前者，注释与行为会脱节"""
        import inspect

        from app.api.admin_nodes import NodeCreateRequest
        from app.api.admin_plugins import PluginCreateRequest
        from app.api.admin_resources import ResourceCreateRequest

        for model in (NodeCreateRequest, PluginCreateRequest, ResourceCreateRequest):
            src = inspect.getsource(model)
            self.assertEqual(
                src.count("is_published:"), 1,
                f"{model.__name__} 里 is_published 声明了不止一次（重复声明会互相覆盖）",
            )


if __name__ == '__main__':
    unittest.main()
