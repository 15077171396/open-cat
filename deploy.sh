#!/usr/bin/env bash
# AI 猫平台 - 一键部署脚本 (Linux)
# 用法: ./deploy.sh [up|down|restart|logs|rebuild|status]
#   up       首次/常规启动 (默认)
#   down     停止并移除容器
#   restart  重启所有服务
#   rebuild  重新构建镜像并启动 (代码变更后用)
#   logs     查看所有服务日志
#   status   查看服务状态

set -euo pipefail

cd "$(dirname "$0")"

ACTION="${1:-up}"
PLACEHOLDER_PATTERN='请替换为'

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[error]${NC} $*" >&2; }

# ---------- 前置检查 ----------
check_env_file() {
    if [[ ! -f .env ]]; then
        if [[ -f .env.example ]]; then
            cp .env.example .env
            # .env 里是 SECRET_KEY / DB 密码 / 第三方密钥，默认 0644 等于同机任意
            # 账号可读 → 拿到 HS256 签名密钥即可伪造 admin JWT（第四轮审查 P2-49④）
            chmod 600 .env
            err "已从 .env.example 创建 .env（权限 600）, 请先编辑 .env 填入真实密钥与密码后重新运行本脚本"
            err "  vi .env"
            exit 1
        else
            err "未找到 .env 且无 .env.example 模板"
            exit 1
        fi
    fi
}

