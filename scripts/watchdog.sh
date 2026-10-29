#!/usr/bin/env bash
# AI 猫平台 - 服务看门狗 / 主动告警：把「故障」变成「通知」（探活/容器/磁盘/备份检查）
#
# 用法:
#   ./scripts/watchdog.sh                  检查一次（异常时才推送告警）
#   ./scripts/watchdog.sh --install-cron   安装定时任务（默认每 5 分钟）
#   ./scripts/watchdog.sh --uninstall-cron 移除定时任务
#   ./scripts/watchdog.sh --status         查看定时任务状态
#   ./scripts/watchdog.sh --doctor         环境自检（依赖/webhook/探活可达性）
#   ./scripts/watchdog.sh --test-alert     发一条测试告警
#   ./scripts/watchdog.sh --verbose        即使一切正常也打印检查明细
#
# 可调环境变量（均可写在项目根 .env；优先级 环境变量 > .env > 默认值）:
#   WATCHDOG_WEBHOOK                告警 webhook 地址（不配则只写日志，不推送）
#   WATCHDOG_HEALTH_URL             深度探活地址  默认 http://127.0.0.1/api/v1/health/deep
#   WATCHDOG_HEALTH_HOST            探针 Host 头  默认 www.wdmao.cn（必带，不带会拿到 301 假警）
#   WATCHDOG_DISK_PERCENT           磁盘告警阈值  默认 85（%）
#   WATCHDOG_BACKUP_MAX_AGE_HOURS   备份最大年龄  默认 48（小时）
#   WATCHDOG_CRON                   cron 表达式   默认 "*/5 * * * *"
#   WATCHDOG_TIMEOUT                单次检查超时  默认 10（秒）
#
# webhook 按 URL 自动识别：Server酱走 form 表单，其余按企业微信 JSON 格式。
# 检查项：①深度探活非 200 ②容器崩溃重启循环 ③磁盘超阈值 ④备份过旧 ⑤备份目录不存在或不可写
# 退出码: 0 = 一切正常（或已成功告警）；1 = 检出问题

set -euo pipefail

# 路径与默认值
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# 从项目根 .env 读取 WATCHDOG_* 配置（优先级 环境变量 > .env > 默认值）。
# 刻意不 source 整个 .env：其中含 $、反引号的密钥按 shell 执行会被展开甚至破坏，只挑 WATCHDOG_* 键。
load_env_watchdog_vars() {
    local env_file="${PROJECT_DIR}/.env"
    [ -f "${env_file}" ] || return 0
    local line key value
    while IFS= read -r line || [ -n "${line}" ]; do
        case "${line}" in
            WATCHDOG_*=*) ;;
            *) continue ;;
        esac
        key="${line%%=*}"
        value="${line#*=}"
        value="${value%$'\r'}"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
        # 已由外部环境显式设置的优先（允许 `WATCHDOG_CRON='*/1 * * * *' ./scripts/watchdog.sh` 临时覆盖）
        if [ -z "$(printenv "${key}" 2>/dev/null || true)" ]; then
            export "${key}=${value}"
        fi
    done < "${env_file}"
}
load_env_watchdog_vars

WATCHDOG_WEBHOOK="${WATCHDOG_WEBHOOK:-}"
WATCHDOG_HEALTH_URL="${WATCHDOG_HEALTH_URL:-http://127.0.0.1/api/v1/health/deep}"
WATCHDOG_DISK_PERCENT="${WATCHDOG_DISK_PERCENT:-85}"
WATCHDOG_BACKUP_MAX_AGE_HOURS="${WATCHDOG_BACKUP_MAX_AGE_HOURS:-48}"
WATCHDOG_CRON="${WATCHDOG_CRON:-*/5 * * * *}"
WATCHDOG_TIMEOUT="${WATCHDOG_TIMEOUT:-10}"

CRON_MARKER="# ai-cat-watchdog"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_DIR}/backups}"
LOCK_FILE="${BACKUP_DIR}/.watchdog.lock"
LOG_FILE="${BACKUP_DIR}/watchdog.log"
# 同一告警的抑制窗口（秒）：持续故障每 5 分钟推一条会把机器人刷屏，
# 反而盖住「故障刚开始」那一条（第五轮审查 OPS-12）
WATCHDOG_ALERT_SUPPRESS_SECONDS="${WATCHDOG_ALERT_SUPPRESS_SECONDS:-1800}"
ALERT_STATE_FILE="${BACKUP_DIR}/.watchdog_alert_state"
# 日志轮转阈值（字节，默认 5MB）：cron 每 5 分钟一条，一年也就几百 KB，
# 但一旦容器反复崩溃/网络抖动，日志会线性长到把磁盘吃满（第五轮审查 OPS-12）。
# 只保留 1 份历史（watchdog.log.1），总量上限 = 2 × 阈值。
WATCHDOG_LOG_MAX_BYTES="${WATCHDOG_LOG_MAX_BYTES:-5242880}"

