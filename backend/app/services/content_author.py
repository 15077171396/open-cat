"""内容「发布作者」的自动归属（2026-10-12 需求）。

背景（用户需求）：
    「知识百科、资源、扩展模块，不应该自己手动写发布者和上传头像」
    —— 发布作者不该由运营手填，应由系统自动带出。

取值优先级：
1. **自动来源提供的值**：第三方导入（GitHub / 掘金 / 知乎…）抓到的原文作者、仓库 owner。
   这是最准确的作者，且同样是「自动」的，不需要人写。
2. **发布账号兜底**：没有自动来源时，用当前登录管理员的昵称（无昵称回落账号）+ 账号头像。
   单运营站点下等价于「本站发布」。

其它约定：
- 创建时按上述优先级写入。
- 编辑时**仅在作者为空时补写**。作者是「谁发布的」而不是「谁最后改的」，
  已有值绝不被覆盖；而本字段上线前创建的历史内容，一旦被编辑就自动归属，
  不必让运营回头逐条补录。
- 后台表单已移除这两个输入控件，运营侧无法（也无需）手工填写。

三个模块（plugin / resource / node）共用本模块，避免各写一份而漂移。
"""

from __future__ import annotations


def _clean(value) -> str | None:
    """空串 / 纯空白 / None 一律归一为 None（NULL = 未填写）"""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def author_fields(admin, provided_name=None, provided_avatar=None) -> dict:
    """推导作者字段：自动来源优先，其次发布账号

    ⚠️ `author_id` **只在作者取自账号时才写**：第三方导入带出的原文作者（如 GitHub owner）
        在站内没有对应账号，写了 id 反而会把展示绑到一个不相干的人身上。
    """
    nickname = _clean(getattr(admin, "nickname", None))
    account = _clean(getattr(admin, "account", None))
    avatar = _clean(getattr(admin, "avatar_url", None))
    from_source = _clean(provided_name) or _clean(provided_avatar)
    return {
        "author_id": None if from_source else getattr(admin, "id", None),
        "author_name": _clean(provided_name) or nickname or account,
        "author_avatar_url": _clean(provided_avatar) or avatar,
    }


def fill_author_if_absent(obj, admin, provided_name=None, provided_avatar=None) -> bool:
    """仅在作者为空时补写；返回是否真的写了（便于测试与调用方判断）"""
    if getattr(obj, "author_name", None) is not None:
        return False
    fields = author_fields(admin, provided_name, provided_avatar)
    obj.author_id = fields["author_id"]
    obj.author_name = fields["author_name"]
    obj.author_avatar_url = fields["author_avatar_url"]
    return True


async def load_authors(db, author_ids) -> dict[int, dict]:
    """批量取作者账号的**当前**昵称/头像，供前台实时展示（一次查询，避免 N+1）。

    返回 `{user_id: {"name": ..., "avatar": ...}}`；空集合直接返回 {}（不发查询）。
    """
    ids = {i for i in author_ids if i}
    if not ids:
        return {}
    from sqlalchemy import select

    from app.models.user import User

    rows = (
        await db.execute(select(User.id, User.nickname, User.account, User.avatar_url).where(User.id.in_(ids)))
    ).all()
    # ⚠️ 用**元组解包**而不是 r.id / r.nickname：真实 SQLAlchemy 返回 Row（named tuple，两种都行），
    # 但测试替身（MemorySession）只返回普通 tuple —— 解包对两者都成立，属性访问只对前者成立。
    return {
        uid: {"name": _clean(nickname) or _clean(account), "avatar": _clean(avatar)}
        for uid, nickname, account, avatar in rows
    }


def apply_live_author(item: dict, author_id, authors: dict[int, dict]) -> None:
    """用账号当前值覆盖快照（就地改 item）。

    - 账号存在 → 用**当前**昵称/头像（这就是「改资料要联动」的实现点）；
      头像取账号当前值（含 None），这样用户删掉头像后卡片会回落到首字兜底。
    - 账号已被删除 / 本来就没有账号（第三方导入的作者）→ 保留快照，行为不变。
    """
    live = authors.get(author_id) if author_id else None
    if not live:
        return
    if live["name"]:
        item["author_name"] = live["name"]
    item["author_avatar_url"] = live["avatar"]


async def apply_live_authors(db, rows, items) -> None:
    """`rows` 与 `items` 一一对应时，批量把「账号当前值」覆盖到 `items` 上。

    列表/详情序列化后调用一次即可；内部只发一条 `users` 查询。
    """
    authors = await load_authors(db, [getattr(r, "author_id", None) for r in rows])
    for r, item in zip(rows, items):
        apply_live_author(item, getattr(r, "author_id", None), authors)


async def apply_live_author_one(db, row, item: dict) -> None:
    """单条（详情页）版本：语义同 `apply_live_authors`"""
    author_id = getattr(row, "author_id", None)
    authors = await load_authors(db, [author_id])
    apply_live_author(item, author_id, authors)
