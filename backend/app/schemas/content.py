"""公开内容接口的响应模型（第五轮审查 EXT-06）。

## 背景

全项目 184 条路由里 171 条返回 `ApiResponse[dict]` —— OpenAPI 里就是「一个对象」，
前端只能靠人肉抄类型（`types/entities.ts` 手写、`Profile.tsx` 再抄一遍）。
真正的成本不在文档难看，而在**后端改字段名时没有任何一处会报错**：
前端读到 `undefined`，表现为「按钮不见了」「作者不显示了」。

## 为什么只覆盖公开内容读写面

`ApiResponse[dict]` 有 171 处，一次性补完不现实且收益递减（管理端表单字段极其发散）。
这里先覆盖**被前端/小程序/预渲染共同消费的 11 条公开接口**：5 个列表 + 6 个详情
（教程有 intro 与整篇两个入口）。它们字段稳定、消费方多，是最值得有契约的一层。
其余接口按需增量补，不追求「一次全绿」。

## ⚠️ 必须 `extra="allow"`

详情/列表的字段集随业务演进（`seo_*`、`author_*`、`cover_image` 都是后加的）。
pydantic 默认 `extra="ignore"`，那会在**响应序列化时把未声明的字段悄悄丢掉** ——
比没有 schema 更糟：接口突然少字段，前端静默空白，而 OpenAPI 看起来完全正确。
所以这里的模型一律 `extra="allow"`：**声明的是契约，不是白名单**。
`tests/test_content_schemas.py` 钉住这一点。
"""

from __future__ import annotations

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

#: 所有内容响应模型的共同配置：允许多余字段透传（原因见模块文档）
_PASSTHROUGH = ConfigDict(extra="allow")


class PageResult(BaseModel, Generic[T]):
    """分页列表响应体。

    与 `services/*_service` 返回的字典逐键对应；`page` / `page_size` 部分端点不返回，
    故为可选。
    """

    model_config = _PASSTHROUGH

    items: list[T] = Field(default_factory=list, description="当前页数据")
    total: int = Field(0, description="符合条件的总数")
    page: Optional[int] = Field(None, description="当前页码（部分端点不返回）")
    page_size: Optional[int] = Field(None, description="每页条数（部分端点不返回）")


class ItemsResult(BaseModel, Generic[T]):
    """只有 `items`、没有分页的列表响应（热门/最新/精选等「取前 N 条」接口）。

    ⚠️ 不要用 `PageResult` 代替：那会额外序列化出 `total: 0`，
    而这类接口本来就没有总数 —— 多出来的字段会让前端以为「总共 0 条」。
    `tests/test_public_list_shapes.py` 对这三个端点钉死了 `data` 的键集合。
    """

    model_config = _PASSTHROUGH

    items: list[T] = Field(default_factory=list, description="当前返回的数据")


class ContentListItem(BaseModel):
    """列表卡片项（5 个内容类型共用）。

    刻意只声明**跨类型稳定**的字段：卡片渲染用到的 id / 标题 / 简介 / 封面 / 分类 /
    浏览量 / 作者。各类型专有字段（`tool_type`、`resource_type`、`file_size` 等）
    靠 `extra="allow"` 透传 —— 强行统一成一个大模型只会让 OpenAPI 里每个字段
    都变成可选，反而更不可信。
    """

    model_config = _PASSTHROUGH

    id: int
    #: 规范标题键；`name` 是同值历史别名（node/plugin/resource/tool 的列名是 name）
    title: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    cover_image: Optional[str] = None
    category: Optional[str] = None
    view_count: Optional[int] = None
    author_name: Optional[str] = None
    author_avatar_url: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ContentDetailBase(BaseModel):
    """公开详情的公共字段集。

    与 `services/detail_shape.build_detail_base()` 一一对应（后者是唯一产出点）。
    各类型专有键（`content` / `inputs` / `netdisk_links` / `nodes` …）透传。
    """

    model_config = _PASSTHROUGH

    id: int
    module: Optional[str] = Field(None, description="内容类型键：tutorial/node/resource/plugin/tool")
    title: Optional[str] = None
    name: Optional[str] = Field(None, description="title 的历史别名，两者恒等")
    description: Optional[str] = None
    category: Optional[str] = None
    cover_image: Optional[str] = None
    view_count: Optional[int] = Field(None, description="无浏览量列的类型（工具）为 null")
    updated_at: Optional[str] = None
    author_name: Optional[str] = None
    author_avatar_url: Optional[str] = None
    seo_title: Optional[str] = None
    seo_description: Optional[str] = None
    seo_keywords: Optional[str] = None