VERBOSE=0
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

mkdir -p "${BACKUP_DIR}" 2>/dev/null || true

# 日志超过阈值就轮转：把当前文件改名为 .log.1（覆盖上一份），再新建空文件。
# 刻意只留 1 份历史 —— 看门狗日志的用途是「最近发生了什么」，
# 不是审计留存（审计在 admin_audit_logs 里），保留多份只会继续吃盘。
_rotate_log_if_needed() {
    [ -f "${LOG_FILE}" ] || return 0
    local size
    size="$(wc -c < "${LOG_FILE}" 2>/dev/null | tr -d ' ')"
    [ -n "${size}" ] || return 0
    [ "${size}" -ge "${WATCHDOG_LOG_MAX_BYTES}" ] 2>/dev/null || return 0
    mv -f "${LOG_FILE}" "${LOG_FILE}.1" 2>/dev/null || return 0
    printf '%s [info] 日志已达 %s 字节，已轮转（上一份为 %s.1）\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" "${size}" "${LOG_FILE}" >> "${LOG_FILE}" 2>/dev/null || true
}

# 输出与日志
# cron 场景 stdout 会进邮件或直接丢弃，落文件才查得到
log() {
    local level="$1"; shift
    local ts; ts="$(date '+%Y-%m-%d %H:%M:%S')"
    _rotate_log_if_needed
    printf '%s [%s] %s\n' "${ts}" "${level}" "$*" >> "${LOG_FILE}" 2>/dev/null || true
}

say()  { printf '%b\n' "$*"; }
info() { [ "${VERBOSE}" -eq 1 ] && say "${CYAN}[信息]${NC} $*" || true; }
ok()   { [ "${VERBOSE}" -eq 1 ] && say "${GREEN}[正常]${NC} $*" || true; }

