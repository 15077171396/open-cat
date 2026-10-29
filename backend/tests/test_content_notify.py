"""下架通知的纯逻辑单测（不连库：用最小假会话验证「发给谁 / 发什么 / 不带链接」）。"""

import unittest

from app.services.content_notify import notify_favoriters_unpublished


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    """只实现 notify 用到的 execute / add"""

    def __init__(self, favorite_user_ids):
        self._ids = favorite_user_ids
        # 批量 INSERT 的行（实现已从「逐条 db.add」改为一条 insert，见 content_notify 注释）
        self.inserted: list[dict] = []
        # 保留 add 通道：万一将来回退成 ORM 逐条写，这个假会话仍能工作
        self.added = []

    async def execute(self, _stmt, params=None, **_kwargs):
        if params is not None:
            self.inserted.extend(params)
            return _Result([])
        return _Result(self._ids)

    def add(self, obj):
        self.added.append(obj)


class NotifyFavoritersTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_favoriters_sends_nothing(self):
        db = _Session([])
        sent = await notify_favoriters_unpublished(
            db, target_type="node", target_id=1, title="某篇文章"
        )
        self.assertEqual(sent, 0)
        self.assertEqual(db.inserted, [])

    async def test_each_favoriter_gets_one_message(self):
        db = _Session([7, 8, 8])  # 重复的 user_id 必须去重
        sent = await notify_favoriters_unpublished(
            db, target_type="tutorial", target_id=2, title="教程 A"
        )
        self.assertEqual(sent, 2)
        self.assertEqual(len(db.inserted), 2, "每个收藏者恰好一条消息")
        msg = db.inserted[0]
        self.assertEqual(msg["type"], "system")
        self.assertIn("已下架", msg["title"])
        self.assertIn("教程 A", msg["content"])
        # 目标已下架，给链接等于把用户送进 404 —— 通知里刻意不带 link
        self.assertIsNone(msg["link"])
        # 主键必须显式给：批量 INSERT 不走 ORM 的 Python 端 default
        self.assertTrue(all(row["id"] for row in db.inserted))

    async def test_unknown_type_still_uses_generic_label(self):
        db = _Session([9])
        await notify_favoriters_unpublished(
            db, target_type="unknown_type", target_id=3, title="某内容"
        )
        self.assertEqual(len(db.inserted), 1)
        self.assertIn("内容已下架", db.inserted[0]["title"])

    async def test_empty_title_falls_back(self):
        db = _Session([10])
        await notify_favoriters_unpublished(db, target_type="node", target_id=4, title="   ")
        self.assertIn("你收藏的内容", db.inserted[0]["content"])


if __name__ == "__main__":
    unittest.main()
