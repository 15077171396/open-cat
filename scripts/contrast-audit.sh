#!/usr/bin/env bash
# 对比度审计：对给定页面注入 WCAG 审计脚本，输出不合格的文本节点。
# 用法：bash scripts/contrast-audit.sh <slug|url> [more...]
#   - slug 以 admin- 开头时先登录管理后台
# 判定：正文（<18.66px）需 4.5:1，大字号需 3:1；跳过压在图片上的文字。
set -u
JS="$(dirname "${BASH_SOURCE[0]}")/contrast-audit.js"

case "${1:-}" in
  admin-*) bash "$(dirname "${BASH_SOURCE[0]}")/admin-login.sh" >/dev/null 2>&1 || { echo "登录失败"; exit 1; } ;;
esac

for target in "$@"; do
  url="${target#*|}"; slug="${target%%|*}"
  agent-browser open "${url}" >/dev/null 2>&1
  sleep 4
  echo "=== ${slug} ==="
  agent-browser eval "$(cat "${JS}")" 2>/dev/null | head -c 4000
  echo
done
