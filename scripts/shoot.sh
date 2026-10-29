#!/usr/bin/env bash
# 视觉回归截图：把一组页面整页截图到指定目录（文件名 = URL 的 slug）。
#
# 用法：bash scripts/shoot.sh before|after
# 依赖：agent-browser（已全局安装）
set -u

PHASE="${1:-after}"
OUT="E:/wxl-project/ai/trae/ai-cat/.visual/${PHASE}"
mkdir -p "${OUT}"

# 管理端列表页（需要先登录；凭据从环境变量读，不写进仓库）
ADMIN_PAGES=(
  "admin-dashboard|http://localhost:5174/admin/"
  "admin-tutorials|http://localhost:5174/admin/tutorials"
  "admin-resources|http://localhost:5174/admin/resources"
  "admin-nodes|http://localhost:5174/admin/nodes"
  "admin-tools|http://localhost:5174/admin/tools"
  "admin-plugins|http://localhost:5174/admin/plugins"
  "admin-categories|http://localhost:5174/admin/categories"
  "admin-announcements|http://localhost:5174/admin/announcements"
  "admin-users|http://localhost:5174/admin/users"
  "admin-points|http://localhost:5174/admin/points"
  "admin-redeem-codes|http://localhost:5174/admin/redeem-codes"
  "admin-auth-requests|http://localhost:5174/admin/auth-requests"
  "admin-feedback|http://localhost:5174/admin/feedback"
  "admin-comments|http://localhost:5174/admin/comments"
  "admin-settings|http://localhost:5174/admin/settings"
  "admin-roles|http://localhost:5174/admin/roles"
  "admin-departments|http://localhost:5174/admin/departments"
  "admin-audit-logs|http://localhost:5174/admin/audit-logs"
)

# 页面清单：slug|URL（覆盖 5 个列表页 + 首页 + 搜索 + 管理端设置）
PAGES=(
  "home|http://localhost:5173/"
  "nodes|http://localhost:5173/nodes"
  "tutorials|http://localhost:5173/tutorials"
  "tools|http://localhost:5173/tools"
  "resources|http://localhost:5173/resources"
  "plugins|http://localhost:5173/plugins"
  "search|http://localhost:5173/search?q=ComfyUI"
  "admin-settings|http://localhost:5174/admin/settings"
)

# phase 以 admin- 开头时：先登录（凭据走环境变量），再截管理端页面
case "${PHASE}" in
  admin-*)
    PAGES=("${ADMIN_PAGES[@]}")
    bash "$(dirname "${BASH_SOURCE[0]}")/admin-login.sh" || { echo "登录失败，终止"; exit 1; }
    ;;
esac

for item in "${PAGES[@]}"; do
  slug="${item%%|*}"
  url="${item#*|}"
  agent-browser open "${url}" >/dev/null 2>&1
  sleep 4
  agent-browser screenshot --full --screenshot-dir "${OUT}" >/dev/null 2>&1
  # 把最新生成的文件改名成 slug.png（CLI 不支持指定文件名）
  latest="$(ls -t "${OUT}"/screenshot-*.png 2>/dev/null | head -1)"
  if [ -n "${latest}" ]; then
    mv -f "${latest}" "${OUT}/${slug}.png"
  else
    echo "FAIL ${slug}  <- ${url}（截图未生成）"
    continue
  fi

  # ⚠️ 必须检查页面是不是「错误边界」或空页，否则一个崩掉的页面会和另一个崩掉的页面
  # 比出 0.00% 差异 —— 那比没有回归测试更危险（它给你一个「没变化」的假结论）。
  # 这个检查来自一次真实踩坑：清掉 node_modules/.vite 后 dev server 重新预构建，
  # 页面引用的是旧 hash 的依赖，`@ant-design_icons.js?v=...` 返回 504，
  # 懒加载 chunk 失败 → ErrorBoundary 兜底 → 截图与「正常登录页」差异 78%，
  # 但若两侧都崩就会静默通过。
  snapshot="$(agent-browser snapshot 2>/dev/null || true)"
  case "${snapshot}" in
    *"页面发生异常"*|*"(empty page)"*)
      echo "FAIL ${slug}  <- ${url}（页面异常/空白，见上方快照）"
      continue
      ;;
  esac
  echo "ok  ${slug}  <- ${url}"
done

agent-browser close >/dev/null 2>&1
echo "done -> ${OUT}"
