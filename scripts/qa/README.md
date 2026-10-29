# 接口黑盒探针（QA）

2026-10-29 全链路测试用的一次性探针，保留下来便于回归。**只读 + 少量可清理写入**，不要指向生产。

## 用法

```bash
cd backend
./venv/Scripts/python.exe ../scripts/qa/qa_probe.py    # 公共接口 / 鉴权 / 注入 / SSRF / CORS / 性能
./venv/Scripts/python.exe ../scripts/qa/qa_probe2.py   # 限流 / 上传 / 后台 CRUD / 审计
./venv/Scripts/python.exe ../scripts/qa/qa_probe3.py   # 评论 XSS / 支付回调 / 越权细节
./venv/Scripts/python.exe ../scripts/qa/qa_probe4.py   # 付费墙 / 能力降级 / 并发
./venv/Scripts/python.exe ../scripts/qa/qa_probe5.py   # 用户 + 后台写路径（收藏处会中断，见 qa_probe6）
./venv/Scripts/python.exe ../scripts/qa/qa_probe6.py   # 用户写路径全量（带容错）
./venv/Scripts/python.exe ../scripts/qa/qa_contract_audit.py   # 响应模型 vs 真实响应（缺字段 / 类型漂移）
```

结果 JSON 落到 `logs/qa/`。

## qa_contract_audit 说明

纯只读（全部 GET，带超管令牌），把 56 个 GET 端点的**真实响应**和 **OpenAPI 声明的 schema**
逐字段比对，抓两类"单测看不见"的问题：

- **声明了但从不返回的字段** → 每个响应恒挂几个 `null`（`_Item` 透传模型最容易出这种）；
- **类型漂移** → 声明 `int` 而库里是 `str`，Pydantic 静默强转（`"11"` → `11`），
  前端 `String(targetId) === target_id` 恒假，「已举报过」的入口抑制静默失效。

2026-10-29 首次运行即暴露了 `ReportItem` 的这两个问题（已修）。改了响应模型后建议跑一次：
`--strict` 让类型漂移也计入失败退出码。

## 前提

- 后端跑在 `127.0.0.1:8000`，本机有 `HTTP(S)_PROXY`，所以所有脚本都 `trust_env=False`。
- 令牌用 `app.core.security.create_access_token` 直接签发（admin=4 / user=12），不依赖登录口令。
- 同一 IP 游客限流 120 次/分钟：跑完一轮探针后需等 60 秒窗口重置，否则后续全是 429。
- `qa_probe5.py` 在「收藏教程」处会中断（该接口当前 500），用 `qa_probe6.py` 替代。

## 已知会写入的数据

公告（用后删除）、评论（用后删除）、反馈（用后删除）、收藏（用后删除）、举报（无删除接口，需在后台审核台处理）。
