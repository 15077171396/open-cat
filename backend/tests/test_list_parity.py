"""public / admin 列表实现的 parity 守卫（缺口台账 P2-30）。

`*_service.py` 与 `admin_*_service.py` 是**按角色切分**而非按边界切分的：5 个内容域
每个域都有两套列表实现，序列化字典手抄两份。这类重复的危险不是"难看"，而是
**新增一列时必须记得改两处**，忘了就静默不一致（前台有、后台没有，或反之）。
关键词检索当初也是这样漂的，直到 P1-41 才收敛进 `search.apply_keyword_filter` ——
而排序、可见性、字段集这三类漂移此前**没有任何测试**覆盖
（`test_search_parity.py` 只管关键词，`test_public_list_shapes.py` 只管"取前 N 条"的形状）。

本文件把三类不变量钉住，全部是**静态 AST 检查**（不需要数据库，CI 离线可跑）：

1. `order_by` 必须以唯一列 `id` 收尾 —— 并列时 MySQL 不保证两次查询同序，
   分页就会重复或漏项（同 P2-8 分片、P2-36 兜底那条教训）。
2. 五个域的关键词只能走 `search.apply_keyword_filter`，不允许再出现本地
   `escape_like(...)` / `col.contains(...)` 手写 LIKE。
3. 两侧序列化键集合的差异必须**逐项登记**：新增漂移即红；差异被收敛了但登记表没删，
   也红（防它变成第二份 `AUDIT_EXEMPT` 式的垃圾场）。
"""

import ast
import io
import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
SVC = os.path.join(REPO, 'app', 'services')

# 域名 → (public 文件, public 列表函数, admin 文件, admin 列表函数)
# 只剩教程一个业务域：新增业务模块时在这里登记一行，本守卫才会检查它的公开/后台列表
# 键集合是否一致（漏登记 = 该模块的两侧字段漂移无人发现）。
DOMAINS = {
    'tutorial': ('tutorial_service.py', 'get_tutorials', 'admin_tutorial_service.py', 'list_tutorials'),
}

# 两侧键集合的**有意**差异（第四轮审查逐字段核对后的判定）：
#   admin-only：is_published（草稿态只有后台需要）、created_at（后台列表展示注册时间）
#   public-only：卡片渲染要用的展示字段（封面/简介/浏览量/标签/正文摘要等），
#                后台列表不渲染这些，但**新增时必须显式确认属于哪一侧**
INTENDED_DELTA = {
    'tutorial': {
        # 2026-10-13：后台教程列表改版后需要封面缩略图 / 简介 / 学习次数列，
        # 这三个键已由 public-only 收敛为两侧共有（不再登记）；
        # 更新时间仅后台列表展示（前台卡片不用），登记为 admin-only。
        'public_only': set(),
        'admin_only': {'is_published', 'updated_at'},
    },
}


def _parse(rel):
    return ast.parse(io.open(os.path.join(SVC, rel), encoding='utf-8').read())


def _fn(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f'函数 {name} 不存在（改名了就要同步本守卫）')


ENVELOPE_KEYS = {'items', 'total', 'page', 'page_size'}


def _serialize_keys(fn):
    """列表序列化是内联 dict 推导；取函数里的**行级** dict 字面量作为行形状。

    信封 dict（`{items, total, page, page_size}`）必须先排除 —— 它只有 4 个键，
    与某些行的键数相同，按"最大"挑会挑错（node 的 admin 行恰好也是 4 键）。
    """
    candidates = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if len(keys) > 2 and not set(keys) <= ENVELOPE_KEYS:
                candidates.append(set(keys))
    if not candidates:
        raise AssertionError('列表函数里找不到行级 dict 字面量，守卫需重写')
    return max(candidates, key=len)


def _order_calls(fn):
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'order_by':
            yield node


def _refs_id(arg) -> bool:
    return any(
        isinstance(n, ast.Attribute) and n.attr == 'id' for n in ast.walk(arg)
    )


