# 贡献指南

先说结论：**这个项目对代码风格很宽松，对三条工程约定很严格。**
违反风格不会被拒，违反约定一定过不了 CI。

---

## 一、三条硬约定

这三条不是偏好，是这套后台能长期维护的原因。CI 里有测试守着它们，写错了会红。

### 1. 新增写操作必须写审计

任何「改数据」的接口，都要在同一事务里留下一条审计记录：

```python
from app.services.admin_audit_service import record_admin_action

async def delete_something(db, target_id: int, actor: User) -> None:
    obj = await _get_or_404(db, target_id)
    obj.deleted_at = utcnow()
    record_admin_action(          # ⚠️ 只 add，不 commit
        db, actor, "delete",
        target_type="something", target_id=target_id,
        summary=f"删除 xxx {obj.title}",
    )
    await db.commit()             # 审计与业务同事务提交
```

`record_admin_action` **不 commit**，由调用方在同一事务提交。这样「业务成功但审计丢失」在物理上不可能发生。
漏写会被 `tests/test_admin_audit_coverage.py` 抓住。

### 2. 新增管理端接口必须在路由规则表登记

路由规则表是 **fail-closed** 的：没登记的接口一律 403，而不是默认放行。
「新接口忘了加权限判断」在这里的后果是**调不通**，不是**裸奔**。

### 3. 新增权限码必须同步菜单项

权限码、菜单、前端路由、后端依赖注入四处由 `app/services/rbac_catalog.py` 单一声明派生。
只加权限码不加菜单项，会出现「有权限但没入口」；反过来会出现「有入口但点进去 403」。两种都有测试守着。

> 加一个完整业务模块的八步流程见[开发文档 §五](docs/07-开源文档/05-开发文档.md)。
> 前五份样板文件可以用 `python scripts/new_module.py <模块名> --label <中文名>` 生成。

### 补充：写操作幂等（可选，**不是**硬约定）

后端有个中间件（`app/core/idempotency.py`）对 `/api/v1/admin` 下的写方法生效：
请求带 `Idempotency-Key` 头时，同一个 key 的重复提交只执行一次，第二次直接重放首次响应。

前端用法是**一次用户动作一个 key**，在动作开始前生成（弹确认框时），不要每次请求现生成：

```ts
const key = newIdempotencyKey()          // 弹窗打开时
await apiPost('/admin/users/batch-delete', { ids }, idempotentConfig(key))
```

新端点不需要额外改动就会获得这个能力；**不带 key 的请求行为完全不变**，
所以它是可选增强，不影响既有调用方。

---

## 二、环境准备

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.11+ | 生产镜像同为 3.11，CI 也是 3.11 |
| Node.js | **22** | 不要更低：构建脚本依赖 22.6+ |
| MySQL | 8.0+ | 依赖 `ngram` 中文全文索引，必须 `--ngram_token_size=2` |
| Redis | 7+ | 限流 / 缓存 / 令牌黑名单 |

MySQL 与 Redis 是**运行**依赖，不是**测试**依赖——后端单测全程离线可跑（见下）。

---

## 三、本地启动

```bash
# 后端
cd backend
python -m venv venv
venv/Scripts/activate            # Windows；macOS/Linux 用 source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env             # 填 DB / Redis / SECRET_KEY
mysql -u root -p < init_db.sql
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 前端（另开一个终端，别和后端共用终端）
cd admin-frontend
npm install
npm run dev                      # http://localhost:5174/admin/
```

首次打开会跳 `/admin/setup` 安装向导（库里还没有管理员时）。
也可以 `python seed_data.py` 建号，口令取 `.env` 的 `ADMIN_PASSWORD`；留空则随机生成并**只打印一次**。

---

## 四、提交前自检

CI 跑的就是这几条，本地先过一遍能省一轮来回。

```bash
# 后端单测：全程离线，端口指向 1 是为了让「偷偷连真库」的用例立刻失败
cd backend && DB_PORT=1 REDIS_PORT=1 python -m pytest tests/ -q

# 后端静态检查
ruff check backend

# 前端 lint / 类型检查 + 构建 / 单测
cd admin-frontend && npm run lint && npm run build && npm test

# 零依赖静态自检（CI 也跑这些）
python scripts/check_ci_config.py          # CI 版本与生产镜像是否对齐
python scripts/check_docs_links.py         # 文档链接与锚点
python scripts/check_env_consistency.py    # compose ↔ .env 模板 ↔ Settings 三方一致
python scripts/check_doc_stats.py          # 文档里的接口/表/迁移/测试数是否漂移
python scripts/check_nginx_conf.py         # nginx 配置静态检查
python scripts/check_compose_scalars.py    # compose 折叠标量里不能有 #
```

> `check_doc_stats.py` 会真的 import `app` 去数路由和表。改了接口数量而没同步文档，
> 这条会红——**这是刻意的**：README 里写的「222 端点」漂移过 20%，人肉维护靠不住。

---

## 五、提交信息

用 Conventional Commits，中文描述，前缀对齐现有历史：

```
feat(admin): 用户列表支持按注册来源筛选
fix(auth): 修 TOTP 校验在时钟偏移下的误判
refactor(ui): 列表页统一换 DataTable
docs: 部署说明书补备份恢复章节
chore(db): 清理下线模块遗留表
```

`scope` 常用值：`admin` / `auth` / `ui` / `db` / `export` / `storage` / `ops`。
一个 PR 只做一件事；重构与功能改动不要混在同一个提交里。

---

## 六、提交 PR

1. Fork → 从 `main` 拉分支，命名 `feat/xxx`、`fix/xxx`；
2. 按上面「提交前自检」跑一遍；
3. PR 描述里写清**为什么改**（不是改了什么，diff 自己会说）；
4. 涉及 UI 的改动请附截图，涉及接口的请说明是否破坏兼容；
5. CI 全绿再请求 review。

Review 时被问得最多的三个问题，提前想好答案：
**这个写操作审计了吗？接口登记了吗？权限码和菜单同步了吗？**

---

## 七、不要做的事

- **不要提交 `.env`**，也不要往 `.env.example` 里填任何真实凭据（模板会进版本库）；
- **不要在业务代码里 `create_all`**：建表只走 Alembic，CI 会校验迁移链单头且可逆；
- **不要用同步 DB 调用**：`api/` 与 `services/` 全异步，混进一个同步调用会拖住整个事件循环；
- **不要在循环里触发懒加载**：列表查询显式 `selectinload` / `joinedload`；
- **不要为了过 CI 关掉检查**：`# noqa`、`eslint-disable`、删测试，都会被 review 打回。

---

## 八、行为准则

讨论对事不对人。技术分歧用基准测试或复现步骤说话，不用音量。
骚扰、人身攻击、歧视性言论一律请出。

---

## 许可

提交 PR 即表示你同意以 [MIT](LICENSE) 许可发布你的贡献。