usage() {
    sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# HTTP 工具（curl 优先，wget 兜底）
# 🚨 探针必须带 Host 头：nginx 80 端口的默认 server 块会把裸域 301 收敛到 www，
# 默认探活地址是 127.0.0.1，不带 Host 就命中它拿到 301 → 看门狗开箱即报假警。
WATCHDOG_HEALTH_HOST="${WATCHDOG_HEALTH_HOST:-www.wdmao.cn}"

# --noproxy '*'：探针必须直连本地，不能被 http_proxy 劫持（残留代理会把 127.0.0.1 走代理造成假告警）
http_get_code() {
    local url="$1"
    if command -v curl >/dev/null 2>&1; then
        # curl 连接失败时 -w 自会输出 000，不可再加 || echo 000（会拼成两个 000 使判等失败）
        local out=""
        out="$(curl -s --noproxy '*' -o /dev/null -m "${WATCHDOG_TIMEOUT}" \
            -H "Host: ${WATCHDOG_HEALTH_HOST}" -w '%{http_code}' "${url}" 2>/dev/null)" || true
        printf '%s' "${out:-000}"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O /dev/null -T "${WATCHDOG_TIMEOUT}" \
            --header="Host: ${WATCHDOG_HEALTH_HOST}" --server-response "${url}" 2>&1 \
            | awk '/HTTP\//{code=$2} END{print (code==""?"000":code)}'
    else
        echo "000"
    fi
}

# 取响应体（截断到 400 字符）。/health/deep 已把失败原因分类好，必须带进告警文案，丢掉等于丢诊断信息。
http_get_body() {
    local url="$1"
    if command -v curl >/dev/null 2>&1; then
        curl -s --noproxy '*' -m "${WATCHDOG_TIMEOUT}" -H "Host: ${WATCHDOG_HEALTH_HOST}" \
            "${url}" 2>/dev/null | head -c 400 || true
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O - -T "${WATCHDOG_TIMEOUT}" \
            --header="Host: ${WATCHDOG_HEALTH_HOST}" "${url}" 2>/dev/null \
            | head -c 400 || true
    fi
}

# 推送告警。返回 0 表示「已送达或无需送达」，不因推送失败而中断检查流程。
push_alert() {
    local title="$1" body="$2"
    log "ALERT" "${title} | ${body}"

    if [ -z "${WATCHDOG_WEBHOOK}" ]; then
        say "${YELLOW}[告警]${NC} ${title}"
        say "       ${body}"
        say "       ${YELLOW}(未配置 WATCHDOG_WEBHOOK，仅记录到 ${LOG_FILE})${NC}"
        return 0
    fi

    # 同一类告警在窗口内只推一次（按 title 区分；检查项各自有独立标题）
    local sig now_ts last_sig="" last_ts=0
    sig="$(printf '%s' "${title}" | cksum | awk '{print $1}')"
    now_ts="$(date +%s)"
    if [ -f "${ALERT_STATE_FILE}" ]; then
        read -r last_sig last_ts < "${ALERT_STATE_FILE}" 2>/dev/null || true
    fi
    if [ "${sig}" = "${last_sig:-}" ] && [ $((now_ts - ${last_ts:-0})) -lt "${WATCHDOG_ALERT_SUPPRESS_SECONDS}" ]; then
        log "ALERT-SUPPRESSED" "${title} | 距上次推送不足 ${WATCHDOG_ALERT_SUPPRESS_SECONDS}s，本次只记日志"
        return 0
    fi
    printf '%s %s\n' "${sig}" "${now_ts}" > "${ALERT_STATE_FILE}" 2>/dev/null || true

    local ok_flag=0
    if [[ "${WATCHDOG_WEBHOOK}" == *"sctapi.ftqq.com"* ]]; then
        # Server酱：form 表单
        curl -s -m "${WATCHDOG_TIMEOUT}" -X POST "${WATCHDOG_WEBHOOK}" \
            --data-urlencode "title=${title}" \
            --data-urlencode "desp=${body}" >/dev/null 2>&1 && ok_flag=1 || true
    else
        # 企业微信 / 飞书 / 钉钉 / 自建：JSON。用 python 做安全转义（有则用，无则手工兜底）
        local payload
        if command -v python3 >/dev/null 2>&1; then
            payload="$(python3 -c 'import json,sys; print(json.dumps({"msgtype":"text","text":{"content":sys.argv[1]}}))' "${title}
${body}" 2>/dev/null)" || payload=""
        fi
        if [ -z "${payload}" ]; then
            payload="{\"msgtype\":\"text\",\"text\":{\"content\":\"${title} - ${body}\"}}"
        fi
        curl -s -m "${WATCHDOG_TIMEOUT}" -X POST "${WATCHDOG_WEBHOOK}" \
            -H 'Content-Type: application/json' -d "${payload}" >/dev/null 2>&1 && ok_flag=1 || true
    fi

    if [ "${ok_flag}" -eq 1 ]; then
        say "${RED}[告警已推送]${NC} ${title}"
    else
        say "${RED}[告警推送失败]${NC} ${title}（webhook 不可达，已记录本地日志）"
    fi
    return 0
}

# 各项检查
PROBLEMS=()

check_health() {
    local code
    code="$(http_get_code "${WATCHDOG_HEALTH_URL}")"
    if [ "${code}" = "200" ]; then
        ok "深度探活 200（${WATCHDOG_HEALTH_URL}）"
    elif [ "${code}" = "000" ]; then
        PROBLEMS+=("深度探活不可达：${WATCHDOG_HEALTH_URL}（服务可能已停）")
    else
        # 带上响应体：/health/deep 已把原因分类好，丢掉等于丢诊断依据
        PROBLEMS+=("深度探活返回 ${code}（非 200）：${WATCHDOG_HEALTH_URL} —— 依赖或 schema 异常，响应体：$(http_get_body "${WATCHDOG_HEALTH_URL}")")
    fi
}

check_containers() {
    command -v docker >/dev/null 2>&1 || { info "未安装 docker，跳过容器检查"; return 0; }

    # 三种「不正常」要分别看（第五轮审查 OPS-09）：
    #   restarting —— 崩溃循环，最容易发现
    #   unhealthy  —— 进程活着但健康检查连续失败（app 就是这种：进程在、依赖挂了）
    #   exited     —— 已退出（umami / postgres 这类非核心服务退出时最容易没人发现）
    local restarting
    restarting="$(docker ps --filter 'status=restarting' --format '{{.Names}}' 2>/dev/null || true)"
    if [ -n "${restarting}" ]; then
        PROBLEMS+=("容器处于崩溃重启循环：$(echo "${restarting}" | tr '\n' ' ')")
    fi

    local unhealthy
    unhealthy="$(docker ps --filter 'health=unhealthy' --format '{{.Names}}' 2>/dev/null || true)"
    if [ -n "${unhealthy}" ]; then
        PROBLEMS+=("容器健康检查失败（Up 但 unhealthy）：$(echo "${unhealthy}" | tr '\n' ' ')")
    fi

    local exited
    exited="$(docker ps -a --filter 'status=exited' --format '{{.Names}}' 2>/dev/null | grep -v 'ai-cat-migrate' || true)"
    if [ -n "${exited}" ]; then
        PROBLEMS+=("容器已退出（非 migrate）：$(echo "${exited}" | tr '\n' ' ')")
    fi

    if [ -z "${restarting}${unhealthy}${exited}" ]; then
        ok "容器状态正常（无 restarting / unhealthy / exited）"
    fi
}

check_disk() {
    local used
    # 取项目所在分区的使用率（去掉 % 号）
    used="$(df -Pk "${PROJECT_DIR}" 2>/dev/null | awk 'NR==2{gsub(/%/,"",$5); print $5}')"
    if [ -z "${used}" ]; then
        info "无法读取磁盘使用率，跳过"
        return 0
    fi
    if [ "${used}" -ge "${WATCHDOG_DISK_PERCENT}" ]; then
        PROBLEMS+=("磁盘使用率 ${used}% ≥ 阈值 ${WATCHDOG_DISK_PERCENT}%（满盘会导致 MySQL 写失败、备份写不下）")
    else
        ok "磁盘使用率 ${used}%"
    fi
}

check_backup_freshness() {
    if [ ! -d "${BACKUP_DIR}" ]; then
        PROBLEMS+=("备份目录不存在：${BACKUP_DIR}")
        return 0
    fi
    # 找最近一份 .sql.gz
    local latest
    latest="$(find "${BACKUP_DIR}" -maxdepth 1 -name '*.sql.gz' -type f -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | head -1 | awk '{print $2}')"
    if [ -z "${latest}" ]; then
        PROBLEMS+=("备份目录内没有任何 .sql.gz 备份（自动备份可能从未成功运行）")
        return 0
    fi
    local age_sec now mtime age_hours
    now="$(date +%s)"
    mtime="$(date -r "${latest}" +%s 2>/dev/null || stat -c %Y "${latest}" 2>/dev/null || echo 0)"
    age_sec=$(( now - mtime ))
    age_hours=$(( age_sec / 3600 ))
    if [ "${age_hours}" -ge "${WATCHDOG_BACKUP_MAX_AGE_HOURS}" ]; then
        PROBLEMS+=("最近一份备份已 ${age_hours} 小时（阈值 ${WATCHDOG_BACKUP_MAX_AGE_HOURS}h）：$(basename "${latest}")")
    else
        ok "最近备份 ${age_hours} 小时前：$(basename "${latest}")"
    fi
}

# 主流程
run_checks() {
    check_health
    check_containers
    check_disk
    check_backup_freshness

    if [ "${#PROBLEMS[@]}" -eq 0 ]; then
        log "OK" "全部检查通过"
        ok "全部检查通过"
        return 0
    fi

    local body=""
    local p
    for p in "${PROBLEMS[@]}"; do
        body+="· ${p}"$'\n'
    done
    push_alert "【AI 猫】服务告警（$(hostname) $(date '+%m-%d %H:%M')）" "${body}"
    return 1
}

# cron 管理
install_cron() {
    local current
    current="$(crontab -l 2>/dev/null | grep -v "${CRON_MARKER}" || true)"
    {
        echo "${current}"
        # ⚠️ 刻意不把 WATCHDOG_WEBHOOK 内联进 crontab 行：内联值会压过 .env，改 .env 不生效；
        # 本脚本自己读 .env，让 .env 成为唯一事实来源。
        echo "${WATCHDOG_CRON} cd ${PROJECT_DIR} && ./scripts/watchdog.sh >/dev/null 2>&1 ${CRON_MARKER}"
    } | grep -v '^$' | crontab -
    say "${GREEN}已安装定时任务${NC}：${WATCHDOG_CRON}"
    if [ -z "${WATCHDOG_WEBHOOK}" ]; then
        say "${YELLOW}⚠️ 未配置 WATCHDOG_WEBHOOK，告警只会写本地日志、不会推送 —— 等于叫不醒人。${NC}"
        say "   请在项目根 .env 里填（推荐，一次配好长期有效）："
        say "     WATCHDOG_WEBHOOK='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx'"
    elif ! grep -qE '^WATCHDOG_WEBHOOK=.+' "${PROJECT_DIR}/.env" 2>/dev/null; then
        # 值来自进程环境而非 .env：cron 里的脚本读不到它 → 会静默退回「只写日志」
        say "${YELLOW}⚠️ WATCHDOG_WEBHOOK 来自环境变量，但 .env 里没有它。${NC}"
        say "   cron 执行时不继承当前 shell 的环境 → 告警会静默失效。"
        say "   请把它写进 ${PROJECT_DIR}/.env 后再执行 --install-cron。"
    fi
}

uninstall_cron() {
    crontab -l 2>/dev/null | grep -v "${CRON_MARKER}" | crontab - || true
    say "${GREEN}已移除定时任务${NC}"
}

cron_status() {
    if crontab -l 2>/dev/null | grep -q "${CRON_MARKER}"; then
        say "${GREEN}定时任务已安装${NC}："
        crontab -l 2>/dev/null | grep "${CRON_MARKER}"
    else
        say "${YELLOW}未安装定时任务${NC}（运行 --install-cron 安装）"
    fi
}

doctor() {
    say "${CYAN}=== 环境自检 ===${NC}"
    command -v curl  >/dev/null 2>&1 && say "curl:  ${GREEN}可用${NC}" || say "curl:  ${YELLOW}缺失（将尝试 wget）${NC}"
    command -v wget  >/dev/null 2>&1 && say "wget:  ${GREEN}可用${NC}" || say "wget:  未安装"
    command -v docker >/dev/null 2>&1 && say "docker: ${GREEN}可用${NC}" || say "docker: ${YELLOW}缺失（跳过容器检查）${NC}"
    # 告警通道三问：配了吗/配在哪/装了吗 —— 缺一则告警静默，比没有告警更危险
    if [ -n "${WATCHDOG_WEBHOOK}" ]; then
        if grep -qE '^WATCHDOG_WEBHOOK=.+' "${PROJECT_DIR}/.env" 2>/dev/null; then
            say "webhook: ${GREEN}已配置${NC}（来源 .env ✓）"
        else
            say "webhook: ${YELLOW}来自环境变量，但 .env 里没有${NC} —— cron 不继承 shell 环境，告警会静默失效"
        fi
    else
        say "webhook: ${YELLOW}未配置${NC} —— 告警只写日志，不会叫人"
        say "         请在 ${PROJECT_DIR}/.env 填：WATCHDOG_WEBHOOK='<企业微信机器人地址>'"
    fi
    if crontab -l 2>/dev/null | grep -q "${CRON_MARKER}"; then
        say "cron:    ${GREEN}已安装${NC} $(crontab -l 2>/dev/null | grep "${CRON_MARKER}" | head -1 | awk '{print $1, $2, $3, $4, $5}')"
    else
        say "cron:    ${YELLOW}未安装${NC} —— 没装定时任务，本脚本永远不会自动跑"
        say "         执行：./scripts/watchdog.sh --install-cron"
    fi
    say "探活地址: ${WATCHDOG_HEALTH_URL}（Host: ${WATCHDOG_HEALTH_HOST}）→ HTTP $(http_get_code "${WATCHDOG_HEALTH_URL}")"
    say "备份目录: ${BACKUP_DIR} $([ -w "${BACKUP_DIR}" ] && echo "${GREEN}可写${NC}" || echo "${RED}不可写${NC}")"
    say "日志文件: ${LOG_FILE}"
}

# 参数解析
case "${1:-}" in
    --install-cron)   install_cron ;;
    --uninstall-cron) uninstall_cron ;;
    --status)         cron_status ;;
    --doctor)         doctor ;;
    --verbose)        VERBOSE=1; run_checks ;;
    --test-alert)     push_alert "【AI 猫】测试告警" "这是一条测试消息，收到即表示 webhook 配置正确。" ;;
    --help|-h)        usage ;;
    "")               run_checks ;;
    *)                say "${RED}未知参数: $1${NC}"; usage; exit 2 ;;
esac
