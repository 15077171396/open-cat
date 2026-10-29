#!/usr/bin/env bash
# AI 猫平台 - MySQL 自动备份 / 恢复 / 校验脚本（导出 → 压缩 → 校验 → 轮转 → 记日志）
#
# 用法:
#   ./scripts/backup_mysql.sh                  执行一次备份（cron 里就是这么调）
#   ./scripts/backup_mysql.sh --install-cron   安装每日定时任务（默认 03:17，可改 BACKUP_CRON）
#   ./scripts/backup_mysql.sh --uninstall-cron 移除本脚本写入的定时任务
#   ./scripts/backup_mysql.sh --cron-status    查看定时任务是否已安装
#   ./scripts/backup_mysql.sh --list           列出已有备份
#   ./scripts/backup_mysql.sh --verify         校验最近一份备份（gzip 完整性 + SQL 结构）
#   ./scripts/backup_mysql.sh --doctor         环境自检（docker/.env/容器/磁盘/目录可写）
#   ./scripts/backup_mysql.sh --restore FILE   从备份恢复（需交互确认，见下方警告）
#
# 可调环境变量（均可不设，走默认值）:
#   BACKUP_DIR        备份目录          默认 <项目根>/backups
#   BACKUP_KEEP_DAYS  保留天数          默认 30
#   BACKUP_CRON       cron 表达式       默认 "17 3 * * *"（避开整点，降低与其它任务撞车概率）
#   BACKUP_MIN_TABLES 最少表数，低于则判定备份无效  默认 10
#   MYSQL_DATABASE    库名              默认取 .env，兜底 ai_cat
#   BACKUP_AFTER_HOOK 备份成功后的**离机上传**命令（默认空 = 不上传）。
#                     脚本会以 BACKUP_FILE / BACKUP_NAME 两个环境变量把刚生成的备份传给它，例如：
#                       BACKUP_AFTER_HOOK='rclone copy "$BACKUP_FILE" remote:ai-cat-backup/'
#                       BACKUP_AFTER_HOOK='scp -q "$BACKUP_FILE" backup@10.0.0.9:/data/ai-cat/'
#                     ⚠️ 不配它 = 备份只与本机同盘（机器/磁盘坏了备份一起没），
#                        这是当前最高优先级的运维缺口，见 docs 与 reviews 的闭环评估。
#
# ⚠️ 恢复警告：--restore 会覆盖目标库的同名表；脚本会先自动备份当前库再恢复，恢复窗口内建议停写。

set -euo pipefail

# 路径与默认值
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

BACKUP_DIR="${BACKUP_DIR:-${PROJECT_DIR}/backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
BACKUP_CRON="${BACKUP_CRON:-17 3 * * *}"
BACKUP_MIN_TABLES="${BACKUP_MIN_TABLES:-10}"
BACKUP_AFTER_HOOK="${BACKUP_AFTER_HOOK:-}"
CRON_MARKER="# ai-cat-backup"          # crontab 中的唯一标记，卸载时按它定位

COMPOSE_FILE="${PROJECT_DIR}/docker-compose.yml"
ENV_FILE="${PROJECT_DIR}/.env"
LOCK_FILE="${BACKUP_DIR}/.backup.lock"
LOG_FILE="${BACKUP_DIR}/backup.log"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
log()  { echo -e "${GREEN}[backup]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[error]${NC} $*" >&2; }

# 同时写日志文件的 log（cron 场景 stdout 会进邮件/丢失，落文件才查得到）
logf() {
    echo -e "${GREEN}[backup]${NC} $*"
    echo "[$(date '+%F %T')] INFO  $*" >> "${LOG_FILE}" 2>/dev/null || true
}
errf() {
    echo -e "${RED}[error]${NC} $*" >&2
    echo "[$(date '+%F %T')] ERROR $*" >> "${LOG_FILE}" 2>/dev/null || true
}

usage() {
    sed -n '4,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
}

