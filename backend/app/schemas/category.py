"""分类 Schema"""
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# 分类名长度上限：与 categories.name 的 String(50) 对齐。
# 不设上限时超长名称会直落 MySQL，报 DataError → 500；这里挡成 422。
NAME_MAX_LENGTH = 50


class CategoryModuleItem(BaseModel):
    """一个「内容类型分组」（分类页的 Tab / 分组标题）。"""

    key: str
    label: str


class CategoryModulesData(BaseModel):
    """`GET /categories` 的 data：可管理分类的内容类型清单。"""

    modules: list[CategoryModuleItem]


class CategoryGroupCreate(BaseModel):
    """新建分组：module 必须是已登记的内容类型（与分类同一套白名单）。"""

    module: str
    name: str = Field(..., min_length=1, max_length=NAME_MAX_LENGTH)

    @field_validator("module")
    @classmethod
    def validate_module(cls, v: str) -> str:
        from app.services.category_service import MODULE_TABLE_MAP

        if v not in MODULE_TABLE_MAP:
            raise ValueError(f"module 必须是 {sorted(MODULE_TABLE_MAP)} 之一")
        return v


class CategoryGroupItem(BaseModel):
    """分组列表条目（左侧栏渲染用）。"""

    id: int
    name: str
    sort_order: int


class CategoryMoveRequest(BaseModel):
    """把分类移入分组；`group_id=None` 表示移出分组（回到「未分组」）。"""

    group_id: Optional[int] = None


class CategoryGroupUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=NAME_MAX_LENGTH)


class CategoryGroupReorder(BaseModel):
    """分组整组重排：ids 为该内容类型的**完整**分组 id 列表，顺序即排序值。"""

    ids: list[int] = Field(..., min_length=1)


class CategoryCreate(BaseModel):
    module: str
    name: str = Field(..., min_length=1, max_length=NAME_MAX_LENGTH)
    # 所属分组；不传或传 null = 未分组
    group_id: Optional[int] = None

    @field_validator("module")
    @classmethod
    def validate_module(cls, v: str) -> str:
        """白名单以 service 层的 MODULE_TABLE_MAP 为唯一事实来源。

        原先这里硬编码 ``{tutorial, resource, node, plugin}``，漏了 ``tool``——
        管理端「在线工具」Tab 能列出分类、却一新增就 422（模块白名单与
        路由白名单、前端 Tab 三处各写一份，必然漂移）。函数内导入避免
        schema → service 的模块级依赖（当前依赖方向是 service → schema）。
        """
        from app.services.category_service import MODULE_TABLE_MAP

        if v not in MODULE_TABLE_MAP:
            raise ValueError(f"module 必须是 {sorted(MODULE_TABLE_MAP)} 之一")
        return v


class CategoryUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=NAME_MAX_LENGTH)
    # 传 None = 不移动；要「移出分组」请用专门的移动接口（区分「没传」与「置空」）
    group_id: Optional[int] = None


class CategoryReorder(BaseModel):
    """整组重排：``ids`` 为该模块的**完整**分类 id 列表，顺序即新的排序值（从 1 开始）。

    要求完整而非增量：只重排一部分、其余保留旧值会留下难以解释的并列排序，
    而并列时 ``list_categories`` 按 id 兜底排序，运营看到的顺序会与预期不符。
    """

    ids: list[int] = Field(..., min_length=1)