class TutorialDetail(ContentDetailBase):
    """教程整篇详情（需登录）。正文与网盘链接在此声明，其余透传。"""

    content: Optional[str] = Field(None, description="整篇正文（章节化后通常为空，正文在章节接口）")
    status: Optional[str] = None
    access_mode: Optional[str] = None
    points_price: Optional[int] = None
    published_at: Optional[str] = None
    netdisk_links: list[dict] = Field(default_factory=list)


class TutorialIntro(ContentDetailBase):
    """教程介绍页（公开）：含章节树与阅读权限投影，但不含正文。"""

    introduction: Optional[str] = None
    status: Optional[str] = None
    access_mode: Optional[str] = None
    points_price: Optional[int] = None
    is_recommended: Optional[bool] = None
    published_at: Optional[str] = None
    can_read: Optional[bool] = Field(None, description="当前用户是否可读正文（游客恒 false）")
    purchased: Optional[bool] = None
    nodes: list[dict] = Field(default_factory=list, description="章节树（含 children）")
    flat: list[dict] = Field(default_factory=list, description="章节前序列表（翻章用）")
    node_count: Optional[int] = None
    netdisk_links: list[dict] = Field(default_factory=list)
    netdisk_link_count: Optional[int] = None


class NodeDetail(ContentDetailBase):
    """百科节点详情。

    ⚠️ `inputs` / `outputs` / `params` / `examples` 在库里是 **JSON 列**，形态不受约束：
    实测 `examples` 既有数组也有 `{"text": "..."}` 这样的对象。这里一律声明为
    `list | dict` —— 曾经写成 `Optional[list]`，于是 examples 为对象的节点详情
    直接 `ResponseValidationError` → 500（2026-10-20 发现，本地 11/16 节点中招）。
    """

    content: Optional[str] = None
    inputs: Optional[list | dict] = None
    outputs: Optional[list | dict] = None
    params: Optional[list | dict] = None
    examples: Optional[list | dict] = None


class PluginDetail(ContentDetailBase):
    """扩展详情。"""

    content: Optional[str] = None
    sources: list[dict] = Field(default_factory=list)


class ResourceDetail(ContentDetailBase):
    """资源详情。`resource_type` 与 `category` 同值（后者是统一键）。

    ⚠️ `content_list` / `usage_notes` / `tags` 在库里是 **JSON 列**（实测都是数组）。
    曾经把前两者写成 `Optional[str]`，于是「内容清单」「使用说明」一填就
    `ResponseValidationError` → 详情页 500（2026-10-20 发现，本地 8/16 资源中招）。
    """

    content: Optional[str] = None
    resource_type: Optional[str] = None
    file_size: Optional[str] = None
    content_list: Optional[list | dict] = None
    usage_notes: Optional[list | dict] = None
    download_count: Optional[int] = None
    is_featured: Optional[bool] = None
    is_pinned: Optional[bool] = None
    tags: Optional[list | dict] = None
    netdisk_links: list[dict] = Field(default_factory=list)
    netdisk_link_count: Optional[int] = None


class ToolDetail(ContentDetailBase):
    """工具详情。`ai_config`（提示词模板）**刻意不返回**，故不声明。"""

    tool_type: Optional[str] = None
    form_schema: Optional[dict] = None
    usage_count: Optional[int] = None
    access_mode: Optional[str] = None
    points_cost: Optional[int] = None
