# 图片搜索配图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在后台编辑器提供多图源图片搜索，选中后转存 COS 并插入正文，同时沉淀本站素材库。

**Architecture:** 后端新增 `image_assets` 表、`image_search` 服务与四个管理接口；前端在 `QuillEditor` 工具栏增加入口与弹窗，按图源并发请求，插入时只使用 COS URL。Pexels/Pixabay 适配器归一化成统一 `ImageHit`，导入流水线复用 COS 与图片校验能力。

**Tech Stack:** FastAPI、SQLAlchemy async、Alembic、Redis、httpx、Pillow、React 18、Ant Design 6、Quill 2、Vitest。

**Spec:** `docs/superpowers/specs/2026-10-29-image-search-design.md`

## Global Constraints

- 权限复用内容编辑权限 `_CONTENT_EDIT_ANY`，不新增权限码。
- 正文只插入 COS URL，不插入外部图源地址。
- 图源 Key 只存加密设置，接口/日志不得回显。
- 导入接口只接受 `source + source_id`，不接受 URL。
- 下载域名精确白名单、HTTPS、公网 IP、最多 3 跳重定向、5MB 流式上限。
- 图片魔数一致、20M 像素上限、拒绝 SVG/GIF，统一转 WebP。
- 站点默认插入署名；Pixabay 署名不可关闭。
- 所有新设置键必须登记进 `backend/tests/test_settings_parity.py` 的 `ADMIN_ONLY_SETTINGS`，新分组必须登记进 `settingsNav.ts`。

---

### Task 1: 数据模型、迁移与设置项

**Files:**
- Create: `backend/app/models/image_asset.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/20261029_0066_add_image_assets.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/services/setting_definitions.py`
- Modify: `admin-frontend/src/pages/Settings/settingsNav.ts`
- Modify: `backend/tests/test_settings_parity.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/test_image_asset_model.py`

**Interfaces:**
- Produces: `ImageAsset` ORM，字段见 spec；迁移 revision `add_image_assets`，down_revision `add_ai_writing`。
- Produces: settings keys `image_search_enabled` / `image_search_pexels_key` / `image_search_pixabay_key` / `image_search_page_size` / `image_search_admin_hourly_limit` / `image_search_daily_import_limit`。
- Produces: `Settings.IMAGE_SEARCH_*` 环境变量字段。

- [ ] 写模型与迁移（唯一索引 `source+source_id`，索引 `sha256/is_active/created_at`）。
- [ ] 在 `models/__init__.py` 导入并加入 `__all__`。
- [ ] 注册设置分组与定义；密钥 `secret=True, public=False`；非密钥 `public=False`。
- [ ] `settingsNav.ts` 的 `media` 分类加入 `image_search`。
- [ ] `ADMIN_ONLY_SETTINGS` 加入 6 个键。
- [ ] 测试：模型可实例化、迁移头链正确、设置分组无空组、导航覆盖。
- [ ] 跑 `pytest tests/test_image_asset_model.py tests/test_settings_parity.py tests/test_settings_nav.py -q`。
- [ ] Commit。

### Task 2: Provider 抽象与 Pexels/Pixabay 适配器

**Files:**
- Create: `backend/app/services/image_search/__init__.py`
- Create: `backend/app/services/image_search/base.py`
- Create: `backend/app/services/image_search/providers.py`
- Test: `backend/tests/test_image_search_providers.py`

**Interfaces:**
- Produces:
  - `@dataclass ImageHit(source, source_id, thumb_url, download_url, width, height, alt, title, author, author_url, page_url, license, license_url, requires_attribution)`
  - `@dataclass SearchPage(items, total, has_more)`
  - `class ImageSearchProvider`：`name`、`license_name`、`license_url`、`requires_attribution`、`image_hosts`、`async search(query, page, per_page, image_type, orientation)`、`async get(source_id)`、`async close()`
  - `build_provider(source, api_key, transport=None) -> ImageSearchProvider`
  - `configured_sources(keys: dict[str,str]) -> list[str]`
- Pexels：`GET https://api.pexels.com/v1/search`，`Authorization: <key>`，字段 `photos[].src.large/large2x`、`alt`、`photographer`、`photographer_url`、`url`。
- Pixabay：`GET https://pixabay.com/api/`，query `key/q/image_type/orientation/page/per_page/safesearch=true`，字段 `hits[].webformatURL/largeImageURL`、`imageWidth/Height`、`user`、`pageURL`、`tags`。

- [ ] 写失败测试：Pexels 归一化、Pixabay 归一化、空结果、401/429/5xx 映射、无 Key 不发请求。
- [ ] 实现 base 与两个适配器；所有异常翻译为 `AppException`（401/403→503 配置错误，429→429，超时→504，其他→502）。
- [ ] 测试 URL 编码、`per_page` 上限、orientation/type 映射。
- [ ] 跑 `pytest tests/test_image_search_providers.py -q`。
- [ ] Commit。

### Task 3: 搜索服务（缓存、熔断、额度、本站素材）

