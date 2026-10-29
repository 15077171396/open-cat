#!/usr/bin/env bash
# 空态功能校验：登录 → 打开指定后台列表页 → 输入无结果关键词 → 断言空态文案与按钮。
# 用法：bash scripts/check-admin-empty.sh <slug> <url> <filteredText>
set -u
SLUG="$1"; URL="$2"; EXPECT="$3"

bash "$(dirname "${BASH_SOURCE[0]}")/admin-login.sh" >/dev/null 2>&1 || { echo "FAIL ${SLUG}: 登录失败"; exit 1; }
agent-browser open "${URL}" >/dev/null 2>&1
sleep 6

# 在第一个搜索框里输入一个必然无结果的词
# 必须用 type（真实按键）而不是 fill：fill 直接设 value，不触发 React 的 onChange，
# 受控输入的搜索框不会更新状态（踩过：页面仍显示全部数据）
agent-browser click 'input[placeholder*="搜索"]' >/dev/null 2>&1
agent-browser keyboard type 'zzzzzznotexist' >/dev/null 2>&1
sleep 6

# 用 innerText 而不是可访问性快照：快照在页面重渲染期间可能只抓到侧边栏
snap="$(agent-browser eval 'document.body.innerText' 2>/dev/null || true)"
case "${snap}" in
  *"${EXPECT}"*) echo "OK   ${SLUG}: 空态文案正确（${EXPECT}）" ;;
  *) echo "FAIL ${SLUG}: 未出现空态文案「${EXPECT}」"; printf '%s\n' "${snap}" | tail -8 ;;
esac

case "${snap}" in
  *"清空筛选条件"*) echo "OK   ${SLUG}: 有「清空筛选条件」按钮" ;;
  *) echo "WARN ${SLUG}: 未见「清空筛选条件」按钮（该页可能无筛选态）" ;;
esac
