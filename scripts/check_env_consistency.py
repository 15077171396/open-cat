#!/usr/bin/env python3
"""环境变量一致性检查（零依赖）：校验 compose、.env.example 与 config.py Settings 三者一致。

用法：python scripts/check_env_consistency.py [--quiet]
退出码：0 = 无错误；1 = 存在错误项
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

COMPOSE = REPO_ROOT / "docker-compose.yml"
ROOT_ENV = REPO_ROOT / ".env.example"
BACKEND_ENV = REPO_ROOT / "backend" / ".env.example"
CONFIG_PY = REPO_ROOT / "backend" / "app" / "config.py"

# compose 里的 ${VAR} / ${VAR:-default} / ${VAR:?msg}
COMPOSE_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::[-?][^}]*)?\}")
# .env 模板里的 KEY=
ENV_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=", re.MULTILINE)
# config.py 中 Settings 类的字段：4 空格缩进 + 大写名 + 类型注解
SETTINGS_FIELD_RE = re.compile(r"^    ([A-Z][A-Z0-9_]*)\s*:\s*", re.MULTILINE)
# compose 服务名（services: 下 2 空格缩进）
SERVICE_RE = re.compile(r"^  ([a-z][a-z0-9_-]*):\s*$")
# 服务 environment: 块内的条目：列表写法 `- NAME=...` 与映射写法 `NAME: ...` 都要认。
# 2026-10-30 起编排改用映射写法（`DB_HOST: mysql`），只认列表写法会漏掉全部基础设施变量。
SERVICE_ENV_ITEM_RE = re.compile(r"^      (?:- )?([A-Za-z_][A-Za-z0-9_]*)(?:=|:)")

# 检查「注入的容器」：只关心跑应用的容器，migrate/scheduler 各按需传
APP_SERVICES = ("app", "scheduler")

# 定时任务入口及其直接调用的服务模块：其 settings.X 必须在 scheduler 容器内可解析（P2-24）
SCHEDULER_DIR = REPO_ROOT / "backend" / "scheduler"
SCHEDULER_SERVICE_MODULES = (
    REPO_ROOT / "backend" / "app" / "services" / "baidu_push_service.py",
    REPO_ROOT / "backend" / "app" / "services" / "indexnow_service.py",
)
# 代码里 settings.NAME 的用法
SETTINGS_USAGE_RE = re.compile(r"\bsettings\.([A-Z][A-Z0-9_]*)")

# 仅宿主机脚本使用、不需进容器的变量：应出现在根 .env.example 但 compose 不引用，
# 在此登记以免被第 2 项检查误报为「填了没用」。
ALLOW_HOST_ONLY: set[str] = {
    # watchdog.sh（宿主机 cron）自读 .env，无需注入容器
    "WATCHDOG_WEBHOOK",
    "WATCHDOG_HEALTH_URL",
    "WATCHDOG_HEALTH_HOST",
    "WATCHDOG_DISK_PERCENT",
    "WATCHDOG_BACKUP_MAX_AGE_HOURS",
    "WATCHDOG_CRON",
    "WATCHDOG_TIMEOUT",
    "WATCHDOG_ALERT_SUPPRESS_SECONDS",
    "WATCHDOG_LOG_MAX_BYTES",
    # backup_mysql.sh（宿主机 cron）自读 .env 的离机上传钩子，与容器无关
    "BACKUP_AFTER_HOOK",
}

# 编排层与构建期专用、后端 Settings 不认识的键：
# 它们由 compose / Dockerfile 消费，出现在 .env 里是**正确**的，不能按「拼错」报错。
ALLOW_COMPOSE_ONLY: set[str] = {
    "MYSQL_DATABASE",
    "MYSQL_ROOT_PASSWORD",
    "ADMIN_PASSWORD",
    "NGINX_PORT",
    # Vite 构建期变量：由 Dockerfile.nginx 的 ARG→ENV 消费，不进后端
    "VITE_SENTRY_DSN",
}

# 刻意不经 compose 暴露的内部调优项（默认值即产品决策）；新增字段若不注入也不在此列表会被第 3 项报出
ALLOW_DEFAULT_ONLY: dict[str, str] = {
    # JWT 参数是**内部契约**：改算法/有效期与已签发的令牌不兼容，
    # 不适合作为部署期可调项暴露（要改就改代码并让所有人重新登录）
    "JWT_ALGORITHM": "内部契约，改动与已签发令牌不兼容",
    "JWT_ACCESS_EXPIRE_MINUTES": "同上",
    "JWT_REFRESH_EXPIRE_DAYS": "同上",
    "WATCHDOG_WEBHOOK": "后端只读取它判断「告警通道是否配置」，不负责发送告警",
    # 存储目录由 compose 写死为挂卷路径 /app/uploads（写进 .env 只会让文件落到容器内
    # 非挂载路径 → 容器重建即丢）；对外前缀默认由 SITE_BASE_URL 派生，无需单独配置。
    "STORAGE_LOCAL_DIR": "compose 已固定为卷挂载点 /app/uploads，允许覆盖只会丢文件",
    "STORAGE_PUBLIC_BASE": "默认由 SITE_BASE_URL 派生，特殊域名再在 .env 里加即可",
    "AVATAR_MAX_SIZE": "与前端提示文案耦合，改代码才安全",
    "AVATAR_ALLOWED_TYPES": "同上",
    "COS_MAX_FILE_SIZE": "与前端提示文案耦合（超出即提示用网盘）",
    "COS_IMAGE_MAX_SIZE": "同上",
    "COS_IMAGE_WEBP_THRESHOLD": "图片转码调优，默认值即产品决策",
    "COS_IMAGE_WEBP_QUALITY": "同上",
    "AI_TIMEOUT_SECONDS": "AI 调用重试策略，内部实现细节",
    "AI_RETRY_COUNT": "同上",
    "AI_TOTAL_BUDGET_SECONDS": "同上（AI 调用含重试的总耗时上限，防慢请求占死 worker）",
    "THIRD_PARTY_FETCH_BUDGET_SECONDS": "抓取含多源/图片转写的总耗时上限，内部实现细节",
    "SEARCH_FALLBACK_COUNT_TIMEOUT_MS": "LIKE 兜底计数的语句超时（防慢查询占死连接池），内部调优项",
    "HEALTH_DEEP_CACHE_SECONDS": "深度探活缓存，默认 5 秒已足够",
    "REDIS_DB": "缓存/限流/验证码与 prerender 缓存共用同一 DB，改动会让两边错位",
    "BACKUP_DIR": (
        "容器内必须是 volumes 的挂载点 /app/backups，compose 已写死；"
        "允许 .env 覆盖只会让该项显示「不可见」（宿主机路径在容器里不存在）"
    ),
}


def read_compose_vars() -> set[str]:
    return set(COMPOSE_VAR_RE.findall(COMPOSE.read_text(encoding="utf-8")))


def read_service_env_names() -> dict[str, set[str]]:
    """解析各服务 environment 块，返回 {服务名: {环境变量名}}；仅缩进扫描，不引入 PyYAML（CI 最小环境运行）。"""
    services: dict[str, set[str]] = {}
    current: str | None = None
    in_environment = False

    for line in COMPOSE.read_text(encoding="utf-8").splitlines():
        service_match = SERVICE_RE.match(line)
        if service_match:
            current = service_match.group(1)
            services.setdefault(current, set())
            in_environment = False
            continue
        if current is None:
            continue
        if re.match(r"^    environment:\s*$", line):
            in_environment = True
            continue
        # 回到 4 空格缩进的其它键，退出 environment 块
        if in_environment and re.match(r"^    [a-z]", line):
            in_environment = False
            continue
        if in_environment:
            item = SERVICE_ENV_ITEM_RE.match(line)
            if item:
                services[current].add(item.group(1))
    return services


def read_env_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(ENV_KEY_RE.findall(path.read_text(encoding="utf-8")))


def read_service_env_files() -> dict[str, bool]:
    """哪些服务用 `env_file: - .env` 全量注入。

    2026-10-30 开源化改造：编排从「逐个 `- KEY=${KEY:-}` 列出」改为 `env_file: .env`
    + 少量 environment 覆盖。若不识别这一步，第 2/3 项会把「模板里声明了但
    compose 没逐个引用」全部误报为错误。
    """
    result: dict[str, bool] = {}
    current: str | None = None
    in_block = False
    for line in COMPOSE.read_text(encoding="utf-8").splitlines():
        service_match = SERVICE_RE.match(line)
        if service_match:
            current = service_match.group(1)
            in_block = False
            continue
        if current is None:
            continue
        if re.match(r"^    env_file:\s*$", line):
            in_block = True
            result.setdefault(current, False)
            continue
        if in_block:
            if re.match(r"^      - ", line):
                if ".env" in line:
                    result[current] = True
                continue
            in_block = False
    return result


def read_settings_fields() -> set[str]:
    text = CONFIG_PY.read_text(encoding="utf-8")
    start = text.find("class Settings(BaseSettings):")
    if start == -1:
        return set()
    end = text.find("\nsettings = Settings()", start)
    body = text[start : end if end != -1 else len(text)]
    return set(SETTINGS_FIELD_RE.findall(body))


def read_settings_usage() -> set[str]:
    """扫描定时任务代码（含其直接调用的推送服务）里用到的 Settings 名"""
    used: set[str] = set()
    files = list(SCHEDULER_DIR.rglob("*.py")) if SCHEDULER_DIR.exists() else []
    files += [f for f in SCHEDULER_SERVICE_MODULES if f.exists()]
    for path in files:
        used |= set(SETTINGS_USAGE_RE.findall(path.read_text(encoding="utf-8")))
    return used


def main() -> int:
    # Windows 控制台默认 GBK，输出 ✅ 会抛 UnicodeEncodeError，故强制 UTF-8 输出。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    for path in (COMPOSE, ROOT_ENV, CONFIG_PY):
        if not path.exists():
            print(f"缺少必需文件: {path}", file=sys.stderr)
            return 1

    compose_vars = read_compose_vars()
    service_env = read_service_env_names()
    service_env_files = read_service_env_files()
    root_env = read_env_keys(ROOT_ENV)
    backend_env = read_env_keys(BACKEND_ENV)
    settings_fields = read_settings_fields()

    injected: set[str] = set()
    for name in APP_SERVICES:
        injected |= service_env.get(name, set())
        # env_file 全量注入：模板里声明的键都会进容器
        if service_env_files.get(name):
            injected |= root_env
    # 第 3 项只判 app，不取并集（P2-24）：并集会让「只注入 scheduler」的字段被误判通过。
    app_injected: set[str] = set(service_env.get("app", set()))
    if service_env_files.get("app"):
        app_injected |= root_env

    errors: list[str] = []
    warnings: list[str] = []

    # 1. compose 引用但模板未声明
    for name in sorted(compose_vars - root_env):
        errors.append(
            f"docker-compose.yml 引用了 ${{{name}}}，但根 .env.example 未声明"
            "（部署时为空值 → 功能静默失效）"
        )

    # 2. 模板声明但不是 Settings 字段（拼错或已废弃的键：用户填了不会有任何效果）
    #    说明：改用 env_file 后，「声明即注入」，原先「声明但未在 compose 引用」的判定
    #    已不适用；这里改为校验「声明的键必须真的被后端认识」。
    for name in sorted(root_env - set(settings_fields) - ALLOW_HOST_ONLY - ALLOW_COMPOSE_ONLY):
        errors.append(
            f"根 .env.example 声明了 {name}，但 config.py 的 Settings 里没有该字段"
            "（拼写错误或已废弃，填了不起作用）"
        )

    # 3. Settings 字段在生产环境无法注入（只判 app，不取并集）
    for name in sorted(settings_fields - app_injected - set(ALLOW_DEFAULT_ONLY)):
        errors.append(
            f"Settings.{name} 既未被 app 服务注入，也不在 ALLOW_DEFAULT_ONLY 中"
            " → 生产环境无法配置该值"
        )

    # 3b. 逐服务判定（P2-24）：scheduler 读取的 Settings 必须注入 scheduler，并集判定会漏掉单服务漏注入。
    scheduler_injected = set(service_env.get("scheduler", set()))
    if service_env_files.get("scheduler"):
        scheduler_injected |= root_env
    for name in sorted(read_settings_usage() - scheduler_injected - set(ALLOW_DEFAULT_ONLY)):
        errors.append(
            f"Settings.{name} 被 scheduler 代码读取，但 compose 的 scheduler 服务未注入"
            " → 定时任务在生产环境读到默认值/空值（表现为静默跳过）"
        )

    # 4. Settings 字段未出现在任何模板
    for name in sorted(settings_fields - root_env - backend_env - set(ALLOW_DEFAULT_ONLY)):
        warnings.append(f"Settings.{name} 未出现在任何 .env.example 中（用户无从得知可配置）")

    if not args.quiet:
        print(
            f"compose 引用 {len(compose_vars)} 个变量 · 注入容器 {len(injected)} 个 · "
            f"根模板 {len(root_env)} 个 · backend 模板 {len(backend_env)} 个 · "
            f"Settings 字段 {len(settings_fields)} 个"
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

    print("\n✅ 环境变量一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
