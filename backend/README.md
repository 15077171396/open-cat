# AI 猫平台 — 后端服务（`backend/`）

FastAPI + SQLAlchemy 2.0（异步）+ MySQL 8 + Redis 7 + Alembic + APScheduler。

> ⚠️ **本文只写「在 `backend/` 里怎么干活」**：路由清单、表结构、字段口径、业务规则等**清单类信息一律以
> [项目说明](../docs/03-开发/项目说明.md) 为唯一来源**，本文不复制（曾各自维护一份接口表，两份都过期且互相矛盾）。
>
> 相关文档：[技术架构](../docs/02-架构/技术架构.md)（分层与决策依据）· [部署说明书](../docs/04-运维/部署说明书.md)（生产运行）· [缺口台账](../docs/06-待办/缺口台账.md)（待办与已知限制）

---

## 1. 快速开始

```bash
cd backend
python -m venv venv
.\venv\Scripts\pip.exe install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple   # Windows
# venv/bin/pip install -r requirements.txt                                                        # Linux / macOS

copy .env.example .env        # Windows；Linux/macOS 用 cp。填 DB_* / REDIS_* / SECRET_KEY / DEEPSEEK_API_KEY
.\venv\Scripts\alembic.exe upgrade head
.\venv\Scripts\python.exe seed_data.py      # 建管理员与基础数据
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Swagger `http://localhost:8000/docs` · ReDoc `/redoc` · 探活 `/api/v1/health` · 深度探活 `/api/v1/health/deep`
- 默认管理员：账号 `admin@aicat.com`，口令由 `seed_data.py` 决定 —— 有 `ADMIN_PASSWORD` 环境变量就用它，否则随机生成 `secrets.token_urlsafe(16)` 并**只打印一次**（登录后请立即改）
- 也可以直接双击 `../scripts/start-backend.bat`（常驻窗口，不受开发会话影响）
- ⚠️ Windows 下 uvicorn 的 `--reload` 会波及其他常驻进程，后端与前端 dev server **要分终端跑**

## 2. 目录与职责

```
backend/
├── app/
│   ├── main.py               # 应用入口：中间件、异常处理、lifespan
│   ├── config.py             # ⚠️ 配置在这里，不是 app/core/config.py
│   ├── database.py           # 异步引擎 + 非自增 ID 生成 + 会话时区锁定
│   ├── redis.py              # Redis 异步连接池
│   ├── dependencies.py       # get_current_user / require_admin 等依赖
│   ├── api/                  # 路由层：参数校验 + 依赖注入 + 响应包装（禁止出现 ORM 查询）
│   │   ├── router.py         #   聚合入口 + 2 条健康探针
│   │   ├── admin.py          #   管理端聚合入口（自身 0 路由）
│   │   ├── admin_*.py        #   15 个管理域模块（原 1515 行的 admin.py 按域拆出）
│   │   └── _admin_shared.py  #   共享 router 与跨域模型
│   ├── services/             # 业务逻辑（含 ai/ 与 third_party_fetch/ 两个子包）
│   ├── models/               # SQLAlchemy 模型
│   ├── schemas/              # Pydantic 出入参
│   ├── core/                 # security / crypto / permissions / rbac / rate_limit / exceptions / monitoring / request_context
│   └── utils/                # verification / sanitize / time 等
├── scheduler/tasks.py        # APScheduler 定时任务（独立容器运行，不在 app 进程内）
├── alembic/versions/         # 迁移
├── tests/                    # pytest（离线可跑，见上）
├── seed_data.py              # 基础种子（管理员 + 少量教程）
└── seed_demo.py             # 演示数据（教程/分类/评论/反馈/公告，图片真实落盘）
```

模块与路由的**实际数量、清单、权限**见 [项目说明](../docs/03-开发/项目说明.md) 第三 / 五 / 七章。

## 3. 写代码前要知道的约定

这些约定都有对应的**守卫测试或静态检查**，破坏了不会报错但会被 CI 打回：