**Files:**
- Create: `backend/app/services/image_search/config.py`
- Create: `backend/app/services/image_search/service.py`
- Create: `backend/app/services/image_search/local.py`
- Test: `backend/tests/test_image_search_service.py`

**Interfaces:**
- Consumes: `ImageHit`、`SearchPage`、`build_provider`、`ImageAsset`。
- Produces:
  - `async load_config(db) -> ImageSearchConfig(enabled, page_size, hourly_limit, daily_import_limit, keys)`
  - `async search(db, admin_id, source, query, page, per_page, image_type, orientation) -> dict`
  - `async resolve_hit(db, source, source_id) -> ImageHit`
  - `async search_local(db, query, page, per_page) -> dict`
- Redis keys：`image_search:cache:{source}:{sha1}`、`image_search:item:{source}:{id}`、`image_search:health:{source}`、`image_search:quota:{source}:{bucket}`、`image_search:user:{admin_id}:{scope}:{bucket}`。

- [ ] 写失败测试：缓存命中不调用 provider、缓存写入、单图源失败熔断、429 额度、本站素材 LIKE 搜索/分页、密钥回退环境变量。
- [ ] 实现配置读取（一次查询 + `decrypt_secret` + env 回退）。
- [ ] 实现缓存（TTL 600）、item 缓存（TTL 1800）、熔断（3 次失败/5 分钟）、额度（Pexels 180/h、Pixabay 90/min）、管理员 120/h。
- [ ] 实现本地素材查询（`is_active`、`title/alt/query/tags contains`、按创建时间倒序）。
- [ ] Redis 异常时记录 `degrade_open` 并继续，不因缓存/额度不可用拒绝搜索。
- [ ] 跑 `pytest tests/test_image_search_service.py -q`。
- [ ] Commit。

### Task 4: 导入流水线与缩略图代理

**Files:**
- Create: `backend/app/services/image_search/importer.py`
- Create: `backend/app/services/image_search/thumb.py`
- Test: `backend/tests/test_image_search_importer.py`
- Test: `backend/tests/test_image_search_thumb.py`

**Interfaces:**
- Produces:
  - `async import_hit(db, hit, *, alt, query, admin_id) -> ImageAsset`
  - `async download_image(url, allowed_hosts, max_bytes, timeout) -> (bytes, content_type)`
  - `def normalize_image(data, content_type) -> (bytes, width, height)`（同步，调用方 `run_heavy`）
  - `def sign_thumb(url: str, exp_seconds: int = 604800) -> str`
  - `def verify_thumb(token: str) -> str`
  - `async fetch_thumb(url, allowed_hosts, max_bytes=2MB) -> (bytes, content_type)`

- [ ] 写失败测试：重复导入返回旧记录、域名白名单拒绝、HTTP/私网拒绝、重定向到非白名单拒绝、超大/像素炸弹拒绝、WebP 输出、COS 调用参数、签名过期/篡改拒绝。
- [ ] 实现下载与校验；使用 `validate_image_pixels`、`is_allowed_image`、`run_heavy`。
- [ ] 实现 WebP 归一化（EXIF 旋转、最长边 2400、去元数据）。
- [ ] 实现 COS 上传 `folder=f"images/{source}"`、`IntegrityError` 竞态回查、`created_by`。
- [ ] 实现缩略图签名（HMAC-SHA256 + base64url）与代理抓取。
- [ ] 跑 `pytest tests/test_image_search_importer.py tests/test_image_search_thumb.py -q`。
- [ ] Commit。

### Task 5: 管理接口与 RBAC

**Files:**
- Create: `backend/app/schemas/image_search.py`
- Create: `backend/app/api/admin_image_search.py`
- Modify: `backend/app/api/admin.py`
- Modify: `backend/app/core/rbac.py`
- Test: `backend/tests/test_admin_image_search_api.py`

**Interfaces:**
- `GET /api/v1/admin/image-search/options` → `{enabled, page_size, cos_configured, sources:[...], local_available}`
- `GET /api/v1/admin/image-search/search` → `{source, items, has_more, total, cached}`
- `POST /api/v1/admin/image-search/import` → `{url, width, height, alt, credit_html, asset_id, deduped}`
- `GET /api/v1/admin/image-search/thumb?token=...` → image bytes

- [ ] 写失败测试：RBAC 未登记 403/内容编辑 200、参数校验、未开启 503、单源失败状态码、导入成功/重复、缩略图签名非法 403。
- [ ] 实现 Pydantic 请求/响应模型与路由；`success_response` 包装。
- [ ] 注册 4 条 RBAC 规则，全部 `_CONTENT_EDIT_ANY`。
- [ ] `admin.py` 导入新模块。
- [ ] 跑 `pytest tests/test_admin_image_search_api.py -q`。
- [ ] Commit。

### Task 6: 上传图片自动进入本站素材

**Files:**
- Modify: `backend/app/api/uploads.py`
- Create: `backend/app/services/image_search/asset_recorder.py`
- Test: `backend/tests/test_upload_image_asset.py`

