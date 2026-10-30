"""内容 API：对外只读命名空间（`/api/v1/content/*`）。

**为什么单独开一个命名空间**

后台管理端调的是 `/api/v1/admin/*`，而渲染站点要的是教程 / 分类 / 公告 / 首页聚合 ——
两类接口此前混在同一个前缀下。后果是：对外只能整份 OpenAPI 甩出去，读者分不清
「哪些是给我用的、哪些是后台内部的」；而内容接口想做破坏性变更时，又和管理端绑在一起。

独立前缀后：
- 内容 API 有自己的一份文档：`/api/v1/content/docs`（只列内容端点）；
- 管理端接口的调整不再牵连内容接口的兼容承诺。

**为什么不直接把旧路径搬过来**

本仓库只开源后台，仓库外还有用户端与小程序在用 `/api/v1/tutorials` 这类旧路径。
直接搬会静默打断它们（表现为整站空白，而不是报错）。所以旧路径保留、在 OpenAPI 里
标记 `deprecated=True`，下个大版本再删。

**为什么内容 API 不是纯只读**

站点渲染完正文要上报阅读量（`POST /tutorials/{id}/track`），这是内容能力的一部分。
但付费解锁（`purchase_tutorial_endpoint`）属于交易，不进内容 API —— 见 `_WRITE_ENDPOINTS_ALLOWED`。
"""

from fastapi import APIRouter
from fastapi.routing import APIRoute

from app.api.announcements import router as announcements_router
from app.api.categories import router as categories_router
from app.api.home import router as home_router
from app.api.tutorials import router as tutorials_router

# 对外完整前缀（= api_router 的 /api/v1 + 本 router 的 /content）
CONTENT_PREFIX = "/api/v1/content"

# 允许进入内容命名空间的写端点（按 endpoint 函数名登记，路径改动不会让白名单失效）
# - track_tutorial：浏览埋点，站点渲染后必然要上报阅读量
# 刻意**不含** purchase_tutorial_endpoint：付费解锁是交易，不是内容
_WRITE_ENDPOINTS_ALLOWED = frozenset({"track_tutorial"})

content_router = APIRouter(prefix="/content", tags=["内容 API"])


def _mount_read_only(target: APIRouter, source: APIRouter, prefix: str) -> list[str]:
    """把 source 的只读端点原样挂到 target 的 `prefix` 下，返回挂载后的路径列表。

    用 `add_api_route` 复用同一个 endpoint 函数，而不是把路由重写一遍：
    权限依赖、响应模型、参数校验全部跟着函数签名走，不会出现「内容 API 少了一层鉴权」
    这种只有线上才会暴露的分叉。
    """
    mounted: list[str] = []
    for route in source.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.methods != {"GET"} and route.name not in _WRITE_ENDPOINTS_ALLOWED:
            continue
        target.add_api_route(
            f"{prefix}{route.path}",
            route.endpoint,
            methods=sorted(route.methods),
            response_model=route.response_model,
            tags=route.tags,
            summary=route.summary,
            description=route.description,
            name=route.name,
            dependencies=route.dependencies,
        )
        mounted.append(f"{prefix}{route.path}")
    return mounted


# 挂载清单在导入期一次算好，供测试断言（避免测试里再写一遍期望值）
MOUNTED_PATHS: dict[str, list[str]] = {
    "tutorials": _mount_read_only(content_router, tutorials_router, "/tutorials"),
    "categories": _mount_read_only(content_router, categories_router, "/categories"),
    # 首页聚合挂在命名空间根上：GET /api/v1/content 就是「一屏所需的所有内容」
    "home": _mount_read_only(content_router, home_router, ""),
    "announcements": _mount_read_only(content_router, announcements_router, "/announcements"),
}


def build_content_openapi(app) -> dict:
    """从应用完整 OpenAPI 里裁出内容命名空间那一份。

    只裁剪 `paths`，`components` 整体保留 —— 被裁掉的接口引用的 schema 仍可能被
    内容接口复用（如 `PageResult[ContentListItem]`），裁掉 components 会让文档里
    出现断链的 `$ref`，比多带几个未用 schema 更糟。
    """
    import copy

    schema = copy.deepcopy(app.openapi())
    schema["paths"] = {
        path: item
        for path, item in schema.get("paths", {}).items()
        if path == CONTENT_PREFIX or path.startswith(f"{CONTENT_PREFIX}/")
    }
    schema["info"] = {
        "title": "内容 API",
        "version": schema.get("info", {}).get("version", "1.0.0"),
        "description": (
            "对外只读内容接口：教程（含章节树）/ 分类 / 公告 / 首页聚合。\n\n"
            "鉴权：除浏览埋点外均为匿名可读；需要用户身份的接口（如付费解锁）不在本命名空间。\n"
            "后台管理接口见 `/api/v1/admin/*`，不在本文档范围内。"
        ),
    }
    return schema
