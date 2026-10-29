"""内容类型的**唯一注册表**。

## 为什么还留着这个文件

即便现在只剩「教程」一种内容类型，本仓库仍然只在这里登记一次它的能力，
其余清单（分类体系、可评论、可收藏、用户记录可用性、下架通知文案、审计目标类型、
公开/后台列表 parity）全部从这里派生。

一种内容类型 = 一个 dataclass。新增第二种内容（比如「课程」「文章」）时：
① 本文件加一条；② 建模型/迁移；③ 前端加筛选项。漏掉某处不再是「静默少功能」，
而是导入期或测试直接失败。

## 与 `categories.module` 的关系

`module` 必须与 `categories.module` 的取值逐字相同（分类表按它建、列表接口按它查），
`tests/test_content_types.py` 会拿 `MODULE_TABLE_MAP` 做双向比对。

## ⚠️ 改名要慎重

`module` 同时被用在：Redis 列表缓存键前缀（`list:<module>:...`）、
`sitemap` 的 URL 分段、前端路由。改名会让已收录页面 404。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.tutorial import Tutorial


@dataclass(frozen=True)
class ContentType:
    """一种内容类型的全部能力声明。

    只放**代码常量级**的事实（表、列名、能力开关）。凡是要查库/读配置的都不放这里，
    否则会变成第二个 `setting_service`。
    """

    module: str
    """模块键。与 `categories.module` 逐字相同，也是列表缓存前缀与 URL 分段。"""

    label: str
    """面向用户的中文名（用于站内消息、后台标签）。"""

    model: Any | None
    """ORM 模型。预留类型（前端已有入口、后端实体未实现）为 None。"""

    title_attr: str = "title"
    """标题在模型上的属性名。"""

    published_attr: str | None = "is_published"
    """判断「是否公开可见」的属性名。None 表示该类型没有发布态（永远可见）。"""

    category_attr: str = "category"
    """分类字段在模型上的属性名（历史命名差异在这个字段里显式声明）。"""

    cover_attr: str | None = None
    """封面列名。没有封面列的类型为 None。"""

    favorite_cover: bool = False
    """收藏快照是否要存封面。"""

    commentable: bool = False
    """是否支持评论。开启后必须同时登记 `models/comment.py::COMMENT_TARGET_TYPES`。"""

    favoritable: bool = False
    """是否支持收藏。"""

    learnable: bool = False
    """是否有学习记录。"""

    availability_model: Any | None = None
    """「用户记录指向的目标是否仍可访问」要用哪张表判断。默认取 `model`。"""

    reserved: bool = False
    """前端已预留入口、后端实体尚未实现。这类类型的 model 必为 None。"""


#: 内容类型的唯一事实来源。**顺序**即后台下拉/文档展示顺序，与业务无关。
CONTENT_TYPES: dict[str, ContentType] = {
    "tutorial": ContentType(
        module="tutorial",
        label="教程",
        model=Tutorial,
        title_attr="title",
        cover_attr="cover_image",
        favorite_cover=True,
        commentable=True,
        favoritable=True,
        learnable=True,
    ),
}


# ---------------------------------------------------------------------------
# 派生视图：所有其它清单都应该从这里取，而不是自己再抄一份
# ---------------------------------------------------------------------------

#: 全部模块键（与 `categories.module` 相同集合）
MODULES: tuple[str, ...] = tuple(CONTENT_TYPES)

#: 有真实实体的类型（排除预留位）
IMPLEMENTED_MODULES: tuple[str, ...] = tuple(
    m for m, spec in CONTENT_TYPES.items() if spec.model is not None
)

#: 可评论的类型
COMMENTABLE_MODULES: tuple[str, ...] = tuple(
    m for m, spec in CONTENT_TYPES.items() if spec.commentable
)

#: 可收藏的类型
FAVORITABLE_MODULES: tuple[str, ...] = tuple(
    m for m, spec in CONTENT_TYPES.items() if spec.favoritable
)

#: 有学习记录的类型
LEARNABLE_MODULES: tuple[str, ...] = tuple(
    m for m, spec in CONTENT_TYPES.items() if spec.learnable
)


def get(module: str) -> ContentType:
    """按模块键取类型定义；未知键抛 KeyError（调用方应先用 `MODULES` 校验）。"""
    return CONTENT_TYPES[module]


def label_for(module: str, fallback: str = "内容") -> str:
    """取中文名。未知类型返回 fallback —— 站内消息不该因为类型名没登记就发不出去。"""
    spec = CONTENT_TYPES.get(module)
    return spec.label if spec else fallback


def model_for(module: str) -> Any | None:
    """取 ORM 模型；预留类型返回 None。"""
    spec = CONTENT_TYPES.get(module)
    return spec.model if spec else None


def availability_model_for(module: str) -> Any | None:
    """取「可用性判断」用的表；未知类型返回 None（调用方按「可用」处理）。"""
    spec = CONTENT_TYPES.get(module)
    if spec is None:
        return None
    return spec.availability_model or spec.model


def published_conditions(module: str) -> tuple:
    """取该类型的「已发布」SQL 条件；无发布态的类型返回空元组。

    返回的是 SQLAlchemy 表达式，必须由调用方在**已导入模型**的上下文里使用
    （本模块已导入全部模型，不会出现解析失败）。
    """
    spec = CONTENT_TYPES.get(module)
    if spec is None or spec.model is None or not spec.published_attr:
        return ()
    column = getattr(spec.model, spec.published_attr)
    return (column == True,)  # noqa: E712  SQLAlchemy 需要 `== True` 才能生成 `IS TRUE`
