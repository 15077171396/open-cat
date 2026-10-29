#!/usr/bin/env bash
# 登录管理后台并把会话留在浏览器里（供后续截图脚本复用）。
# 用法：bash scripts/admin-login.sh
# 凭据从环境变量读取，不写进仓库：
#   ADMIN_ACCOUNT=... ADMIN_PASSWORD=... bash scripts/admin-login.sh
set -u

: "${ADMIN_ACCOUNT:?需要设置 ADMIN_ACCOUNT}"
: "${ADMIN_PASSWORD:?需要设置 ADMIN_PASSWORD}"

BASE="http://localhost:5174/admin"

agent-browser open "${BASE}/" >/dev/null 2>&1
sleep 5
# 用 id 选择器：antd 表单的 placeholder 是「手机号 / 邮箱」「请输入密码」，
# 「账号 / 密码」只是 Form.Item 的 label 文本，按 placeholder 匹配会找不到元素
agent-browser fill '#account' "${ADMIN_ACCOUNT}" >/dev/null 2>&1
agent-browser fill '#password' "${ADMIN_PASSWORD}" >/dev/null 2>&1
agent-browser click 'button[type=submit]' >/dev/null 2>&1
sleep 7

url="$(agent-browser get url 2>/dev/null || true)"
snap="$(agent-browser snapshot 2>/dev/null || true)"

case "${url}" in
  *"/admin/login"*|"")
    echo "FAIL 仍停留在登录页（url=${url}）"
    printf '%s\n' "${snap}" | head -10
    exit 1
    ;;
esac

case "${snap}" in
  *"页面发生异常"*|*"(empty page)"*)
    echo "FAIL 登录后页面异常（url=${url}）"
    exit 1
    ;;
esac

echo "OK 已登录（url=${url}）"
