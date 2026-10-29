#!/usr/bin/env node
/**
 * tabBar 路由表一致性守卫：校验 app.json 的 tabBar.list 与 utils/tabbar.js 的 TAB_LIST
 * 在 pagePath 顺序、text、icon 上完全一致。
 * 用法：node scripts/check_tabbar.js；退出码 0 = 一致，1 = 不一致
 */

const fs = require('fs')
const path = require('path')

const REPO_ROOT = path.resolve(__dirname, '..')
const APP_JSON = path.join(REPO_ROOT, 'miniprogram', 'app.json')
const TABBAR_JS = path.join(REPO_ROOT, 'miniprogram', 'utils', 'tabbar.js')

function fail(msg) {
  console.error('✗ ' + msg)
  process.exitCode = 1
}

const appJson = JSON.parse(fs.readFileSync(APP_JSON, 'utf-8'))
const appList = (appJson.tabBar && appJson.tabBar.list) || []

// tabbar.js 是纯 CommonJS（不触 wx API），可直接 require
const { TAB_LIST } = require(TABBAR_JS)

if (appList.length !== TAB_LIST.length) {
  fail(
    `tabBar 数量不一致：app.json=${appList.length} 项，utils/tabbar.js=${TAB_LIST.length} 项`
  )
}

// 归一化：app.json 的 pagePath 无前导斜杠，TAB_LIST 带前导斜杠（switchTab 需要）
const norm = (p) => String(p || '').replace(/^\//, '')

const appPaths = appList.map((i) => norm(i.pagePath))
const jsPaths = TAB_LIST.map((i) => norm(i.pagePath))
if (appPaths.join(',') !== jsPaths.join(',')) {
  fail(
    'pagePath 集合或顺序不一致（顺序不一致 = 高亮与图标错位）：\n' +
      `  app.json      = ${appPaths.join(' -> ')}\n` +
      `  utils/tabbar  = ${jsPaths.join(' -> ')}`
  )
}

appList.forEach((item, idx) => {
  const jsItem = TAB_LIST[idx]
  if (!jsItem) return
  if (item.text !== jsItem.text) {
    fail(`第 ${idx + 1} 项文案不一致：app.json="${item.text}" tabbar.js="${jsItem.text}"`)
  }
})

TAB_LIST.forEach((item, idx) => {
  if (!item.icon || !item.activeIcon) {
    fail(`TAB_LIST[${idx}]（${item.pagePath}）缺少 icon / activeIcon，自定义 tabBar 会渲染空白`)
  }
})

if (!process.exitCode) {
  console.log(`✅ tabBar 路由表一致（${TAB_LIST.length} 项：${jsPaths.join(' / ')}）`)
}