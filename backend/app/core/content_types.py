"""内容类型的**唯一注册表**（第五轮审查 EXT-02）。

## 为什么要有这个文件

「内容类型」这件事在本仓库曾经被登记了 9 次，每处都是一份手写清单：

| 登记点 | 原来写在哪 |
|---|---|
| 分类体系 | `services/category_service.py::MODULE_TABLE_MAP` |
| 可评论 | `models/comment.py::COMMENT_TARGET_TYPES` + `comment_service._TARGET_SPECS` |
| 可收藏 | `favorite_service._TARGET_SPECS` / `TARGET_TYPES` |
| 用户记录可用性 | `services/content_availability.py::_TYPE_MODELS` |
| 下架通知文案 | `services/content_notify.py::_TYPE_LABELS` |
| 删除时清理哪些用户记录 | 4 个 `admin_*_service.py` 里 16 处手工 `purge_*` 调用 |
| 公开/后台列表 parity | `tests/test_list_parity.py::DOMAINS` |
| 审计目标类型 | `models/admin_audit_log.py` |
| 前端（用户端筛选项 / 管理端标签） | `CommentSection.tsx`、`Comments/List.tsx`、小程序 |

这些清单**彼此已经漂移**：`tool` 有分类、有可用性判断，却不能收藏也不能评论；
`workflow` 在收藏侧是预留位、在其他清单里根本不存在；审计侧曾漏掉 `tool`
（EXT-08）。漂移本身不报错，只表现为「某类内容少一个功能」，靠人肉核对才发现。

所以这里用**一个** dataclass 描述一种内容类型的全部能力，其余清单全部从它派生。
新增第 6 类内容时只需要：① 本文件加一条；② 建模型/迁移；③ 前端加筛选项。
「漏了某一处」从「静默少功能」变成「导入期/测试直接失败」。

## 与 `categories.module` 的关系

`module` 字段必须与 `categories.module` 的取值逐字相同（分类表按它建、列表接口按它查），
`tests/test_content_types.py` 会拿 `MODULE_TABLE_MAP` 做双向比对。

## ⚠️ 改名要慎重

`module` 同时被用在：Redis 列表缓存键前缀（`list:<module>:...`）、
`sitemap`/`llms.txt` 的 URL 分段、前端路由。改名会让已收录页面 404。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.node import Node, Plugin
from app.models.resource import Resource
from app.models.tool import Tool
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
    """标题在模型上的属性名。教程叫 `title`，其余叫 `name` —— 历史原因，不要统一改
    （改了要动 4 张表 + 所有快照列 + 前端字段）。"""

    published_attr: str | None = "is_published"
    """判断「是否公开可见」的属性名。None 表示该类型没有发布态（永远可见）。"""

    category_attr: str = "category"
    """分类字段在模型上的属性名。资源用 `resource_type`（历史命名，语义等同分类）。"""

    cover_attr: str | None = None
    """封面列名。`plugin` / `tool` 没有这一列，为 None。"""

    favorite_cover: bool = False
    """收藏快照是否要存封面。

    ⚠️ 只有教程为 True：`nodes` / `resources` 也有 `cover_image` 列，但收藏快照
    **历史上只存教程封面**，存量行里其余类型都是 NULL。中途开始写入会让新旧行
    一半有封面一半没有 —— 属于需要单独决定的数据口径变更，不该混在一次重构里。
    详情响应不受这个开关影响（`build_detail_base` 用 `cover_attr`）。
    """

    commentable: bool = False
    """是否支持评论。开启后必须同时登记 `models/comment.py::COMMENT_TARGET_TYPES`。"""

    favoritable: bool = False
    """是否支持收藏。"""

    learnable: bool = False
    """是否有学习记录（目前只有教程）。"""

    availability_model: Any | None = None
    """「用户记录指向的目标是否仍可访问」要用哪张表判断。默认取 `model`。

    预留类型 `workflow` 借用 Tool 表：这是收敛**之前**就有的行为
    （`content_availability` 里 `"workflow": Tool`），此处原样保留并显式记录 ——
    它的实际语义是「workflow 收藏永远查不到实体」，属于已知的占位行为。
    """

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
    "node": ContentType(
        module="node",
        label="百科",
        model=Node,
        title_attr="name",
        cover_attr="cover_image",
        commentable=True,
        favoritable=True,
    ),
    "resource": ContentType(
        module="resource",
        label="资源",
        model=Resource,
        title_attr="name",
        category_attr="resource_type",
        cover_attr="cover_image",
        commentable=True,
        favoritable=True,
    ),
    "plugin": ContentType(
        module="plugin",
        label="扩展",
        model=Plugin,
        title_attr="name",
        # 扩展可收藏但**不可评论**：评论入口只在教程/百科/资源三个详情页实现，
        # 这是产品选择而非遗漏（`tests/test_comment_target_registry.py` 有记录）。
        favoritable=True,
    ),
    "tool": ContentType(
        module="tool",
        label="工具",
        model=Tool,
        title_attr="name",
        # ⚠️ 已知漂移（EXT-02 记录在案）：工具既不能收藏也不能评论。
        # 不是忘了写，而是前端从未有入口；若要做，改这里 + 前端收藏筛选即可，
        # 收藏/评论的写入链路本身是按类型分发的，无需改表。
        availability_model=Tool,
    ),
    "workflow": ContentType(
        module="workflow",
        label="工作流",
        model=None,
        title_attr="title",
        published_attr=None,
        # 预留位：前端收藏页有「工作流」筛选项、小程序同样有，实体表尚未实现。
        # 删除它属于**产品决策**（要同步改两端筛选项），不要当清理顺手删。
        favoritable=True,
        availability_model=Tool,
        reserved=True,
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
