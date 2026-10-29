# `scripts/` 工具脚本索引

> 这里收录 19 个脚本。此前有 6 个在全仓文档里**零出现**（第五轮全量审查 P2-57 之八）——
> 不是它们没用，而是没人知道它们存在，于是每次踩坑都靠口口相传。
> 本文件只回答「这个脚本干什么、怎么跑、跑坏了他会怎样」，
> 部署主流程仍以 [部署说明书](../docs/04-运维/部署说明书.md) 为准。

⚠️ **共同前提**：脚本都按 Windows + Git Bash 的混合环境写（`.bat`/`.ps1` 给本机，
`.sh` 给服务器）。**任何脚本都不含生产凭据**：服务器地址走环境变量或 gitignore 的
本地覆盖文件（P1-9 的整改结论：不要把生产 IP 与 root 用户名写进仓库）。

## 一、四个 `check_*` 是门禁，不是可选工具

| 脚本 | 查什么 | 谁跑 |
|------|--------|------|
| `check_docs_links.py` | `docs/` 内所有文件链接、文内锚点、中文序号是否重复 | CI 第一个 job + 提交前自检 |
| `check_env_consistency.py` | compose 引用 ↔ 注入容器 ↔ 两份 `.env.example` ↔ `Settings` 字段五侧一致 | 同上 |
| `check_ci_config.py` | CI 的 Node/Python 版本 pin ↔ `Dockerfile` 生产镜像一致 | 同上 |
| `check_tabbar.js` | 小程序 `app.json` 的 tabBar 五项 ↔ 自定义 tab-bar 常量表 | CI 第三个 job |

四个都是**非零退出即失败**，且都刻意做到「校验通过时输出干净」——
早先它们在 Windows GBK 控制台会打印 traceback  yet 退出码 1，把成功伪装成失败。

## 二、本机开发

| 脚本 | 用途 | 注意 |
|------|------|------|
| `start-backend.bat` | 常驻启动后端（`uvicorn`，不随窗口关闭而停） | 端口 8000 与本机其它项目冲突（见根 README），脚本会先探测占用 |
| `start-backend-dev.bat` | **开发热重载**：`uvicorn --reload`（只监听 `app/` 与 `scheduler/`），启动前先释放 8000 → **再双击一次 = 重启** | 改完后端代码**自动生效**（不用手动重启，也就不会再出现「接口还是 404」）。⚠️ 真机联调请用上面那个：reload 会在保存文件时中断在飞请求。依赖 `watchfiles`（已装入 venv） |
| `start-miniapp-api.bat` | 小程序联调专用：后端只监听 `127.0.0.1:8000` | 与 `start-backend.bat` 二选一，同时跑会抢端口 |
| `start-scheduler.bat` | **本机跑定时任务**：`python -m scheduler.tasks`（生产是 compose 的 scheduler 容器） | 不开也能用后台「系统 → 定时任务」页（看状态 / 启停 / 改周期），但「立即执行」会 503、页面提示「调度器未运行」—— 那是准确的。⚠️ 它会真的执行清理/降级/推送任务，**本机开发库同样会被改动** |
| `start-all-dev.bat` | **三端一键启动**：后端（复用 `start-backend-dev.bat`）+ 用户端 5173 + 管理端 5174，各开一个独立窗口 | 关窗口 = 停服务，**不受父进程被回收的连坐**。前端带 `--strictPort`：端口被占直接报错，不会悄悄换端口。前提：MySQL 3306 在跑 |
| `fix-weapp-proxy.bat` | 修微信开发者工具「预览 / 真机调试」访问不到本机后端 | 改的是开发者工具的代理设置，跑完需重启工具 |
| `setup-device-debug.bat` | 准备小程序真机调试环境 | 需要本机已装微信开发者工具与 ADB |
| `check-admin-dev-routes.mjs` | **校验管理端 dev server 的编译结果是否与 `App.tsx` 一致**：源码加了 `<Route>` 但编译结果没有时，点菜单会静默跳回数据看板（或整页白屏），而 `tsc`/`vitest`/`vite build` 全绿 | `node scripts/check-admin-dev-routes.mjs`；退出码 1 = dev server 陈旧，需 `taskkill /T` 重启 vite（Vite 是父子进程，只杀子进程会残留）。dev server 没起时跳过（exit 0） |

## 三、隧道与代理（服务器侧要访问 GitHub 时才需要）

