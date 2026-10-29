# 图片搜索配图功能设计（P0/P1）

日期：2026-10-29
状态：已确认，进入实施

## 目标

在四类后台编辑器（教程、百科、扩展、资源）的工具栏增加「搜索图片」入口，从免版税图库检索图片，选中后下载、校验、转存 COS，再插入正文；同时把已上传/已导入的图片沉淀为「本站素材」，支持快速复用。

## 范围

首期（P0/P1）：

- 图源：Pexels（照片）、Pixabay（照片/插画/矢量）、本站素材。
- 搜索、预览、Alt、可选署名、单张插入。
- 后端聚合适配、Redis 搜索缓存、图源熔断与额度、管理员限额。
- 图片下载域名白名单、魔数/像素/大小校验、转 WebP、COS 转存、去重落库。
- 缩略图签名代理，解决热链与国内访问源 CDN 不稳定的问题。
- 上传图片自动进入本站素材库。

暂不做（P2）：

- Lummi、Openverse。
- 批量插入、收藏、标签编辑。
- AI 关键词翻译、图源健康面板、导入审计与下架回收。
- Unsplash 转存、斗图站爬虫、外链直插正文。

## 图源与授权

| 图源 | 额度 | 授权要求 | 处理 |
|---|---|---|---|
| Pexels | 200 次/小时，2 万次/月 | 免费商用；建议署名 | 默认插入署名 |
| Pixabay | 100 次/60 秒 | API 要求署名；禁止热链，要求先下载 | 默认插入署名；导入到 COS |
| 本站素材 | 本地 | 由上传者负责 | 直接复用 COS URL |

Unsplash 要求热链且不允许转存，不接入。表情包没有稳定官方 API 且版权风险高，改为本站素材库。

## 架构

```text
QuillEditor 工具栏
  → ImageSearchModal
  → 每个图源一个 GET /api/v1/admin/image-search/search 请求（前端并发）
  → 后端 Provider Adapter → Redis 缓存 / 熔断 / 额度
  → 缩略图签名代理
  → 预览 → POST /api/v1/admin/image-search/import
  → 下载白名单校验 → 转 WebP → COS → image_assets
  → quill.insertEmbed(COS URL, 'user') + alt + 署名段
```

前端按图源并发请求，先返回先渲染；单个图源失败只影响自己的分区。

## 数据模型

新表 `image_assets`：

- `id` BigInteger 主键
- `source` String(20)：pexels / pixabay / upload
- `source_id` String(64)：图源 ID；上传用 sha256
- `sha256` String(64)，索引
- `title` String(300)
- `alt` String(300)
- `author` String(200)
- `author_url` String(1000) nullable
- `page_url` String(1000) nullable
- `license` String(60)
- `license_url` String(1000) nullable
- `requires_attribution` Boolean
- `width` / `height` / `size_bytes` Integer
- `cos_url` String(1000)
- `thumb_url` String(1000) nullable
- `query` String(200)
- `tags` String(500)
- `created_by` BigInteger nullable
- `is_active` Boolean，默认 true
- `created_at` / `updated_at`

唯一索引 `(source, source_id)`；索引 `sha256`、`is_active`、`created_at`。

## 设置项

新增分组 `image_search`（系统设置 → 上传与素材）：

- `image_search_enabled`：总开关，默认关闭
- `image_search_pexels_key`：secret
- `image_search_pixabay_key`：secret
- `image_search_page_size`：默认 24
- `image_search_admin_hourly_limit`：默认 120
- `image_search_daily_import_limit`：默认 200

密钥缺省回退环境变量，运行期以数据库加密值为准；接口只返回 `configured`，不回显密钥。

## 接口

- `GET /api/v1/admin/image-search/options`
- `GET /api/v1/admin/image-search/search?source=&q=&page=&per_page=&type=&orientation=`
- `POST /api/v1/admin/image-search/import`
- `GET /api/v1/admin/image-search/thumb?token=`

权限全部复用内容编辑权限 `_CONTENT_EDIT_ANY`。导入接口只接受 `source + source_id`，不接受客户端 URL。

## 前端

- 工具栏新增 `search-image` 按钮，图标沿用 `CUSTOM_ICONS`。
- 弹窗包含搜索框、图源切换、类型/方向筛选、按图源分区结果、预览、Alt、署名勾选、插入按钮。
- 插入使用打开弹窗时记录的选区位置；`insertEmbed(..., 'user')` 保证撤销与未保存守卫生效。
- 全屏编辑器下弹窗 `z-index: 10000`。
- 本站素材分区优先展示。

## 安全与合规

- 密钥加密落库、日志脱敏、接口不回显。
- 图片下载仅允许各图源精确 CDN 域名、HTTPS、公网 IP、最多 3 跳重定向、流式 5MB 上限。
- 魔数与声明一致、20M 像素上限、拒绝 SVG/GIF，转 WebP 去 EXIF。
- 缩略图代理使用 HMAC 签名 URL，避免开放代理。
- 保存作者、原图页面、许可证；默认署名，强制署名图源不可关闭。
- 不爬取无官方 API 的表情包站，不接 Unsplash 转存。

## 性能

- Redis 搜索缓存 10 分钟；本站素材直查 DB。
- 单图源超时 8 秒；连续失败 3 次熔断 5 分钟。
- 图源额度：Pexels 180/小时、Pixabay 90/分钟。
- 缩略图签名代理 + 浏览器缓存 7 天。
- 导入优先 1600–2000px 变体，超过 5MB 自动降档，WebP 最长边 2400px。
- 目标：缓存命中 p95 < 150ms；单图源首次 p95 < 2.5s；导入 p95 < 4s。

## 验收

- 搜索两图源、单源失败不阻塞另一源。
- 插入后正文只出现 COS URL；Alt、署名、作者链接正确。
- 重复导入返回同一 URL。
- Ctrl+Z 可撤销图片与署名。
- 全屏、源码模式、窄屏行为正确。
- 非内容编辑管理员 403；关闭总开关后入口隐藏。
- 接口和日志不出现图源 Key。