**Interfaces:**
- `async record_upload_asset(db, *, url, data, content_type, filename, admin_id) -> None`
- 失败只记录日志，不影响上传响应。

- [ ] 写失败测试：上传成功写入 `image_assets`（source=upload, source_id=sha256）；DB 异常时仍返回 URL。
- [ ] 在 `/uploads/image` 成功后调用记录函数并 commit；异常回滚后只记日志。
- [ ] 跑 `pytest tests/test_upload_image_asset.py -q`。
- [ ] Commit。

### Task 7: 前端类型、API 客户端与搜索状态

**Files:**
- Create: `admin-frontend/src/components/image-search/types.ts`
- Create: `admin-frontend/src/components/image-search/imageSearchApi.ts`
- Create: `admin-frontend/src/components/image-search/useImageSearch.ts`
- Test: `admin-frontend/src/components/image-search/imageSearchApi.test.ts`
- Test: `admin-frontend/src/components/image-search/useImageSearch.test.ts`

**Interfaces:**
- `getImageSearchOptions(force?)`：模块级 Promise 缓存 5 分钟。
- `searchSource({source,q,page,perPage,type,orientation,signal})`。
- `importImage({source,sourceId,alt,query})`。
- `useImageSearch()`：`query/setQuery/source/setSource/results/loading/loadMore/retry`，按图源并发、AbortController 取消旧请求、会话内结果缓存。

- [ ] 写失败测试：并发分源、慢源不阻塞快源、单源失败保留其他结果、取消旧请求、loadMore、选项缓存。
- [ ] 实现 API 与 hook。
- [ ] 跑 `pnpm test -- imageSearchApi useImageSearch`（或仓库实际包管理器）。
- [ ] Commit。

### Task 8: 搜索弹窗与预览

**Files:**
- Create: `admin-frontend/src/components/image-search/ImageSearchModal.tsx`
- Create: `admin-frontend/src/components/image-search/ImageSearchGrid.tsx`
- Create: `admin-frontend/src/components/image-search/ImageSearchPreview.tsx`
- Create: `admin-frontend/src/styles/sections/image-search.css`
- Modify: `admin-frontend/src/styles/custom.css`
- Test: `admin-frontend/src/components/image-search/ImageSearchModal.test.tsx`

**Interfaces:**
- Props: `open, initialQuery, onClose, onInsert({url, alt, creditHtml, requiresAttribution, attribution})`。
- 分区：本站素材、Pexels、Pixabay；每个分区独立 loading/error/retry/loadMore。
- 预览：Alt 必填、署名勾选（强制署名锁定）、插入按钮（COS 未配置时禁用）。

- [ ] 写失败测试：搜索防抖、分源渲染、单源错误、Alt 为空禁用、强制署名锁定、插入回调。
- [ ] 实现组件与 CSS（白 + 浅蓝，沿用 AI 写作视觉；`z-index: 10000`）。
- [ ] 跑 `pnpm test -- ImageSearchModal`。
- [ ] Commit。

### Task 9: QuillEditor 工具栏与插入

**Files:**
- Modify: `admin-frontend/src/components/editor/toolbarConfig.ts`
- Modify: `admin-frontend/src/components/QuillEditor.tsx`
- Modify: `admin-frontend/src/styles/sections/editor.css`（或现有编辑器样式）
- Test: `admin-frontend/src/components/QuillEditor.imageSearch.test.tsx`

**Interfaces:**
- 工具栏新增 `search-image`，handler 打开弹窗；`CUSTOM_ICONS` 增加放大镜图标；`TOOLBAR_TITLES` 增加提示。
- 打开时记录 `quill.getSelection(true)` 与选中文字；插入时在该位置 `insertEmbed(url,'image','user')`、`formatText(alt)`，可选插入署名片段（`\n图片：...` + link）。
- 全屏 `z-index: 10000`；源码模式禁用。

- [ ] 写失败测试：按钮存在/禁用逻辑、打开带入选中文字、插入调用 COS URL、署名开关、插入位置、撤销 source='user'。
- [ ] 实现工具栏与弹窗接线。
- [ ] 手动验证四个编辑页共用入口。
- [ ] 跑 `pnpm test -- QuillEditor.imageSearch`。
- [ ] Commit。

### Task 10: 文档、配置样例与全量验证

**Files:**
- Modify: `backend/.env.example`
- Modify: `docs/03-开发/项目说明.md`
- Test: 全量后端/前端门禁

- [ ] 补充 Pexels/Pixabay Key 申请与设置路径说明。
- [ ] 跑 `python -m pytest -q`、`ruff check app tests`。
- [ ] 跑 `pnpm test`、`pnpm lint`、`pnpm build`。
- [ ] 浏览器实测：配置密钥、搜索、预览、插入、Ctrl+Z、全屏、窄屏、单源失败、重复导入。
- [ ] 确认 `img src` 只出现 COS URL、署名链接正确、Key 不出现在响应/日志。
- [ ] Commit。