| 脚本 | 用途 | 首次使用 |
|------|------|---------|
| `tunnel.ps1` | SSH **反向**隧道守护：服务器 `172.17.0.1:7897` → 本机 Clash，供后端 `GITHUB_PROXY` 拉 GitHub README；断线 5 秒自动重连 | 先设 `$env:AICAT_TUNNEL_SERVER = "user@host"`，或新建 `scripts/tunnel.local.ps1`（已在 `.gitignore`）写 `$Server = "..."`；两者都没有时脚本直接报错退出，不会静默连到别处 |
| `start-tunnel.bat` | 前台跑一次 `tunnel.ps1`（关窗口即停） | 临时用 |
| `install-tunnel-task.ps1` | 注册「当前用户登录时自动启动隧道」的计划任务 `SSH-Tunnel-ai-cat` | 卸载：`Unregister-ScheduledTask -TaskName "SSH-Tunnel-ai-cat" -Confirm:$false` |

⚠️ 隧道只在**开发机开机且在线**时有效；服务器上的第三方抓取任务若依赖它，
断链时后端会走降级路径（`fetch_capped` 的错误映射），不会 500，但内容会拉不到新的 README。

## 四、按需/一次性

| 脚本 | 用途 | 风险 |
|------|------|------|
| `gen-tdesign-ignore.js` | 扫描全项目 `usingComponents`，生成 TDesign 按需打包的 `packOptions.ignore` 清单（主包 ≤ 2MB，微信错误码 80051） | 会改写 `miniprogram/project.*` 的 ignore 配置；新增页面后需重跑，否则新组件被当未使用而打进 ignore |
| `backup_mysql.sh` | `mysqldump` 备份 + 保留期清理，被 `deploy.sh` 的发版前强制备份调用 | 依赖 `.env` 的 DB 凭据；失败即让发版中止（可用 `DEPLOY_SKIP_BACKUP=1` 显式跳过） |
| `watchdog.sh` | 定时探活 `/health` 与 `/health/deep`，异常走 `WATCHDOG_WEBHOOK` 告警 | 自己读 `.env` 里 `WATCHDOG_*`（不 source 整份，避免展开含 `$` 的密钥）；`--doctor` 回答「配了吗 / 配在哪 / 装了吗」 |
| `cleanup_orphans.py` | 生成孤儿数据清理 SQL（收藏 / 学习记录快照表无外键，P1-7 的历史遗留） | **只生成 SQL 不执行**，需要人工审过再跑 |

## 五、迁移校验

`verify_migrations.py` —— 对**真实 MySQL**（`.env` 里的 `DB_HOST`/`DB_PORT`）
执行 `upgrade head → downgrade base → upgrade head` 与单 head 校验，等价于 CI 的
migrations job。⚠️ 它会在同一个实例上**建库/删库**（`ai_cat_migrate_check*`），
所以跑之前必须核对连的是不是本机 dev 实例；这也是它属于「人工动作」而不是随手
`pytest` 一部分的原因。

## 六、打包部署包

`pack-deploy.sh` —— **生成部署包的唯一入口**（2026-10-21 起）。

```bash
bash scripts/pack-deploy.sh                # → ./ai-cat-deploy.tar.gz（约 2.8MB / 579 条目）
bash scripts/pack-deploy.sh /tmp/x.tar.gz  # 指定输出路径
```

**为什么不要手敲 `tar`**：手工打包出过两次事故 ——

1. 把工作区脏数据带走：`backend/logs` 有 11MB，一打就是 25MB / 1584 个文件；
2. Windows 上 `core.autocrlf=true` 让工作区的 `.sh` 变成 CRLF，服务器执行
   `bash deploy.sh` 直接报 `$'\r': command not found` / `invalid option nameet: pipefail`。

脚本的做法：**`git archive HEAD`** 导出已提交内容（blob 一律 LF），白名单只放
「构建镜像 + 跑 compose」需要的东西，并排除 8 张未被引用的设计源图（≈4.8MB）、
`backend/tests`（≈970KB）、`backend/pytest.ini`。

自带三项自检，任一不过即退出非 0：无 `node_modules`/`venv`/`.git`/`__pycache__`/
`dist`/`logs`/真实 `.env`/`*.pyc`；部署必需文件齐全；所有 `.sh`/`Dockerfile*`/
`*.conf`/`*.yml` 为 LF。

⚠️ 工作区有未提交改动时会打印警告 —— 包只含 HEAD 的内容，别拿半成品上服务器。
⚠️ 判断 CRLF **不要用** `grep -q $'\r'`（Git Bash 会吃掉参数里的 CR，把 CRLF 误判成 LF），
用 `tr -cd '\r' < 文件 | wc -c`。