# 环境
load_env() {
    if [[ ! -f "${ENV_FILE}" ]]; then
        errf "未找到 ${ENV_FILE}，无法读取数据库凭据"
        exit 1
    fi
    # 只导出需要的键，避免 .env 里的特殊字符 / 注释格式把 set -a 整段搞挂
    local key keys=(MYSQL_ROOT_PASSWORD MYSQL_DATABASE MYSQL_USER)
    for key in "${keys[@]}"; do
        local val
        val="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n1 | cut -d= -f2- || true)"
        # 去掉成对引号
        val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
        [[ -n "${val}" ]] && export "${key}=${val}"
    done
    export MYSQL_DATABASE="${MYSQL_DATABASE:-ai_cat}"
}

require_docker() {
    command -v docker >/dev/null 2>&1 || { errf "未检测到 docker"; exit 1; }
    docker compose version >/dev/null 2>&1 || { errf "未检测到 docker compose (需 v2+)"; exit 1; }
}

# 拿到实际容器 ID：不走 `docker compose exec`，避免 compose 自身的告警输出混进 SQL 流
mysql_container() {
    local cid
    cid="$(docker compose -f "${COMPOSE_FILE}" ps -q mysql 2>/dev/null || true)"
    if [[ -z "${cid}" ]]; then
        cid="$(docker ps -q --filter 'name=ai-cat-mysql' 2>/dev/null || true)"
    fi
    echo "${cid}"
}

ensure_dirs() {
    mkdir -p "${BACKUP_DIR}"
    touch "${LOG_FILE}" 2>/dev/null || true
    # 备份目录里有数据库全量内容，别让同机其他用户读走
    chmod 700 "${BACKUP_DIR}" 2>/dev/null || true
}

# 磁盘空间预检（避免备份写一半把盘撑满）
check_disk_space() {
    local need_kb avail_kb
    need_kb=${BACKUP_MIN_FREE_KB:-1048576}   # 默认预留 1GB
    avail_kb="$(df -Pk "${BACKUP_DIR}" | awk 'NR==2{print $4}')"
    if [[ -n "${avail_kb}" && "${avail_kb}" -lt "${need_kb}" ]]; then
        errf "备份目录可用空间不足：${avail_kb}KB < 需要 ${need_kb}KB（$((avail_kb/1024))MB）"
        return 1
    fi
    return 0
}

# 备份
do_backup() {
    ensure_dirs
    require_docker
    load_env

    if [[ -z "${MYSQL_ROOT_PASSWORD:-}" ]]; then
        errf ".env 中 MYSQL_ROOT_PASSWORD 为空，无法备份"
        exit 1
    fi

    local cid
    cid="$(mysql_container)"
    if [[ -z "${cid}" ]]; then
        errf "mysql 容器未运行（compose 服务名 mysql / 容器名 ai-cat-mysql 都没找到）"
        exit 1
    fi

    check_disk_space

    local ts target tmp
    ts="$(date '+%Y%m%d_%H%M%S')"
    target="${BACKUP_DIR}/ai_cat_${ts}.sql.gz"
    tmp="${BACKUP_DIR}/.ai_cat_${ts}.sql.gz.part"

    logf "开始备份 ${MYSQL_DATABASE} → ${target}"

    # 密码走容器内环境变量，不出现在命令行（避免 ps 泄漏）；边导边压，不落地未压缩中间文件；
    # 原子写：先落 .part，校验通过再 mv，避免 cron 留下半截文件被当成有效备份
    # shellcheck disable=SC2016
    if ! docker exec "${cid}" sh -c \
        'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" \
            --single-transaction --quick \
            --routines --triggers --events \
            --set-gtid-purged=OFF \
            --default-character-set=utf8mb4 \
            "$MYSQL_DATABASE"' 2>>"${LOG_FILE}" | gzip -9 > "${tmp}"; then
        rm -f "${tmp}"
        errf "mysqldump 执行失败，已丢弃半成品（详见 ${LOG_FILE}）"
        exit 1
    fi

    if ! verify_file "${tmp}"; then
        rm -f "${tmp}"
        errf "备份产出未通过校验（表数不足或 gzip 损坏），已丢弃"
        exit 1
    fi

    mv -f "${tmp}" "${target}"
    chmod 600 "${target}"
    sha256sum "${target}" > "${target}.sha256" 2>/dev/null || true

    local size
    size="$(du -h "${target}" | awk '{print $1}')"
    logf "备份完成：$(basename "${target}")（${size}）"

    rotate
    logf "轮转完成：保留 ${BACKUP_KEEP_DAYS} 天，当前 $(find "${BACKUP_DIR}" -maxdepth 1 -name 'ai_cat_*.sql.gz' | wc -l | tr -d ' ') 份"

    # 离机副本放在最后：本地备份已可用，钩子失败不撤销这份备份
    run_after_hook "${target}" || true
}

