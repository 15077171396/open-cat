#!/usr/bin/env python3
"""CI 配置自检（零依赖）：断言 ci.yml 的版本 pin、脚本/服务/npm script 引用与仓库实际配置一致。

用法：python scripts/check_ci_config.py [--quiet]
退出码：0 = 无错误；1 = 存在错误项
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
BACKEND_DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
NGINX_DOCKERFILE = REPO_ROOT / "Dockerfile.nginx"
# 开源版不再包含 prerender / frontend（见 README 的范围说明）
COMPOSE = REPO_ROOT / "docker-compose.yml"
GITIGNORE = REPO_ROOT / ".gitignore"

# workflow 顶部 env 块：2 空格缩进 + 大写下划线名
ENV_PIN_RE = re.compile(
    r"^  (PYTHON_VERSION|NODE_VERSION):\s*[\"']?([0-9][0-9.]*)[\"']?\s*$", re.MULTILINE
)
# Dockerfile: FROM python:3.11-slim
PY_FROM_RE = re.compile(r"^FROM\s+python:([0-9][0-9.]*)", re.MULTILINE)
# Dockerfile.nginx: FROM node:22-alpine AS frontend-builder
NODE_FROM_RE = re.compile(r"^FROM\s+node:([0-9][0-9.]*)", re.MULTILINE)
# workflow: working-directory: ${{ matrix.app }}  → 只取字面量，矩阵变量跳过
WORKDIR_RE = re.compile(r"working-directory:\s*(?!\$\{\{)([^\s#]+)")
# workflow: run: python scripts/xxx.py
PY_SCRIPT_RE = re.compile(r"python\s+(scripts/[A-Za-z0-9_./-]+\.py)")
# workflow: run: node prerender/scripts/xxx.js（脚本必须真实存在，否则 CI 在跑测试前就失败）
NODE_SCRIPT_RE = re.compile(
    r"node\s+((?:scripts|prerender/scripts)/[A-Za-z0-9_./-]+\.(?:js|mjs))"
)
# workflow: npm run <script>  （排除 npm ci / npm install）
NPM_RUN_RE = re.compile(r"npm run ([A-Za-z0-9_:-]+)")
# workflow: docker compose build <service>
COMPOSE_BUILD_RE = re.compile(r"docker\s+compose\s+build\s+([a-z][a-z0-9_-]*)")
# compose 服务名（仅 services: 段内 2 空格缩进，避免把 volumes/networks 条目算进去）
COMPOSE_SERVICE_RE = re.compile(r"^  ([a-z][a-z0-9_-]*):\s*$", re.MULTILINE)


def major_minor(version: str) -> str:
    """'3.11.9' → '3.11'；用于「3.11 与 3.11-slim 视为同版本」的比较。"""
    parts = version.split(".")
    return ".".join(parts[:2])


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def read_compose_services() -> set[str]:
    """取出 services: 块内的服务名（缩进扫描，不引入 PyYAML）。"""
    services: set[str] = set()
    inside = False
    for line in read(COMPOSE).splitlines():
        if re.match(r"^services:\s*$", line):
            inside = True
            continue
        if inside and re.match(r"^[a-zA-Z]", line):
            break  # 下一个顶格键（volumes: / networks: …）
        if inside:
            match = COMPOSE_SERVICE_RE.match(line)
            if match:
                services.add(match.group(1))
    return services


def main() -> int:
    # Windows 控制台默认 GBK，输出 ✅ 会抛 UnicodeEncodeError，故强制 UTF-8 输出。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if not WORKFLOW.exists():
        print(f"缺少 CI 配置: {WORKFLOW.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1

    workflow = read(WORKFLOW)
    errors: list[str] = []
    warnings: list[str] = []

    # 1 & 2. 版本与生产镜像对齐
    pins = dict(ENV_PIN_RE.findall(workflow))
    # 一个 pin 可能对应多个 Dockerfile（Node 被前端构建与 prerender 共用）
    pin_targets = (
        ("PYTHON_VERSION", [(PY_FROM_RE, BACKEND_DOCKERFILE, "backend/Dockerfile")]),
        ("NODE_VERSION", [(NODE_FROM_RE, NGINX_DOCKERFILE, "Dockerfile.nginx")]),
    )
    for pin_name, targets in pin_targets:
        pinned = pins.get(pin_name)
        if not pinned:
            errors.append(f"ci.yml 顶部 env 未声明 {pin_name}")
            continue
        for from_re, dockerfile, label in targets:
            match = from_re.search(read(dockerfile))
            if not match:
                errors.append(f"{label} 中未找到可解析的 FROM 基础镜像版本")
                continue
            image_version = match.group(1)
            if major_minor(pinned) != major_minor(image_version):
                errors.append(
                    f"{pin_name}={pinned} 与 {label} 的 FROM 版本 {image_version} 不一致"
                    "（CI 与生产版本不同 → CI 通过不代表能部署）"
                )

    # 3. working-directory 必须存在
    for rel in sorted(set(WORKDIR_RE.findall(workflow))):
        if not (REPO_ROOT / rel).is_dir():
            errors.append(f"ci.yml 的 working-directory: {rel} 目录不存在")

    # 4. 被调用的脚本必须存在
    for rel in sorted(set(PY_SCRIPT_RE.findall(workflow))):
        if not (REPO_ROOT / rel).is_file():
            errors.append(f"ci.yml 调用了 {rel}，但该文件不存在")

    for rel in sorted(set(NODE_SCRIPT_RE.findall(workflow))):
        if not (REPO_ROOT / rel).is_file():
            errors.append(f"ci.yml 调用了 node {rel}，但该文件不存在")

    # 5. npm script 必须存在
    npm_scripts: dict[str, set[str]] = {}
    for app in ("admin-frontend",):
        pkg_path = REPO_ROOT / app / "package.json"
        if not pkg_path.is_file():
            errors.append(f"缺少 {app}/package.json")
            continue
        try:
            npm_scripts[app] = set(json.loads(read(pkg_path)).get("scripts", {}))
        except json.JSONDecodeError as exc:
            errors.append(f"{app}/package.json 不是合法 JSON: {exc}")

    for name in sorted(set(NPM_RUN_RE.findall(workflow))):
        # npm run 都发生在 working-directory 指定的工程内，任一工程声明即可
        if not any(name in scripts for scripts in npm_scripts.values()):
            errors.append(
                f"ci.yml 执行了 npm run {name}，但 admin-frontend/package.json 里没有这个 script"
            )

    # 矩阵里列出的工程目录必须存在（矩阵是字面量列表）
    for app in sorted(set(re.findall(r"^\s+app:\s*\[([^\]]+)\]", workflow, re.MULTILINE))):
        for item in (part.strip() for part in app.split(",")):
            if item and not (REPO_ROOT / item).is_dir():
                errors.append(f"ci.yml 的 matrix.app 列出了 {item}，但该目录不存在")

    # 6. docker compose build 的服务必须存在
    compose_services = read_compose_services()
    for service in sorted(set(COMPOSE_BUILD_RE.findall(workflow))):
        if service not in compose_services:
            errors.append(
                f"ci.yml 执行 `docker compose build {service}`，"
                f"但 docker-compose.yml 中没有该服务"
            )

    # 7. lockfile 被忽略（提示）
    if re.search(r"^package-lock\.json\s*$", read(GITIGNORE), re.MULTILINE):
        warnings.append(
            ".gitignore 忽略了 package-lock.json → npm ci 永远失败并退回 npm install，"
            "依赖版本不可复现。建议去掉该行并提交两个 lockfile。"
        )

    if not args.quiet:
        print(
            f"CI 自检：{len(pins)} 个版本 pin · compose 服务 {len(compose_services)} 个 · "
            f"npm 工程 {len(npm_scripts)} 个"
        )

    if warnings:
        print(f"\n警告 {len(warnings)} 条：")
        for item in warnings:
            print("  ⚠ " + item)

    if errors:
        print(f"\n错误 {len(errors)} 条：")
        for item in errors:
            print("  ✗ " + item)
        return 1

    print("\n✅ CI 配置与生产镜像一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
