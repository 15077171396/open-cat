# 内容 API 最小示例

一个 `index.html`，没有构建步骤、没有依赖 —— 用来回答一个问题：
**「后台管内容，我自己的站点怎么把内容取出来渲染？」**

打开后它做两件事：

1. 匿名视角：`GET /api/v1/content` 拿到统计 / 教程列表 / 分类 / 公告，
   点教程再用 `GET /api/v1/content/tutorials/{id}/intro` 拿到简介与**章节树**；
2. 带 token 时：点章节用 `GET /api/v1/content/tutorials/{id}/nodes/{node_id}` 取正文。

---

## 先搞清边界：哪些接口匿名可读

这一点最初写错过（把「详情」当成了匿名接口，页面直接 401），所以单独说清：

| 接口 | 匿名 | 说明 |
|---|:--:|---|
| `GET /api/v1/content` | ✅ | 首页聚合：统计 / 教程 / 分类 / 公告 |
| `GET /api/v1/content/tutorials` | ✅ | 教程列表（分页） |
| `GET /api/v1/content/tutorials/{id}/intro` | ✅ | **详情页首选**：简介 + 章节树 + 能否阅读 |
| `GET /api/v1/content/tutorials/{id}/nodes` | ✅ | 章节标题树（不含正文） |
| `GET /api/v1/content/categories` · `/announcements` | ✅ | 分类 / 公告 |
| `GET /api/v1/content/tutorials/{id}` | ❌ | 教程详情：免费教程登录即读，会员教程需 VIP 或已购买 |
| `GET /api/v1/content/tutorials/{id}/nodes/{node_id}` | ❌ | 章节正文，同上 |
| `POST /api/v1/content/tutorials/{id}/track` | ❌ | 阅读埋点（带用户身份才有意义） |

> 为什么正文要登录：涉及免费时长与付费解锁，匿名放开等于把付费内容送出去。

---

## 跑起来（3 步）

### 1. 让后端允许跨域

示例是独立页面（默认 `http://localhost:8080`），后端在 `:8000`，属于跨域。
在 `backend/.env` 里加一行并重启后端：

```bash
CORS_ORIGINS=http://localhost:8080
```

> 为什么必须显式配：`allow_credentials=True` 与 `*` 不兼容，后端启动时会直接拒绝通配符
> （见 `app/config.py`），所以只能写具体来源。
> 生产环境更省事的做法是同源反代（nginx 把 `/api` 转到后端），那样根本不需要 CORS。

### 2. 起一个静态服务

```bash
cd examples/frontend-minimal
python -m http.server 8080
```

### 3. 打开

匿名视角（只能看简介与章节标题）：

<http://localhost:8080/>

想看正文就带上用户 token：

```bash
# 演示数据里的普通用户（口令来自 seed_demo.py 的 DEMO_PASSWORD）
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"account":"demo01@example.com","password":"Demo@123456"}' | jq -r .data.access_token)

echo "http://localhost:8080/?token=$TOKEN"
```

后端不在本机时，用 `?api=` 指定：

```
http://localhost:8080/?api=http://10.0.0.9:8000/api/v1/content
```

---

## 接口清单与等价 curl

```bash
curl -s http://localhost:8000/api/v1/content | jq '.data.stats'
curl -s 'http://localhost:8000/api/v1/content/tutorials?page=1&page_size=5' | jq '.data.items[].title'
curl -s http://localhost:8000/api/v1/content/tutorials/4675248031/intro | jq '.data.flat'

# 需要 token 的两个
curl -s http://localhost:8000/api/v1/content/tutorials/4675248031 -H "Authorization: Bearer $TOKEN" | jq '.data.title'
curl -s http://localhost:8000/api/v1/content/tutorials/4675248031/nodes/4480959980 \
  -H "Authorization: Bearer $TOKEN" | jq '.data.content'
```

完整定义（含字段说明）：<http://localhost:8000/api/v1/content/docs>

---

## 换成你自己的渲染层时，注意四件事

1. **响应有信封**：所有接口都是 `{code, message, data}`，`code === 0` 才算成功。
   只看 HTTP 200 会把业务失败当成功（示例里的 `get()` 就是干这个的）。
2. **列表是分页的**：`data` 里是 `{items, total, page, page_size}`，不是裸数组。
3. **正文是 HTML**：`node.content` 是后台富文本编辑器产出的 HTML，直接 `innerHTML`
   等于把 XSS 防线交给后台的敏感词与审核流程。对外站点建议再过一遍白名单清洗
   （后台前端用的是 DOMPurify）。
4. **`/intro` 比 `/tutorials/{id}` 更适合详情页**：前者匿名可读且已含章节树，
   后者要登录。前台详情页通常只需要 `/intro` + 一次正文请求。

---

## 它不是什么

- 不是可直接上线的站点：没有路由、没有缓存、没有 SEO、没有错误上报；
- 不是 SDK：只有 40 行取数逻辑，看懂之后请按自己的技术栈重写。

如果你的目标是「30 分钟内有个能看的前台」，拿这个文件当起点改是最快的；
如果目标是长期维护的站点，建议直接用 `/api/v1/content/openapi.json` 生成类型。