# 校验：gzip 完整性 + 至少 N 张 CREATE TABLE（防"空库 / 截断文件"被当成成功）
verify_file() {
    local file="$1" tables
    gzip -t "${file}" 2>/dev/null || { errf "gzip 完整性校验失败：${file}"; return 1; }
    tables="$(gzip -dc "${file}" 2>/dev/null | grep -c '^CREATE TABLE' || true)"
    if [[ -z "${tables}" || "${tables}" -lt "${BACKUP_MIN_TABLES}" ]]; then
        errf "备份内容异常：只解析出 ${tables:-0} 张 CREATE TABLE（阈值 ${BACKUP_MIN_TABLES}）"
        return 1
    fi
    return 0
}

# 离机上传钩子：把「备份出机器」这件事做成可配置的一步，而不是写死某家的 SDK。
#
# 为什么用「任意命令」而不是内置 COS 上传：备份脚本跑在宿主机，没有 Python/venv，
# 用 curl 手写 COS 签名既长又容易错（签名错会静默失败）。用户填 rclone / scp /
# 云厂商 CLI 任意一种即可，脚本只负责在**校验通过之后**把它调起来。
#
# 失败语义（刻意如此）：本地备份已落盘并校验通过 → 脚本整体仍算成功（退出 0），
# 但会打 ERROR 并写日志。因为「本地备份成功」是事实，「没有异地副本」是另一件事；
# 把两者混成一个失败码会让 cron 的告警失去指向性。真正的兜底是
# scripts/watchdog.sh 的备份年龄检查 + 后台「系统状态」卡。
run_after_hook() {
    local file="$1"
    [[ -z "${BACKUP_AFTER_HOOK}" ]] && return 0

    logf "执行离机上传钩子：${BACKUP_AFTER_HOOK}"
    if BACKUP_FILE="${file}" BACKUP_NAME="$(basename "${file}")" \
        bash -c "${BACKUP_AFTER_HOOK}" 2>>"${LOG_FILE}"; then
        logf "离机上传完成：$(basename "${file}")"
        return 0
    fi
    errf "离机上传钩子执行失败 —— 本地备份有效，但**没有异地副本**（详见 ${LOG_FILE}）"
    return 1
}

rotate() {
    local deleted
    deleted="$(find "${BACKUP_DIR}" -maxdepth 1 \( -name 'ai_cat_*.sql.gz' -o -name 'ai_cat_*.sql.gz.sha256' \) \
        -type f -mtime "+${BACKUP_KEEP_DAYS}" -print -delete 2>/dev/null | wc -l | tr -d ' ')"
    [[ "${deleted}" -gt 0 ]] && logf "已清理 ${deleted} 个过期备份文件"
    return 0
}

# 列表 / 校验
do_list() {
    ensure_dirs
    if [[ -z "$(find "${BACKUP_DIR}" -maxdepth 1 -name 'ai_cat_*.sql.gz' -print -quit 2>/dev/null)" ]]; then
        warn "尚无备份：${BACKUP_DIR}"
        return 0
    fi
    printf "%-32s %-10s %s\n" "文件" "大小" "时间"
    find "${BACKUP_DIR}" -maxdepth 1 -name 'ai_cat_*.sql.gz' -printf "%f %s %TY-%Tm-%Td %TH:%TM\n" \
        | sort -r \
        | while read -r name size t1 t2; do
              printf "%-32s %-10s %s\n" "${name}" "$(num_to_human "${size}")" "${t1} ${t2}"
          done
}

