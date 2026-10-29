"""公开详情响应的**公共字段集**（第五轮审查 EXT-04）。

## 问题

5 个内容详情（教程 / 百科 / 资源 / 扩展 / 工具）各自手写返回字典，字段集 9~25 个不等，
标题键还分两派：教程用 `title`，其余用 `name`。后果是：

- 「给所有详情加一个字段」（比如后来的 `seo_*`、`author_avatar_url`、`updated_at`）
  要改 5 个 service + 5 个页面，漏一个就表现为「某个详情页不显示作者」；
- 前端要为每个详情页写一套字段映射，`name` / `title` 的分裂还被带进了类型定义；
- 新增第 6 类内容时，没人知道「详情至少该有哪些键」。

## 做法

`build_detail_base()` 统一产出公共键，各 service 再 `extra` 合并专有键。
**只增不减**：原有键全部保留（`name` 作为 `title` 的别名继续存在），
所以这是一次**纯增量**收敛，前端可以按自己的节奏从 `name` 迁到 `title`。

## 为什么保留 `name`

`nodes` / `plugins` / `resources` / `tools` 四张表的列名就是 `name`，前端、小程序、
预渲染模板、以及外部 SEO 抓取结果里都有 `name`。改列名要动 4 张表 + 所有快照列 +
三端代码，收益只是「好看」。所以 `title` 是规范键，`name` 是**同值别名**，
由 `tests/test_detail_shape.py` 钉住两者恒等。
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import inspect as sa_inspect

from app.core.content_types import ContentType


def _iso(value: Any) -> str | None:
    """datetime → ISO 字符串；None 原样返回（前端按「无」处理）。"""
    return value.isoformat() if value is not None else None


# 已经警告过的 (模型, 字段)：只提醒一次，避免每个请求刷一行日志
_warned_unloaded: set[tuple[str, str]] = set()


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """读 ORM 属性，**遇到未加载的列不触发懒加载**。

    为什么必须这么做：查询里用 `load_only(...)` 裁剪大字段时，没被列进去的属性
    在**异步**会话里一读就抛 `MissingGreenlet`（同步上下文才会去发那条 SELECT）。
    而本模块是「公共字段」的产出点，谁漏了一列，整个详情接口就 500 ——
    2026-10-20 教程详情/介绍页全站 500 就是这么来的（漏了 `Tutorial.updated_at`）。

    这里退化成返回 `None` 并**只警告一次**：接口继续可用、字段为 null，
    日志里能直接看到「哪个模型的哪个字段没加载」——比 500 好，也比静默好。
    """
    state = sa_inspect(obj, raiseerr=False)
    if state is not None and name in state.unloaded:
        key = (type(obj).__name__, name)
        if key not in _warned_unloaded:
            _warned_unloaded.add(key)
            logger.warning(
                f"详情公共字段读取了未加载的列 {key[0]}.{key[1]}（查询的 load_only 漏了它）"
                " —— 本次返回 null，请把它补进 load_only"
            )
        return default
    return getattr(obj, name, default)


def build_detail_base(
    spec: ContentType,
    obj: Any,
    *,
    view_count: int | None = None,
    extra: dict | None = None,
) -> dict:
    """构造详情响应的公共部分。

    参数：
        spec: 该内容类型的注册表条目（决定标题列、分类列、封面列）。
        obj: ORM 实例。只读属性，不触发额外查询。
        view_count: 覆盖 `obj.view_count`。浏览量走 Redis 累加时，
            响应要给「DB 值 + 待落库增量」，不能直接用模型上的旧值。
        extra: 该类型专有的键（正文、章节、网盘链接等），最后合并进来，
            允许覆盖公共键（例如教程把 `cover_image` 换成处理后的地址）。

    返回的键顺序固定，便于快照类测试与人工 diff。
    """
    title = _attr(obj, spec.title_attr)
    base: dict[str, Any] = {
        "id": obj.id,
        # 前端据此决定用哪套渲染分支/评论目标类型，省掉「按路由猜类型」
        "module": spec.module,
        # 规范键
        "title": title,
        # 历史别名，与 title 恒等（见模块文档）；前端迁移完可移除
        "name": title,
        "description": _attr(obj, "description"),
        # 分类统一成 `category`：资源的列叫 resource_type，这里归一
        "category": _attr(obj, spec.category_attr),
        "cover_image": _attr(obj, spec.cover_attr) if spec.cover_attr else None,
        # 没有 view_count 列的类型（Tool 用 usage_count）给 None，而不是假 0：
        # 0 会被前端当成「真的没人看过」
        "view_count": view_count if view_count is not None else _attr(obj, "view_count"),
        "updated_at": _iso(_attr(obj, "updated_at")),
    }

    # 以下三组不是每种内容都有（Tool 既无作者也无 SEO 列），
    # 一律给 None 而不是省略键 —— 省略会让前端「有没有这个键」变成类型分支
    for key in (
        "author_name",
        "author_avatar_url",
        "seo_title",
        "seo_description",
        "seo_keywords",
    ):
        base[key] = _attr(obj, key)

    if extra:
        base.update(extra)
    return base


#: 公共键的固定清单（测试与文档用；新增公共字段时这里必须同步）
COMMON_DETAIL_KEYS: tuple[str, ...] = (
    "id",
    "module",
    "title",
    "name",
    "description",
    "category",
    "cover_image",
    "view_count",
    "updated_at",
    "author_name",
    "author_avatar_url",
    "seo_title",
    "seo_description",
    "seo_keywords",
)
