"""管理端 / 公开端检索行为一致性（第三轮增量审查 P1-41）。

背景
----
公开端节点/扩展搜索是「全文优先 + LIKE 兜底」，覆盖 name/description/content；
而管理端此前只写 ``Model.name.contains(...)``（**仅名称**）—— 同一句话在
前台搜得到、在后台搜不到，且两个文件几乎逐行同构、加字段时必然持续漂移。

修复后两侧共用 ``search.apply_keyword_filter``（一份行为）。本文件用真实
``MemorySession``（关闭全文走 LIKE 路径，避免 fake_db 不支持 MATCH 文本条件）
把「同一关键词两侧结论一致」钉死 —— 这正是漂移时期唯一会失败、而现在必须通过的场景。

⚠️ 本文件守的是**一致性**，不是「一定能搜到正文」：后者自第四轮全量审查 P1-60 起
在降级态被有意收窄（LIKE 不再扫 MEDIUMTEXT 正文，见 ``search._like_columns``）。
两侧口径变更要一起改，任何「一侧搜得到另一侧搜不到」的实现都会让这里变红。
"""

import unittest
from datetime import datetime, timezone
from unittest import mock

from app.config import settings
from tests.fake_db import MemorySession

_NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)

if __name__ == "__main__":
    unittest.main()