num_to_human() {
    local b="$1"
    awk -v b="${b}" 'BEGIN{split("B KB MB GB TB",u," ");i=1;while(b>=1024&&i<5){b/=1024;i++}printf (i==1?"%d%s":"%.1f%s"),b,u[i]}'
}

do_verify() {
    ensure_dirs
    local latest
    latest="$(find "${BACKUP_DIR}" -maxdepth 1 -name 'ai_cat_*.sql.gz' -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | head -n1 | cut -d' ' -f2-)"
    if [[ -z "${latest}" ]]; then
        errf "没有可校验的备份"
        exit 1
    fi
    log "校验 ${latest}"
    verify_file "${latest}" && log "✅ 校验通过（gzip 完整 + 表结构正常）" || exit 1
}

# 恢复
do_restore() {
    local file="$1"
    [[ -f "${file}" ]] || { err "备份文件不存在：${file}"; exit 1; }
    ensure_dirs
    require_docker
    load_env

    local cid
    cid="$(mysql_container)"
    [[ -n "${cid}" ]] || { err "mysql 容器未运行"; exit 1; }

    verify_file "${file}" || { err "该备份文件未通过校验，拒绝恢复"; exit 1; }

    warn "即将把 ${file} 恢复到库 ${MYSQL_DATABASE}（容器 ${cid:0:12}）"
    warn "这会覆盖同名表现有数据，且无法撤销。"
    if [[ ! -t 0 ]]; then
        err "非交互环境拒绝恢复（防止 cron / 管道误触发）。请手动执行并确认。"
        exit 1
    fi
    read -r -p "请输入库名 ${MYSQL_DATABASE} 以确认: " confirm
    [[ "${confirm}" == "${MYSQL_DATABASE}" ]] || { log "已取消"; exit 0; }

    # 恢复前先留一份"案发现场"，恢复错了还能退回
    log "恢复前先备份当前库..."
    local pre="${BACKUP_DIR}/pre_restore_$(date '+%Y%m%d_%H%M%S').sql.gz"
    docker exec "${cid}" sh -c \
        'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --quick \
            --routines --triggers --events --set-gtid-purged=OFF \
            --default-character-set=utf8mb4 "$MYSQL_DATABASE"' 2>>"${LOG_FILE}" | gzip -9 > "${pre}"
    log "已保存：${pre}"

    log "开始恢复..."
    if gzip -dc "${file}" | docker exec -i "${cid}" sh -c \
        'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 "$MYSQL_DATABASE"' 2>>"${LOG_FILE}"; then
        log "✅ 恢复完成，建议执行：docker compose restart app scheduler"
    else
        errf "恢复失败，可用 ${pre} 回滚（详见 ${LOG_FILE}）"
        exit 1
    fi
}

# cron 管理
cron_line() {
    echo "${BACKUP_CRON} cd ${PROJECT_DIR} && ${SCRIPT_DIR}/backup_mysql.sh >> ${BACKUP_DIR}/cron.log 2>&1 ${CRON_MARKER}"
}

do_install_cron() {
    ensure_dirs
    local line existing
    line="$(cron_line)"
    existing="$(crontab -l 2>/dev/null | grep -F "${CRON_MARKER}" || true)"
    if [[ -n "${existing}" ]]; then
        warn "已存在本脚本的定时任务，先覆盖为最新配置："
        echo "    ${existing}"
        crontab -l 2>/dev/null | grep -vF "${CRON_MARKER}" | { cat; echo "${line}"; } | crontab -
    else
        crontab -l 2>/dev/null | { cat; echo "${line}"; } | crontab -
    fi
    log "✅ 定时任务已安装：${line}"
    log "可用 ./scripts/backup_mysql.sh --cron-status 复查，或 --doctor 做一次环境自检"
    log "建议装完立刻手动跑一次，确认真的能出文件：./scripts/backup_mysql.sh"
}

do_uninstall_cron() {
    crontab -l 2>/dev/null | grep -vF "${CRON_MARKER}" | crontab - || true
    log "已移除带 ${CRON_MARKER} 标记的定时任务"
}