class OrderByTiebreakTests(unittest.TestCase):
    def test_every_list_order_by_ends_with_id(self):
        offenders = []
        for domain, (pf, pfn, af, afn) in DOMAINS.items():
            for fname, fnname in ((pf, pfn), (af, afn)):
                fn = _fn(_parse(fname), fnname)
                for call in _order_calls(fn):
                    if not call.args:
                        continue
                    if not _refs_id(call.args[-1]):
                        offenders.append(f'{fname}:{call.lineno} {fnname}() 的 order_by 末列不是 id')
        self.assertFalse(
            offenders,
            '列表排序缺少唯一列兜底 → 并列行在两次查询间可能换序，分页重复/漏项：\n'
            + '\n'.join(offenders),
        )

    def test_comment_list_queries_also_tiebreak_on_id(self):
        """本守卫原先只扫 5 个内容域的列表函数，**评论不在内**（第五轮审查 R5-19）。

        而规则对评论同样成立、后果还更直接：`comment_service.list_comments` 的顶层页
        与 `admin_list_comments` 都只按秒级 `created_at` 排，同秒两条评论在两次查询间
        可能换序 → 前台翻页重复/漏项，后台审核「按所属内容删了一条、另一条还在」。
        同文件里取回复的查询本来都带了 id，唯独分页那两条漏了 —— 正是「规则有守卫、
        守卫清单靠手抄」会漏的那一类，所以把清单扩到评论而不是只修一次。
        """
        for fname, fns in (
            ('comment_service.py', ('list_comments', 'admin_list_comments')),
        ):
            mod = _parse(fname)
            for fnname in fns:
                fn = _fn(mod, fnname)
                calls = [c for c in _order_calls(fn) if c.args]
                self.assertTrue(calls, f'{fname}:{fnname}() 没有 order_by，本用例已失效')
                for call in calls:
                    self.assertTrue(
                        _refs_id(call.args[-1]),
                        f'{fname}:{call.lineno} {fnname}() 的 order_by 末列不是 id 兜底',
                    )

    def test_predicate_detects_a_missing_tiebreak(self):
        """反向对照：少写 id 兜底必须被抓出来，否则本守卫可能因解析失效而恒绿。"""
        bad = ast.parse(
            'async def f(db):\n'
            '    return (await db.execute('
            'select(T).order_by(T.view_count.desc(), T.updated_at.desc()))).all()\n'
        )
        fn = bad.body[0]
        calls = list(_order_calls(fn))
        self.assertEqual(len(calls), 1, '解析器没找到 order_by，守卫是恒绿的')
        self.assertFalse(_refs_id(calls[0].args[-1]))


class KeywordEntryTests(unittest.TestCase):
    def test_no_hand_rolled_like_filters_in_list_functions(self):
        """五个域的列表函数只能走 search 的统一入口（P1-41 收敛，P2-30 钉住不回退）。

        范围刻意是**列表函数体内**而不是整个文件：同一文件里还有别的 LIKE 是合理的
        （如 `resource_service.get_download_history` 对 `Download.resource_name` 的筛选，
        那是另一张表、没有全文索引也不参与站内检索）。
        """
        offenders = []
        for pf, pfn, af, afn in DOMAINS.values():
            for fname, fnname in ((pf, pfn), (af, afn)):
                fn = _fn(_parse(fname), fnname)
                for node in ast.walk(fn):
                    if not isinstance(node, ast.Call):
                        continue
                    name = getattr(node.func, 'attr', None) or getattr(node.func, 'id', None)
                    if name in ('contains', 'escape_like'):
                        offenders.append(f'{fname}:{node.lineno} {fnname}() 里手写了 {name}')
        self.assertFalse(
            offenders,
            '列表关键词绕过 apply_keyword_filter，public/admin 会再次漂移：\n' + '\n'.join(offenders),
        )

    def test_predicate_detects_a_hand_rolled_like(self):
        """反向对照：列表函数里真出现手写 LIKE 时必须被抓到。"""
        bad = ast.parse(
            'async def list_x(db):\n'
            '    kw = escape_like(keyword)\n'
            '    return T.name.contains(kw, escape="\\\\")\n'
        )
        fn = bad.body[0]
        hits = [
            getattr(n.func, 'attr', None) or getattr(n.func, 'id', None)
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and (getattr(n.func, 'attr', None) or getattr(n.func, 'id', None)) in ('contains', 'escape_like')
        ]
        self.assertEqual(sorted(hits), ['contains', 'escape_like'])

    def test_every_domain_declares_search_columns(self):
        """新增业务域时把它的 *_SEARCH_COLS 加进来：检索列少的域会在站内搜索里
        出现「搜不到正文」的怪现象（MULTI 全文索引缺列 → 只命中标题）。"""
        from app.services.search import TUTORIAL_SEARCH_COLS

        self.assertGreaterEqual(len(TUTORIAL_SEARCH_COLS), 2, '每个域的检索列至少含名称与简介')


