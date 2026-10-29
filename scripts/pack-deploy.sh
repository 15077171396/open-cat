#!/usr/bin/env bash
# 生成部署包：只打「服务器构建镜像 + 部署」需要的、且**已被 git 提交**的文件。
#
# 用法：
#   bash scripts/pack-deploy.sh              # 输出 ./ai-cat-deploy.tar.gz
#   bash scripts/pack-deploy.sh /tmp/x.tar.gz
#
# ── 为什么用 `git archive` 而不是 `tar -T`（2026-10-21 线上事故）──
#   之前按 `git ls-files` 列清单再 `tar -T` 打包，打的是**工作区文件**。
#   Windows 上 `core.autocrlf=true`，工作区里 `.sh` 全是 CRLF，于是服务器上
#   `bash deploy.sh` 直接报：
#       deploy.sh: line 10: $'\r': command not found
#       : invalid option nameet: pipefail
#   `git archive` 导出的是 **blob 内容**（配合 `.gitattributes` 的 `* text=auto eol=lf`
#   一律 LF），从根上避免这个问题。二进制文件不受影响（.gitattributes 已标 binary）。
#
# ── 为什么只打 HEAD ──
#   `git archive HEAD` 只包含已提交内容。工作区有未提交改动时会打印警告 ——
#   部署包本来就该对应一个确定的提交，别拿半成品上服务器。
#
# ── 排除清单 ──
#   ① 未被引用的设计源图（约 4.8MB）：运行时用的是同名 `.webp`，
#      见 backend/app/services/setting_definitions.py 里 hero_* / star_auth_qrcode 的默认值。
#   ② backend/tests（约 970KB）：服务器只用 app/ alembic/ requirements.txt。
#      ⚠️ 镜像里本来就没有它（backend/.dockerignore 已排除），这里排的是**压缩包**那份。
#   ③ backend/pytest.ini：tests 都不在包里了，这份配置是死文件。
set -euo pipefail

cd "$(dirname "$0")/.."

OUT="${1:-ai-cat-deploy.tar.gz}"

# ---- 白名单：只有这些进包（漏掉 nginx.conf / Dockerfile.nginx 会让 compose 起不来）----
#
# scripts/ 只挑**服务器侧**用得上的，不整目录带：
#   按 scripts/README.md 的分类，`check_*` 是 CI 门禁、`.bat`/`.ps1` 是本机开发工具，
#   带到服务器上既没用又容易让人误以为要在那儿跑。
ITEMS=(
  docker-compose.yml
  deploy.sh
  Dockerfile.nginx
  nginx.conf
  .dockerignore
  .env.example
  backend
  admin-frontend
  # 服务器侧脚本：backup_mysql.sh 被 deploy.sh 强制调用（缺失即中止发版）；
  # watchdog.sh 是要装到服务器 cron 的探活告警；cleanup_orphans.py 针对真实库生成清理 SQL
  scripts/backup_mysql.sh
  scripts/watchdog.sh
  scripts/cleanup_orphans.py
  scripts/README.md
)

EXCLUDES=(
  ':(exclude)admin-frontend/public/logo.png'
  ':(exclude)backend/tests'
  ':(exclude)backend/pytest.ini'
)

for item in "${ITEMS[@]}"; do
  [[ -e "$item" ]] || { echo "[error] 缺少必需条目: $item" >&2; exit 1; }
done

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "[error] 不在 git 仓库内 —— 本脚本按 git 提交内容打包，无法继续" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "[warn] 工作区有未提交改动 —— 部署包只含 HEAD ($(git rev-parse --short HEAD)) 的内容" >&2
fi

echo "[pack] 打包 -> ${OUT}"
# 直接让 git archive 输出 tgz，不经临时文件：Windows 沙箱对 mktemp 出来的路径执行 rm
# 会报 [SAFE_DELETE_INVALID_PATH]（embedded drive prefix），虽不致命但噪音很大。
git archive --format=tgz -o "$OUT" HEAD -- "${ITEMS[@]}" "${EXCLUDES[@]}"

SIZE=$(wc -c <"$OUT")
# 清单只取一次：既省一次解压，也避免 `tar | grep -q` 在 pipefail 下被 SIGPIPE 误判
# （grep -q 命中即退出 → tar 写入端收到 SIGPIPE → 管道整体返回非 0 → 明明有文件也报缺失）
LISTING=$(tar -tzf "$OUT")
COUNT=$(printf '%s\n' "$LISTING" | wc -l)
# 用 awk 换算，不依赖 bc（Git Bash 里常常没有）
awk -v s="$SIZE" -v c="$COUNT" 'BEGIN { printf "[pack] 完成：%.1f MB，%d 个条目\n", s/1048576, c }'

# ---- 自检 1：不该带的东西一个都不能有 ----
BAD=$(printf '%s\n' "$LISTING" | grep -E '(^|/)(node_modules|venv|\.venv|\.git|__pycache__|dist|backups|logs)/|(^|/)\.env$|\.pyc$' || true)
if [[ -n "$BAD" ]]; then
  echo "[error] 包里混入了不该带的内容：" >&2
  echo "$BAD" | head -10 >&2
  exit 1
fi

# ---- 自检 2：部署必需文件必须在 ----
for f in docker-compose.yml deploy.sh Dockerfile.nginx nginx.conf backend/Dockerfile scripts/backup_mysql.sh; do
  # here-string 而不是管道：grep -q 提前退出不会牵连上游
  grep -qx "$f" <<<"$LISTING" || { echo "[error] 包里缺少部署必需文件: $f" >&2; exit 1; }
done

# ---- 自检 3：脚本类文件不得含 CRLF（服务器 bash 会报 $'\r': command not found）----
# 用 `tr -cd '\r' | wc -c` 数字节：`grep $'\r'` 在 Git Bash 里会把参数里的 CR 吃掉，不可靠。
# 直接从 tar 流里 `-O` 读单个文件，不落地解压 —— 避免在 Windows 上 `rm -rf` 临时目录
# 被沙箱的删除保护拦下（[SAFE_DELETE_INVALID_PATH] embedded drive prefix）。
CRLF_BAD=""
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  n=$(tar -xzf "$OUT" -O "$f" | tr -cd '\r' | wc -c | tr -d ' ')
  if [[ "$n" -gt 0 ]]; then
    CRLF_BAD="${CRLF_BAD}  ${f}（${n} 个 CR）"$'\n'
  fi
done < <(printf '%s\n' "$LISTING" | grep -E '(\.sh|Dockerfile[^/]*|\.conf|\.ya?ml)$')

if [[ -n "$CRLF_BAD" ]]; then
  echo "[error] 以下文件是 CRLF 换行，放到 Linux 上会直接报错：" >&2
  printf '%s' "$CRLF_BAD" >&2
  echo "[error] 根因通常是工作区被 core.autocrlf 转成了 CRLF。本脚本用 git archive 导出，理论上不会出现 —— 若出现请检查 .gitattributes 的 * text=auto eol=lf 是否被破坏" >&2
  exit 1
fi

echo "[pack] 自检通过：无 node_modules / venv / .git / __pycache__ / dist / logs / .env / *.pyc"
echo "[pack] 自检通过：部署必需文件齐全（compose / deploy.sh / Dockerfile.nginx / nginx.conf / 两个 Dockerfile / backup_mysql.sh）"
echo "[pack] 自检通过：所有 .sh / Dockerfile / *.conf / *.yml 均为 LF 换行"