# 逐行解析 .env 并导出（**不做任何 shell 展开**，第三轮增量审查 P2-26）。
#
# 原实现 `set -a; source <(grep -v '^#' .env)` 把 .env 当 shell 脚本执行，
# 而 compose 的 .env **不是** shell 格式：
#   · `.env.example` 要求填 `AI_MODEL_PRICES={"deepseek":{...}}` —— source 会
#     触发花括号展开，把值拆坏并把第二段当命令执行；
#   · 含 `$` / 反引号 / `;` / 空格的密码同样会炸，而且**校验的是一个被破坏的值**；
#   · 任一行格式不对，`set -e` 直接让脚本退出。
# 正确先例见 scripts/backup_mysql.sh 的 load_env（只取需要的键）。这里需要保留
# 「全部变量都可用」的行为（NGINX_PORT / WATCHDOG_WEBHOOK / APP_RELEASE 都有人读），
# 故做成通用解析器：只接受合法的 KEY=VALUE，去成对引号，其余原样保留。
load_env_file() {
    local line key value
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"                       # 容忍 CRLF
        [[ "$line" =~ ^[[:space:]]*$ ]] && continue
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" == *"="* ]] || continue
        key="${line%%=*}"
        value="${line#*=}"
        key="${key#"${key%%[![:space:]]*}"}"       # 去掉键左侧空白
        key="${key%"${key##*[![:space:]]}"}"       # 去掉键右侧空白
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        if [[ "$value" == \"*\" && ${#value} -ge 2 ]]; then
            value="${value:1:${#value}-2}"
        elif [[ "$value" == \'*\' && ${#value} -ge 2 ]]; then
            value="${value:1:${#value}-2}"
        fi
        export "$key=$value"
    done < .env
}

check_required_vars() {
    local missing=0
    # 读取 .env（安全解析，见 load_env_file 注释）
    load_env_file

    for var in SECRET_KEY MYSQL_ROOT_PASSWORD REDIS_PASSWORD; do
        val="${!var:-}"
        if [[ -z "$val" || "$val" == *"$PLACEHOLDER_PATTERN"* ]]; then
            err ".env 中 $var 未设置或仍为占位符"
            missing=1
        fi
    done

    if [[ $missing -eq 1 ]]; then
        err "请编辑 .env 填入真实值后重试: vi .env"
        exit 1
    fi
}

# ---------- 告警通道检查（只警告，不阻断） ----------
# 「没配 webhook」与「没装 cron」都会让告警**静默失效** —— 而静默的告警通道比
# 没有告警更危险（让人以为有人在看着）。部署正是最容易想起来补配置的时刻，
# 所以在这里提醒一次。刻意只警告不报错：可观测性增强不该拦住一次正常的发版。
#
# ⚠️ 必须在 check_required_vars **之后**调用 —— 那个函数才会把 .env 载入环境。
check_alerting() {
    local cron_marker="# ai-cat-watchdog" cron_state
    if ! command -v crontab >/dev/null 2>&1; then
        cron_state="不可用（未装 crontab）"
    elif crontab -l 2>/dev/null | grep -q "${cron_marker}"; then
        cron_state="已安装"
    else
        cron_state="未安装"
    fi

    if [[ -z "${WATCHDOG_WEBHOOK:-}" ]]; then
        warn "告警通道未接通：.env 的 WATCHDOG_WEBHOOK 为空 → 线上故障只会写本地日志，不会通知任何人"
        warn "  开通：在 .env 填 WATCHDOG_WEBHOOK='<企业微信机器人地址>'，再执行 ./scripts/watchdog.sh --install-cron"
    elif [[ "${cron_state}" != "已安装" ]]; then
        warn "WATCHDOG_WEBHOOK 已配置，但看门狗定时任务${cron_state} → 检查不会自动跑"
        warn "  安装：./scripts/watchdog.sh --install-cron"
    else
        log "告警通道已接通（webhook + cron）"
    fi

    [[ -n "${SENTRY_DSN:-}" ]] || warn "SENTRY_DSN 为空 → 后端未捕获异常不会上报（只写日志）"
    return 0
}

check_docker() {
    if ! command -v docker >/dev/null 2>&1; then        err "未检测到 docker, 请先安装 Docker 与 Docker Compose"
        err "  curl -fsSL https://get.docker.com | sh"
        exit 1
    fi
    if ! docker compose version >/dev/null 2>&1; then
        err "未检测到 docker compose 子命令 (需 Docker Compose v2+)"
        exit 1
    fi
}

# 日志目录准备（第三轮增量审查 P2-23）。
# compose 把 ./logs bind mount 到容器 /app/logs，而容器内以 appuser(uid 10001)
# 运行（backend/Dockerfile）—— 宿主机目录属主不对时 loguru 文件 sink 会 OSError，
# 应用**静默退化为仅控制台输出**（日志仍不落盘，问题看起来没修好）。
# 故在启动前把目录建好并（root 下）交给 10001。
ensure_log_dir() {
    mkdir -p logs
    if [[ "$(id -u)" == "0" ]]; then
        chown -R 10001:10001 logs 2>/dev/null \
            || warn "无法调整 ./logs 属主，容器内 appuser 可能无法写日志文件"
    elif [[ ! -w logs ]]; then
        warn "当前用户对 ./logs 无写权限且非 root —— 容器内 appuser(10001) 可能无法写日志"
    fi
}

print_access_info() {
    local port="${NGINX_PORT:-80}"
    local ip
    ip=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "<服务器IP>")
    echo
    echo -e "${CYAN}================ 部署完成 ================${NC}"
    echo -e "  用户前台:  http://${ip}${port:+:$port}/"
    echo -e "  管理后台:  http://${ip}${port:+:$port}/admin/"
    echo -e "  健康检查:  http://${ip}${port:+:$port}/healthz"
    echo -e "${CYAN}==========================================${NC}"
    echo
    echo -e "  首次部署后可导入种子数据 (管理员账号等):"
    echo -e "    docker compose run --rm migrate python seed_data.py"
    echo
    echo -e "  查看日志:   ./deploy.sh logs"
    echo -e "  查看状态:   ./deploy.sh status"
    echo
}

# ---------- 健康校验 ----------
# 轮询直到就绪或超时。**刻意替代原来的 `sleep 5`**：那种写法下，新镜像若因
# 配置错 / 迁移失败 / 端口被占用而起不来，脚本仍会打印「部署完成」——
# 线上其实一直是 502，却要等人自己发现。让脚本自己判定失败，成本低得多。
#
# 🚨 门禁必须同时满足两点（第三轮增量审查 2026-10-11 P0-3）：
# ① **带 Host 头**：nginx 对 80 端口的默认 server 块是「裸域 301 收敛到 www」
#    （`listen 80; server_name wdmao.cn; return 301 ...`；全文件无 default_server，
#    nginx 取**第一个** server 块作默认）。探针写 `http://127.0.0.1:80/...` 必然
#    命中它并拿到 301。
# ② **显式比对状态码**，不能用 `curl -f`：`-f` 只在 >=400 时失败、**3xx 视为成功**
#    —— 于是门禁在第一次轮询就通过、约 3 秒后无条件打印「部署完成」，后端是否健康、
#    是否 OOM、是否在 crash loop 全部不参与判断（P2-16 引入该门禁的收益被完全抵消）。
HEALTH_HOST="${HEALTH_HOST:-www.wdmao.cn}"

# 探针：只输出 HTTP 状态码（连不上统一为 000）。**必须始终返回 0**，
# 否则 `set -e` 下 `code="$(http_code ...)"` 会直接把脚本打断。
# `--noproxy '*'`：健康探针必须**直连本地**，绝不能被 http_proxy 劫持 ——
# 宿主机若残留 `http_proxy`/`HTTP_PROXY`，curl 会把 127.0.0.1 也走代理，
# 拿回一个与本站无关的 502，门禁报出的原因就完全跑偏了。
http_code() {
    local out=""
    out="$(curl -sS --noproxy '*' -o /dev/null --max-time 5 -H "Host: ${HEALTH_HOST}" \
        -w '%{http_code}' "$1" 2>/dev/null)" || true
    printf '%s' "${out:-000}"
}

# 带**自定义 Host** 的探针变体（www 之外还有第二个 server 块要探）。
# HEALTH_HOST 的说明、--noproxy 与「必须返回 0」的约束与 http_code 完全一致。
http_code_host() {
    local host="$1"
    shift
    local out=""
    out="$(curl -sS --noproxy '*' -o /dev/null --max-time 5 -H "Host: ${host}" \
        -w '%{http_code}' "$1" 2>/dev/null)" || true
    printf '%s' "${out:-000}"
}

wait_for_healthy() {
    local url="$1" timeout="${2:-180}" interval=3 elapsed=0 code=""
    if ! command -v curl >/dev/null 2>&1; then
        warn "未检测到 curl，跳过健康校验（无法自动确认服务是否真的起来了）"
        sleep 5
        return 0
    fi
    while [[ $elapsed -lt $timeout ]]; do
        code="$(http_code "$url")"
        if [[ "$code" == "200" ]]; then
            echo
            return 0
        fi
        printf '.'
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
    echo
    err "服务在 ${timeout}s 内未通过健康校验：${url} 最后一次返回 ${code}（期望 200）"
    return 1
}

# 统计后台（tj.wdmao.cn）的存活门禁（缺口台账 P1-80②）。
# 🚨 背景口径：2026-10-14 整域 500 的线上故障 —— auth_basic 口令文件权限问题让
# tj. 域整体 500，而当时的发版门禁只探主站，「部署成功 + 统计后台奔溃」同时成立。
# 探针仍**走本地直连**（127.0.0.1 + Host 头，同 wait_for_healthy 的两条铁律：
# 不经 EdgeOne / 不吃代理环境变量），比探 https://tj.wdmao.cn 更可靠 ——
# 出公网探活会把「本站没起」误判成「EdgeOne 或外网链路的问题」，报错原因此刻最宝贵。
# Umami 启动比 app 慢（建库 + 迁移），单独放宽到 120s；探的是 `/`（报表入口），
# 主站门禁照旧在其之前先行通过，故此处失败必是 tj 这个 server 块自身的问题。
STATS_HOST="${STATS_HOST:-tj.wdmao.cn}"

wait_for_stats() {
    local url="$1" timeout="${2:-120}" interval=3 elapsed=0 code=""
    if ! command -v curl >/dev/null 2>&1; then
        warn "未检测到 curl，跳过统计后台健康校验"
        return 0
    fi
    while [[ $elapsed -lt $timeout ]]; do
        code="$(http_code_host "$STATS_HOST" "$url")"
        if [[ "$code" == "200" ]]; then
            echo
            log "统计后台 ${STATS_HOST} 200"
            return 0
        fi
        printf '.'
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
    echo
    err "统计后台 ${STATS_HOST} 在 ${timeout}s 内未返回 200（最后 ${code}）—— tj. 整域故障曾静默上线（P1-80），本次发版按失败处理"
    return 1
}

# 深度探活：**只报告，不阻断**。
# 它会因 MySQL/Redis 故障与 schema 漂移返回 503，而应用对 Redis 故障是**优雅降级**的
# （Redis 挂了站点照常服务）—— 拿它当硬门禁，会让一次本该成功的发版被无关依赖卡住。
# 但它能给出 router.py 已分类好的具体原因（MySQLSessionNotUTC / SchemaOutOfDate /
# 驱动异常 / Redis 故障），比「等用户来发现」有用得多，所以放在存活门禁之后打印。
report_health_deep() {
    command -v curl >/dev/null 2>&1 || return 0
    local url="http://127.0.0.1:${NGINX_PORT:-80}/api/v1/health/deep"
    local code body=""
    code="$(http_code "$url")"
    if [[ "$code" == "200" ]]; then
        log "深度探活 200（MySQL / Redis / schema 均正常）"
        return 0
    fi
    body="$(curl -sS --noproxy '*' --max-time 5 -H "Host: ${HEALTH_HOST}" "$url" 2>/dev/null)" || body=""
    err "深度探活返回 ${code}（非 200，**不阻断本次部署**，但这次发版不能算「验证通过」）"
    err "  响应体：${body:-（无）}"
    err "  该端点因 MySQL/Redis 故障或 schema 漂移返回 503；Redis 故障下站点仍可服务。"
    err "  排查：docker compose logs app --tail=100"
    docker compose logs app --tail=50 2>/dev/null || true
}

# ---------- 内容页冒烟门禁（2026-10-20 线上事故）----------
# 🚨 背景：`get_tutorial_intro` 的 `load_only` 漏了 `updated_at`，异步会话里读未加载的列
# 抛 MissingGreenlet → **所有教程的介绍页/详情页 500**。而当时的发版门禁只探
# `/api/v1/health` 和 `/health/deep`：前者是纯存活探针，后者只查 MySQL/Redis/schema ——
# 两者都 200，于是「部署成功」和「所有教程打不开」同时成立，直到用户来报。
#
# 教训：健康探针**不碰任何真实业务查询**，这类 ORM 层的 500 它天生看不见。
# 所以这里补一道真正走业务链路的冒烟：每类内容取列表第一条，再请求它的详情接口。
#
# 判定口径：
#   - 详情对游客返回 401/403 属**正常**（教程正文需登录），只有 5xx 才算失败 ——
#     本门禁只判「服务端有没有炸」，不判业务权限，避免把产品决策当故障。
#   - 教程额外探公开的 `/intro`：介绍页与详情页首屏都靠它，必须是 200。
#   - 某类型没有数据时跳过（不拿空库把发版卡住）。
#
# 为什么不依赖 jq/python：线上宿主机不保证装了。用单个 awk 正则取第一个 "id"，
# 且**不组成管道** —— `set -o pipefail` 下 `grep | head` 会因 SIGPIPE 返回 141，
# 赋值语句就会把整个脚本打断（与 http_code「必须始终返回 0」是同一个坑）。
smoke_content_endpoints() {
    command -v curl >/dev/null 2>&1 || return 0
    local base="${SMOKE_BASE:-http://127.0.0.1:${NGINX_PORT:-80}/api/v1}"
    local mods="nodes resources plugins tutorials tools"
    # 每类取前 3 条：这类 500 往往**依赖数据**（某条记录的 JSON 字段形态特殊才炸），
    # 只测第一条容易漏。3 条是「多覆盖一点」与「别把发版拖慢」的折中。
    local sample=3
    local failed="" checked=0 mod list ids id code
    for mod in $mods; do
        list="$(curl -sS --noproxy '*' --max-time 8 -H "Host: ${HEALTH_HOST}" \
            "${base}/${mod}?page=1&page_size=${sample}" 2>/dev/null)" || list=""
        # 逐个取出前 sample 个 "id"（不依赖 jq/python；awk 内部计数，
        # 不用 `| head` —— pipefail 下 SIGPIPE 会让整个脚本退出）
        ids="$(printf '%s' "$list" | awk -v n=0 -v limit="$sample" '{
            while (n < limit && match($0, /"id":[[:space:]]*[0-9]+/)) {
                s = substr($0, RSTART, RLENGTH); gsub(/[^0-9]/, "", s);
                print s; n++; $0 = substr($0, RSTART + RLENGTH)
            }
        }')" || ids=""
        if [[ -z "$ids" ]]; then
            warn "冒烟：/${mod} 无数据或响应不可解析，跳过该类型"
            continue
        fi
        while IFS= read -r id; do
            [[ -z "$id" ]] && continue
            code="$(http_code "${base}/${mod}/${id}")"
            checked=$((checked + 1))
            case "$code" in
                2*|401|403) ;;
                *) failed="${failed}  /${mod}/${id} → ${code}"$'\n' ;;
            esac
            if [[ "$mod" == "tutorials" ]]; then
                code="$(http_code "${base}/tutorials/${id}/intro")"
                checked=$((checked + 1))
                if [[ "$code" != "200" ]]; then
                    failed="${failed}  /tutorials/${id}/intro → ${code}"$'\n'
                fi
            fi
        done <<< "$ids"
    done
    if [[ -z "$failed" ]]; then
        log "内容页冒烟通过（${checked} 个公开接口，无 5xx）"
        return 0
    fi
    err "内容页冒烟失败 —— 有公开接口返回异常（共检查 ${checked} 个）："
    printf '%s' "$failed" | while IFS= read -r line; do
        [[ -n "$line" ]] && err "$line"
    done
    err "  5xx 说明服务端在**真实业务查询**上炸了，而健康探针看不见这类问题。"
    err "  排查：docker compose logs app --tail=100"
    return 1
}

# 校验失败时打印诊断信息，省去「再去手动翻日志」这一步
diagnose() {
    echo
    err "服务未在预期时间内通过健康校验 —— 以下是诊断信息："
    echo
    echo "--- 容器状态 ---"
    docker compose ps || true
    echo
    echo "--- 各服务最后 30 行日志 ---"
    docker compose logs --tail=30 || true
    echo
    warn "数据卷未被删除，修复后可重新执行本脚本。"
    warn "回退代码前先看部署说明书 10.5：本仓库的迁移可能是**破坏性**的（删列 / 表改名），"
    # ⚠️ 双引号里不能出现裸反引号：bash 会当成命令替换去执行（`git checkout` 真被跑了一次，
    # 在非 git 目录下打出 fatal 并把那段文字替换成空）。这里用「」代替。
    warn "  旧代码 + 新 schema 会直接 1146/1054 —— 只「git checkout 上一提交」并不能把站点救回来，"
    warn "  需要的是「回滚 schema（alembic downgrade 到代码对应的 revision）或恢复本次发版前的备份」。"
}

# ---------- 低内存构建 (4G 服务器防 OOM) ----------
# 1. 构建前停掉**应用层**旧容器释放内存 (数据卷保留)
# 2. 逐个服务串行构建 (COMPOSE_PARALLEL_LIMIT=1 禁止 compose 并行构建)
# 3. 前端两个构建已在 Dockerfile.nginx 中合并为单 stage 顺序执行
#
# ⚠️ 只停应用层，**不停 mysql/redis**（第二轮增量审查 P2-16）：
# 原实现是 `docker compose stop` 全停，于是发版期间数据库与缓存也一并下线，
# 把「全站 502」的窗口白白拉长几分钟，且 MySQL 重启后还要经历缓冲池预热。
# 停掉应用层已足够释放构建所需内存（app/scheduler 是内存大头）。
APP_SERVICES="app scheduler nginx"

# 发版版本号（第三轮增量审查 P2-28）。
# 两个 Sentry SDK（后端 APP_RELEASE / 前端构建期 VITE_APP_RELEASE，经 compose 转发）
# 都以它作为 release；不设置时 release=undefined —— **每次发版在 Sentry 里塌缩成
# 同一个桶**，「昨天 18:04 那次发版是不是元凶」无法回答，也失去按版本回归检测的能力。
# 优先用 .env / 环境里显式配置的值；未配置时取当前提交短号；连 git 都没有
# （tar 包部署）则生成时间戳兜底 —— 至少让每次发版可区分。
resolve_release_tag() {
    if [[ -n "${APP_RELEASE:-}" ]]; then
        export APP_RELEASE
        log "发布版本 APP_RELEASE=${APP_RELEASE}（来自 .env / 环境）"
        return 0
    fi
    local tag=""
    if command -v git >/dev/null 2>&1 && tag="$(git rev-parse --short HEAD 2>/dev/null)" && [[ -n "$tag" ]]; then
        :
    else
        tag="deploy-$(date -u +%Y%m%d-%H%M%S)"
        warn "无法读取 git 提交号（tar 包部署？），APP_RELEASE 用生成值 ${tag}"
        warn "  → Sentry 仍可按本次发版分桶，但无法与本仓库提交对应"
    fi
    export APP_RELEASE="$tag"
    log "发布版本 APP_RELEASE=${APP_RELEASE}"
}

# 发版前给现有镜像打 :prev 快照，失败时至少有「一条命令换回上一版」的退路。
# 只做快照、**不自动回滚**：compose up 会先跑 migrate，迁移一旦前滚，
# 旧镜像配新库结构往往报错更多 —— 自动回滚会把「新版本挂了」变成「新旧都不行」。
tag_previous_images() {
    local img
    for img in ai-cat-backend ai-cat-nginx; do
        if docker image inspect "${img}:latest" >/dev/null 2>&1; then
            if docker tag "${img}:latest" "${img}:prev" 2>/dev/null; then
                log "已保存上一版镜像 ${img}:prev"
            fi
        fi
    done
}

rollback_hint() {
    warn "回滚方式（上一版镜像已存为 :prev）："
    warn "  docker tag ai-cat-backend:prev ai-cat-backend"
    warn "  docker tag ai-cat-nginx:prev ai-cat-nginx"
    warn "  docker compose up -d"
    warn "  ⚠️ 若本次已执行过迁移：先 alembic downgrade 或恢复发版前备份，否则旧代码可能读不了新表结构"
}

# 清理悬空镜像：每次 rebuild 都会留下上一份（nginx 镜像内含两套前端 dist，几百 MB），
# 不清理时磁盘会随发版次数单调增长（第五轮审查 OPS-11）。
# 只删 dangling 且超过 7 天的：`:prev` 是带 tag 的镜像，不受影响。
prune_old_images() {
    command -v docker >/dev/null 2>&1 || return 0
    docker image prune -f --filter "until=168h" >/dev/null 2>&1 || true
    log "已清理 7 天前的悬空镜像"
}

build_all() {
    resolve_release_tag
    tag_previous_images
    log "停止应用层旧容器以释放内存 (数据库与缓存保持运行)..."
    # shellcheck disable=SC2086
    docker compose stop $APP_SERVICES 2>/dev/null || true

    export COMPOSE_PARALLEL_LIMIT=1
    log "串行构建镜像 (低内存模式, 无并行)..."
    # 后端服务名为 app (migrate/scheduler 共用同一镜像 ai-cat-backend)
    docker compose build app
    docker compose build nginx
    unset COMPOSE_PARALLEL_LIMIT
}

# 启动失败必须进诊断（第三轮增量审查 P2-26）。
# 原实现直接 `docker compose up -d`：最常见的失败（迁移写错、端口占用、
# 依赖不满足）是非零退出，`set -e` 会**立刻终止脚本** —— 运维只看到 compose
# 的一行报错，没有 ps、没有日志尾、不知道是哪个服务；而 build_all 已经停掉了
# 旧容器 → 站点保持不可用直到人工排查。
compose_up_or_diagnose() {
    if ! docker compose up -d "$@"; then
        err "docker compose up 失败 —— 收集诊断信息（服务当前可能不可用）"
        diagnose
        rollback_hint
        exit 1
    fi
}

# ---------- 发版前强制备份（第五轮审查 R5-09）----------
# 本批发版含破坏性迁移（`20261012_0040` 删列、`0038` 改名 + downgrade 会删非教程评论行），
# 而 MySQL DDL 不是事务性的：迁移半途失败时库里是「两不管」状态，没有备份就只能手工拼 SQL。
# 必须在**旧容器仍在运行、MySQL 未被触碰**的时刻备份 —— 即 build_all 之前。
# 确实不需要（例如演练机、空库）时用 DEPLOY_SKIP_BACKUP=1 显式跳过。
ensure_pre_release_backup() {
    if [[ "${DEPLOY_SKIP_BACKUP:-0}" == "1" ]]; then
        warn "DEPLOY_SKIP_BACKUP=1：跳过发版前备份 —— 本次迁移不可回滚，出问题只能手工修库"
        return 0
    fi
    if [[ ! -x scripts/backup_mysql.sh && ! -f scripts/backup_mysql.sh ]]; then
        err "找不到 scripts/backup_mysql.sh，无法在迁移前备份"
        exit 1
    fi
    # 全新机器首次部署时 mysql 容器还不存在，备份脚本会 exit 1 把整个发版挡住。
    # 备份的目的是「迁移前留退路」，没有旧库就没有可备份的东西 → 跳过即可。
    if ! docker compose ps --status running --services 2>/dev/null | grep -qx mysql; then
        warn "未检测到运行中的 mysql 容器（首次部署 / 库未启动）—— 跳过发版前备份"
        return 0
    fi
    log "迁移前备份数据库 (scripts/backup_mysql.sh)..."
    if ! bash scripts/backup_mysql.sh; then
        err "备份失败 —— **终止发版**（继续跑迁移就等于放弃唯一的退路）"
        err "  确认可以不带备份发版时：DEPLOY_SKIP_BACKUP=1 ./deploy.sh ${ACTION}"
        exit 1
    fi
    log "备份完成，继续发版"
}

# ---------- 命令 ----------
case "$ACTION" in
    up)
        check_docker
        check_env_file
        check_required_vars
        check_alerting
        ensure_log_dir
        ensure_pre_release_backup
        build_all
        log "启动服务..."
        compose_up_or_diagnose
        log "等待服务就绪 (最多 180s)"
        # 必须探**经反代到后端**的 /api/v1/health：nginx 的 /healthz 是 `return 200`
        # 静态端点（nginx.conf:331），app 容器全挂时它照样 200 —— 第五轮审查 R5-02。
        if ! wait_for_healthy "http://127.0.0.1:${NGINX_PORT:-80}/api/v1/health"; then
            diagnose
            rollback_hint
            exit 1
        fi
        # 主站门禁之后：统计后台的整域 500 不能静默上线（2026-10-14 复盘，P1-80②）
        if ! wait_for_stats "http://127.0.0.1:${NGINX_PORT:-80}/"; then
            diagnose
            rollback_hint
            exit 1
        fi
        report_health_deep
        # 最后一道门禁：走真实业务链路的冒烟。详情页 500 是健康探针的盲区
        # （2026-10-20 教程全站 500 就是这么发出去的），必须阻断。
        if ! smoke_content_endpoints; then
            diagnose
            rollback_hint
            exit 1
        fi
        prune_old_images
        print_access_info
        ;;
    rebuild)
        check_docker
        check_env_file
        check_required_vars
        check_alerting
        ensure_log_dir
        ensure_pre_release_backup
        build_all
        log "启动服务..."
        compose_up_or_diagnose --force-recreate
        log "等待服务就绪 (最多 180s)"
        if ! wait_for_healthy "http://127.0.0.1:${NGINX_PORT:-80}/api/v1/health"; then
            diagnose
            rollback_hint
            exit 1
        fi
        # 同上（P1-80②）：rebuild 的门禁两侧一致
        if ! wait_for_stats "http://127.0.0.1:${NGINX_PORT:-80}/"; then
            diagnose
            rollback_hint
            exit 1
        fi
        report_health_deep
        # 最后一道门禁：走真实业务链路的冒烟。详情页 500 是健康探针的盲区
        # （2026-10-20 教程全站 500 就是这么发出去的），必须阻断。
        if ! smoke_content_endpoints; then
            diagnose
            rollback_hint
            exit 1
        fi
        prune_old_images
        print_access_info
        ;;
    down)
        check_docker
        log "停止并移除容器 (数据卷保留)..."
        docker compose down
        ;;
    restart)
        check_docker
        log "重启所有服务..."
        docker compose restart
        ;;
    logs)
        check_docker
        docker compose logs -f --tail=100
        ;;
    status)
        check_docker
        docker compose ps
        ;;
    *)
        err "未知动作: $ACTION"
        echo "用法: $0 [up|down|restart|rebuild|logs|status]"
        exit 1
        ;;
esac