class SerializationParityTests(unittest.TestCase):
    def _delta(self, domain):
        pf, pfn, af, afn = DOMAINS[domain]
        pub = _serialize_keys(_fn(_parse(pf), pfn))
        adm = _serialize_keys(_fn(_parse(af), afn))
        return pub - adm, adm - pub

    def test_key_delta_matches_the_registered_table(self):
        drift = []
        for domain in DOMAINS:
            public_only, admin_only = self._delta(domain)
            want = INTENDED_DELTA[domain]
            if public_only != want['public_only'] or admin_only != want['admin_only']:
                drift.append(
                    f'{domain}: 实际 public-only={sorted(public_only)} admin-only={sorted(admin_only)}'
                    f' | 登记值 public-only={sorted(want["public_only"])} '
                    f'admin-only={sorted(want["admin_only"])}'
                )
        self.assertFalse(
            drift,
            'public / admin 列表字段集出现未登记的差异（新增一列必须显式决定它属于哪一侧，'
            '并同步 INTENDED_DELTA）：\n' + '\n'.join(drift),
        )

    def test_registered_table_is_not_stale(self):
        """登记过的差异若已被收敛，必须从这里删掉 —— 否则表格变成第二份谎话。"""
        stale = []
        for domain in DOMAINS:
            public_only, admin_only = self._delta(domain)
            want = INTENDED_DELTA[domain]
            gone_pub = want['public_only'] - public_only
            gone_adm = want['admin_only'] - admin_only
            if gone_pub or gone_adm:
                stale.append(f'{domain}: 已不再差异的登记项 public={sorted(gone_pub)} admin={sorted(gone_adm)}')
        self.assertFalse(stale, '\n'.join(stale))


class DomainRegistryIsComplete(unittest.TestCase):
    """DOMAINS 必须覆盖「内容类型」全集（第五轮审查 MNT-11）。

    此前 DOMAINS / PUBLIC_SITES / PURGE_HOSTS 都是手抄清单，没有任何测试断言
    「这些清单 == 实际存在的内容类型集合」：新增第 6 类内容时漏加一行，CI 依然全绿，
    而该类内容的排序兜底、关键词入口、两侧字段漂移全部不受检查。
    `category_service.MODULE_TABLE_MAP` 是内容类型的权威清单（分类体系按它建），
    以它为基准做双向比对。
    """

    def test_domains_match_category_modules(self):
        from app.services.category_service import MODULE_TABLE_MAP

        self.assertEqual(
            set(DOMAINS), set(MODULE_TABLE_MAP),
            "test_list_parity.DOMAINS 与 category_service.MODULE_TABLE_MAP 不一致："
            "新增/删除内容类型时必须同步登记，否则该类内容完全不受 parity 守卫覆盖",
        )

    def test_domain_files_and_functions_exist(self):
        """域改名/函数改名时必须同步清单，否则守卫会静默空转"""
        for domain, (pub_file, pub_fn, adm_file, adm_fn) in DOMAINS.items():
            for filename, funcname in ((pub_file, pub_fn), (adm_file, adm_fn)):
                path = os.path.join(SVC, filename)
                self.assertTrue(os.path.exists(path), f"{domain}: 找不到 {filename}")
                src = io.open(path, encoding='utf-8').read()
                tree = ast.parse(src)
                names = {
                    n.name for n in ast.walk(tree)
                    if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                }
                self.assertIn(funcname, names, f"{domain}: {filename} 里没有 {funcname}")


if __name__ == '__main__':
    unittest.main()
