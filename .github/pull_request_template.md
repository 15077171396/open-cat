## 这个 PR 做了什么

<!-- 一句话说清目的。diff 会说明改了什么，「为什么改」只有你能说清。 -->

## 关联 Issue

<!-- Closes #123 -->

## 改动类型

- [ ] 新功能
- [ ] Bug 修复
- [ ] 重构（不改变行为）
- [ ] 文档
- [ ] 工程 / CI

## 三条硬约定自检

- [ ] **写操作已写审计**（`record_admin_action`，与业务同事务提交）
- [ ] **新增管理端接口已在路由规则表登记**（未登记 = 403）
- [ ] **新增权限码已同步菜单项**（权限码 / 菜单 / 路由 / 守卫四处一致）

## 测试

- [ ] `cd backend && DB_PORT=1 REDIS_PORT=1 python -m pytest tests/ -q` 通过
- [ ] `ruff check backend` 通过
- [ ] `cd admin-frontend && npm run lint && npm run build && npm test` 通过
- [ ] 新增 / 修改的行为有测试覆盖

<!-- 如果某条没跑，说明原因（例如纯文档改动）。 -->

## 数据库变更

- [ ] 无
- [ ] 有，迁移文件：`backend/alembic/versions/____.py`
  - [ ] `alembic upgrade head` 与 `alembic downgrade -1` 双向验证过
  - [ ] 迁移链仍是单头

## 界面改动

<!-- 有 UI 改动请贴截图（改前 / 改后）。没有就删掉本节。 -->

| 改前 | 改后 |
|---|---|
|  |  |

## 破坏性变更

<!-- 有就说明影响范围与升级步骤；没有写「无」。 -->

无

## 备注

<!-- 需要 reviewer 特别注意的地方，例如刻意的取舍、暂时不处理的边界。 -->
