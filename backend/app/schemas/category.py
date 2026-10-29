"""分类 Schema"""
from pydantic import BaseModel, Field, field_validator

# 分类名长度上限：与 categories.name 的 String(50) 对齐。
# 不设上限时超长名称会直落 MySQL，报 DataError → 500；这里挡成 422。
NAME_MAX_LENGTH = 50


class CategoryCreate(BaseModel):
    module: str
    name: str = Field(..., min_length=1, max_length=NAME_MAX_LENGTH)

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


class CategoryReorder(BaseModel):
    """整组重排：``ids`` 为该模块的**完整**分类 id 列表，顺序即新的排序值（从 1 开始）。

    要求完整而非增量：只重排一部分、其余保留旧值会留下难以解释的并列排序，
    而并列时 ``list_categories`` 按 id 兜底排序，运营看到的顺序会与预期不符。
    """

    ids: list[int] = Field(..., min_length=1)