do_cron_status() {
    local existing
    existing="$(crontab -l 2>/dev/null | grep -F "${CRON_MARKER}" || true)"
    if [[ -z "${existing}" ]]; then
        warn "未安装自动备份定时任务。执行 ./scripts/backup_mysql.sh --install-cron 安装"
        return 1
    fi
    log "已安装："
    echo "    ${existing}"
    local latest
    latest="$(find "${BACKUP_DIR}" -maxdepth 1 -name 'ai_cat_*.sql.gz' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -n1 | cut -d' ' -f2-)"
    if [[ -n "${latest}" ]]; then
        log "最近一份备份：${latest}（$(date -r "${latest}" '+%F %T')）"
        local age=$(( ($(date +%s) - $(date -r "${latest}" +%s)) / 86400 ))
        [[ "${age}" -ge 2 ]] && warn "⚠️ 最近备份已 ${age} 天前，定时任务可能没在跑" || log "备份时效正常"
    else
        warn "定时任务已装，但还没有任何备份文件产出"
    fi
}

# 自检
do_doctor() {
    local ok=0
    echo -e "${CYAN}===== 备份环境自检 =====${NC}"

    command -v docker >/dev/null 2>&1 && { log "docker: 已安装"; } || { err "docker: 未安装"; ok=1; }
    docker compose version >/dev/null 2>&1 && { log "docker compose: 可用"; } || { err "docker compose: 不可用"; ok=1; }
    [[ -f "${ENV_FILE}" ]] && { log ".env: 存在"; } || { err ".env: 缺失"; ok=1; }

    if [[ -f "${ENV_FILE}" ]]; then
        grep -qE '^MYSQL_ROOT_PASSWORD=.+' "${ENV_FILE}" \
            && log "MYSQL_ROOT_PASSWORD: 已配置" \
            || { err "MYSQL_ROOT_PASSWORD: 未配置"; ok=1; }
    fi

    local cid
    cid="$(mysql_container)"
    [[ -n "${cid}" ]] && log "mysql 容器: 运行中（${cid:0:12}）" || { err "mysql 容器: 未运行"; ok=1; }

    mkdir -p "${BACKUP_DIR}" 2>/dev/null && log "备份目录: ${BACKUP_DIR}（可写）" || { err "备份目录不可写"; ok=1; }

    local avail
    avail="$(df -Ph "${BACKUP_DIR}" 2>/dev/null | awk 'NR==2{print $4}')"
    [[ -n "${avail}" ]] && log "可用空间: ${avail}"

    if [[ -n "${BACKUP_AFTER_HOOK}" ]]; then
        log "离机上传钩子: 已配置（${BACKUP_AFTER_HOOK}）"
    else
        warn "离机上传钩子: 未配置 —— 备份与本机同盘，机器/磁盘故障会同时丢失数据与备份"
        warn "  建议在 .env 填 BACKUP_AFTER_HOOK（如 rclone / scp），见本脚本 --help"
    fi

    do_cron_status || ok=1

    echo -e "${CYAN}======================${NC}"
    [[ "${ok}" -eq 0 ]] && log "✅ 全部检查通过" || err "存在未通过项，请按上方提示修复"
    return "${ok}"
}

# 入口
main() {
    case "${1:-}" in
        -h|--help|help) usage ;;
        --install-cron)   do_install_cron ;;
        --uninstall-cron) do_uninstall_cron ;;
        --cron-status)    do_cron_status ;;
        --list)           do_list ;;
        --verify)         do_verify ;;
        --doctor)         do_doctor ;;
        --restore)        [[ -n "${2:-}" ]] || { err "用法: $0 --restore <备份文件>"; exit 1; }; do_restore "$2" ;;
        "")               do_backup ;;
        *)                err "未知参数: $1"; usage ;;
    esac
}

# 单实例锁：cron 与手动执行撞车时，后到的直接退出而不是写出两个半成品
ensure_dirs 2>/dev/null || true
if command -v flock >/dev/null 2>&1; then
    exec 9>"${LOCK_FILE}"
    if ! flock -n 9; then
        err "已有备份进程在运行（锁：${LOCK_FILE}），本次跳过"
        exit 0
    fi
fi

main "$@"