| 约定 | 为什么 | 被什么钉住 |
|------|--------|-----------|
| **路由层不写 ORM 查询**，一律下沉到 `services/` | 事务边界与权限逻辑要可复用、可测 | `test_*` 守卫（P1-41） |
| **时间统一 naive UTC**，禁止裸 `datetime.now()` | 跨模块时区口径曾分裂（容器 `TZ=UTC` 隐式成立，本地库 `-08:00` 就错） | 迁移评审 + `test_db_session_timezone`（P2-13 / P1-24） |
| **MySQL 会话时区在连接层锁 UTC**（`connect_args`） | 不依赖数据库服务器/容器时区 | AST 守卫：所有引擎创建必须带 `connect_args`（P2-13） |
| **管理端敏感操作必须落审计**，且与业务**同事务** | 回滚即不应留下审计行 | `test_admin_audit`（P2-9） |
| **出站抓取一律 `fetch_capped` 流式限流**，不得「先下载后校验」 | 超大响应打爆内存 | AST 守卫（P2-6 / P2-11） |
| **新增 AI 模型只在 `services/ai/registry.py` 登记一次** | 四层手工注册曾遗漏即静默换模型 | `test_provider_registry`（P1-40） |
| **管理端路由必须挂 `check_route_permission`** | router 级 `dependencies` **不跨 `include_router` 继承**，裸 `APIRouter()` = 任意 admin 可自我提权 | `test_admin_route_guards` / `test_rbac_guard_enforced`（P0-7） |
| **测试全程离线**：不连真实 MySQL / Redis | 否则「本地绿、CI 红」；用 `conftest.py` 的 `async_client` + `FakeRedis` + `fake_db` | CI 显式传 `DB_PORT=1 REDIS_PORT=1`（P0-2） |
| **新增环境变量要三处同步**：`config.py` Settings + `docker-compose.yml` + `.env.example` | 漏一处就是「生产无法配置」或「静默不生效」 | `scripts/check_env_consistency.py`（P1-11 / P1-12） |

## 4. 测试

```bash
# 全程离线（不需要 MySQL / Redis）
cd backend && DB_PORT=1 REDIS_PORT=1 python -m pytest tests/ -q

# 迁移正反向（在**真实 MySQL** 的临时库里跑，不需要 Docker；等价于 CI 的 migrations job）
python ../scripts/verify_migrations.py
```

⚠️ 运行前先看一眼 `backend/.env` 的 `DB_HOST/DB_PORT/DB_USER` —— 该脚本用 `pymysql` 直连
这里配置的实例，并 `DROP DATABASE IF EXISTS ai_cat_migtest` 后重建（临时库名可用 `TMP_DB` 改）。
指向线上或同事的库时会在那个实例上建/删库（第五轮审查 R5-D5：此前这里写成「需要 Docker」，
既误导环境准备，也没提示它会动真实例）。

单个文件的用例数与覆盖范围见 [项目说明](../docs/03-开发/项目说明.md) 9.1。
⚠️ `fake_db` 是内存会话仿真，保真度有已知失真（台账 P1-42）——用它跑出来的「通过」不等于真实数据库上的行为。

## 5. 常见坑

- **配置不在 `app/core/config.py`**，在 `app/config.py`；`app/core/` 只放安全、RBAC、限流、异常、监控。
- **生产弱凭据会 fail-fast**：`SECRET_KEY`（默认值 / 长度 <32）、`DB_PASSWORD`（空或 `root`）、`REDIS_PASSWORD`（空）任一不合格直接拒绝启动。新增生产必填项时要同步进这段校验，否则它会带着默认值静默上线。
- **定时任务不在 app 进程里**，是独立容器 `python -m scheduler.tasks`；改任务要同时确认 `docker-compose.yml` 的 `scheduler` 服务。
- **`alembic heads` 必须只有一个 head**；出现多主要先 `alembic merge`，否则 `upgrade head` 行为不确定（CI 会直接失败）。
- **本地库落后代码**会表现为「某几个页面 500」而 `/health` 仍然 ok；`/health/deep` 会校验「库版本 == 代码 head」，先跑 `alembic upgrade head`。
