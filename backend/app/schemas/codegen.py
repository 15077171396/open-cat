"""代码生成器请求 / 响应模型。"""

from pydantic import BaseModel, Field


class CodegenColumn(BaseModel):
    name: str
    type: str
    nullable: bool
    comment: str
    primary: bool
    editable: bool


class CodegenSuggested(BaseModel):
    module: str
    class_name: str
    title: str
    route_prefix: str
    fields: list[str]


class CodegenTable(BaseModel):
    name: str
    comment: str
    columns: list[CodegenColumn]
    suggested: CodegenSuggested


class CodegenTablesData(BaseModel):
    available: bool = True
    database: str = ""
    error: str = ""
    items: list[CodegenTable] = Field(default_factory=list)


class CodegenFile(BaseModel):
    path: str
    content: str


class CodegenPreviewData(BaseModel):
    available: bool = True
    warnings: list[str] = Field(default_factory=list)
    files: list[CodegenFile] = Field(default_factory=list)


class CodegenDownloadRequest(BaseModel):
    table: str = Field(..., min_length=1, max_length=64)
    module: str = Field(..., min_length=3, max_length=40)
    class_name: str = Field(..., min_length=2, max_length=60)
    title: str = Field("", max_length=80)
    route_prefix: str = Field("", max_length=80)
    fields: list[str] = Field(default_factory=list, max_length=100)
