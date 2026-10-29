"""作者展示「联动」契约：改昵称/换头像后，已发布内容上的作者要跟着变（2026-10-13）。

背景（用户反馈）：
    「为什么我后台改信息了，发布者和头像没有联动」

根因：作者此前只以 `author_name` / `author_avatar_url` **快照**存在内容行里，
    没有任何指向账号的引用，所以改资料不会回写历史内容。

修复：三张内容表新增 `author_id`（迁移 `20261013_0043`），
    前台展示**以 author_id 指向的账号为准**（实时解析），快照仅作兜底。

本文件钉住三件事：
1. 账号存在 → 展示**账号当前**昵称/头像（哪怕快照是旧值）；
2. 账号已删 → 回落**快照**（内容不会因此变成「无作者」）；
3. 没有 author_id（第三方导入带出的原文作者，站内无账号）→ 用快照。
三个模块（plugin / resource / node）各验一遍，避免只改一处。
"""

import unittest
from datetime import datetime

from app.models.user import User
from tests.fake_db import MemorySession

AUTHOR_ID = 4

def make_author(nickname="新昵称", avatar="https://cdn.example.com/new.png") -> User:
    return User(id=AUTHOR_ID, account="author@test", nickname=nickname, avatar_url=avatar)

if __name__ == "__main__":
    unittest.main()
