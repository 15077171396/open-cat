"""接口契约审计：响应模型 vs 真实响应（字段缺失 / 类型漂移）。

为什么需要它（2026-10-29 的真实教训）：
- `ReportItem` 声明了 `user_id` / `target_type` / `description`，service 从不产出
  → 每条举报都带 3 个恒为 null 的字段（声明不存在的字段 = 凭空造 null）；
- 同一个模型把 `target_id` 声明成 `int`，而库里是 `String(64)`
  → Pydantic 把 "11" 静默转成 11，前端 `String(targetId) === target_id` 恒假，
    「已举报过」的入口抑制静默失效（功能没报错，但怎么点都在）。

这两类问题单测都看不出来（fake_db 返回 dict、Pydantic 只校验声明过的字段），
只能拿"真实响应"跟"声明的 schema"逐字段比。本脚本就是干这个的。

用法（后端必须已在 127.0.0.1:8000 运行）：

    cd backend && ./venv/Scripts/python.exe ../scripts/qa/qa_contract_audit.py

只读：全部是 GET，不发任何写请求。带 `--strict` 时类型漂移也算失败（退出码 1）。
"""

from __future__ import annotations

import argparse
import collections
import sys
import time

import httpx

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402

BASE = "http://127.0.0.1:8000"
# 用超管令牌：只读 GET 全都能过，避免大量 403 把审计面缩小
HEADERS = {"Authorization": "Bearer " + create_access_token({"sub": "4", "role": "admin"})}

TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def deref(node: dict, schemas: dict, depth: int = 0) -> dict:
    """展开 $ref / anyOf / oneOf / allOf，返回 {字段名: 子 schema}"""
    if not isinstance(node, dict) or depth > 8:
        return {}
    if "$ref" in node:
        return deref(schemas.get(node["$ref"].split("/")[-1], {}), schemas, depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in node:
            merged: dict = {}
            for sub in node[key]:
                if isinstance(sub, dict) and sub.get("type") == "null":
                    continue
                merged.update(deref(sub, schemas, depth + 1))
            return merged
    if "properties" in node:
        return node["properties"]
    if node.get("type") == "array":
        return deref(node.get("items", {}), schemas, depth + 1)
    return {}


def declared_types(schema: dict) -> set[str]:
    """声明允许的 JSON 类型集合（anyOf 里的 null 不算）。

    ⚠️ 空 schema `{}` 表示「不约束类型」（Pydantic 的 `Optional[Any]` 就长这样：
    `{"anyOf": [{}, {"type": "null"}]}`）。遇到它必须返回空集 = 跳过校验，
    否则会把「任意类型」误判成「只允许 null」。
    """
    if not isinstance(schema, dict):
        return set()
    if "type" in schema and isinstance(schema["type"], str):
        return {schema["type"]}
    types: set[str] = set()
    for key in ("anyOf", "oneOf"):
        for sub in schema.get(key, []):
            if not sub:  # 空 schema = 任意类型
                return set()
            types |= declared_types(sub)
    return types


def json_type(value) -> str:
    # bool 必须先判：Python 里 bool 是 int 的子类
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "null"


def audit_row(where: str, props: dict, row: dict, problems: list[str]) -> None:
    for name, schema in props.items():
        if name not in row:
            problems.append(f"{where}: 声明了 {name} 但响应里没有（会被填成 null）")
            continue
        value = row[name]
        if value is None:
            continue  # 可空字段，运行时确实可以为 null
        allowed = declared_types(schema)
        if not allowed:
            continue
        actual = json_type(value)
        # integer 声明接受 number（JSON 里 1 与 1.0 同源）
        if actual == "number" and "integer" in allowed:
            continue
        if actual not in allowed:
            problems.append(
                f"{where}.{name}: 声明 {sorted(allowed)} 但实际是 {actual}（{value!r:.40}）"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="类型漂移也算失败")
    args = parser.parse_args()

    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    client = httpx.Client(base_url=BASE, timeout=20, trust_env=False)

    # 游客/用户限流按分钟固定窗口，连跑多个脚本会整轮 429
    for _ in range(70):
        if client.get("/api/v1/health").status_code == 200:
            break
        time.sleep(1)

    status = collections.Counter()
    problems: list[str] = []
    checked = 0

    for path, methods in spec["paths"].items():
        if "get" not in methods or "{" in path:
            continue
        schema = (
            methods["get"].get("responses", {}).get("200", {})
            .get("content", {}).get("application/json", {}).get("schema")
        )
        if not schema:
            continue
        data_props = deref(deref(schema, schemas).get("data", {}), schemas)
        if not data_props:
            continue

        resp = client.get(path, headers=HEADERS)
        status[resp.status_code] += 1
        if resp.status_code != 200:
            continue
        checked += 1
        data = resp.json().get("data")

        if isinstance(data, dict):
            audit_row(f"GET {path} data", data_props, data, problems)
            items = data.get("items")
            item_props = deref(data_props.get("items", {}), schemas)
            if isinstance(items, list) and items and isinstance(items[0], dict) and item_props:
                audit_row(f"GET {path} items[0]", item_props, items[0], problems)
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            item_props = deref(data_props, schemas)
            audit_row(f"GET {path} [0]", item_props, data[0], problems)

    print(f"探测 {sum(status.values())} 个端点，状态分布 {dict(status)}，契约比对 {checked} 个")
    if problems:
        print(f"\n❌ 发现 {len(problems)} 处契约偏差：")
        for item in problems:
            print("  ✗ " + item)
        return 1
    print("\n✅ 响应模型与真实响应一致（无缺字段、无类型漂移）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
