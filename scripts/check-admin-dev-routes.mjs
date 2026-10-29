#!/usr/bin/env node
/**
 * 守卫：admin dev server 实际编译出来的 `App` 是否与磁盘上的 `App.tsx` 一致。
 *
 * 为什么需要它（2026-10-27 一天内踩了两次，症状都不像缓存问题）：
 * - 第一次：源码里有 `const XxxPage = lazy(...)`，浏览器却报 `XxxPage is not defined`，整页白屏；
 * - 第二次：源码里有 `<Route path="/payment-settings">`，点菜单却跳回数据看板
 *   —— 因为编译结果里没有这条路由，`navigate()` 落到了兜底的 `*` → `/`。
 *
 * 两次都是 Vite 的增量编译结果陈旧（磁盘对、dev server 返回旧版本），
 * `tsc` / `vitest` / `vite build` 全部通过，只有浏览器出错。
 * 本脚本把「怎么判断」固化成命令，不必再去 curl 手工数关键字。
 *
 * 用法：
 *   node scripts/check-admin-dev-routes.mjs            # 默认 http://localhost:5174
 *   ADMIN_DEV_URL=http://localhost:5174 node ...       # 指定端口
 *
 * 退出码：0 = 一致或 dev server 未运行（跳过）；1 = dev server 明显陈旧。
 */

import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const ENTRY = join(ROOT, 'admin-frontend', 'src', 'App.tsx')
const BASE = (process.env.ADMIN_DEV_URL || 'http://localhost:5174').replace(/\/+$/, '')
const REL = '/admin/src/App.tsx'

/** 磁盘上声明的路由（`<Route path="...">`，含跨行写法） */
function diskRoutes(source) {
  return [...source.matchAll(/<Route\s+path="([^"]+)"/g)].map((m) => m[1])
}

const disk = diskRoutes(readFileSync(ENTRY, 'utf8'))
if (!disk.length) {
  console.error('❌ 没从 App.tsx 解析出任何 <Route path>，守卫需重写（正则失效）')
  process.exit(1)
}

let code
try {
  const res = await fetch(`${BASE}${REL}`, { headers: { accept: '*/*' } })
  if (!res.ok) {
    console.error(`❌ dev server 返回 ${res.status}（${BASE}${REL}）`)
    process.exit(1)
  }
  code = await res.text()
} catch {
  // 没起 dev server 不算失败：本脚本是「起了才校验」的旁路检查
  console.log(`⏭  未连上 ${BASE}，跳过（admin dev server 未运行）`)
  process.exit(0)
}

// 编译结果里路由字段是 `path: "/xxx",`；* 是兜底路由，不必存在
const missing = disk.filter((p) => p !== '*' && !code.includes(`path: "${p}"`))
// 反向也查：编译结果里有、磁盘没了 → 同样说明 dev server 在跑旧代码
const stray = [...code.matchAll(/path: "(\/[^"]*)"/g)]
  .map((m) => m[1])
  .filter((p) => !disk.includes(p) && p !== '/')

if (missing.length || stray.length) {
  if (missing.length) {
    console.error('❌ dev server 的编译结果缺少这些路由（点菜单会跳回数据看板）：')
    for (const p of missing) console.error(`   - ${p}`)
  }
  if (stray.length) {
    console.error('❌ dev server 里还有磁盘上已不存在的路由（跑的是旧代码）：')
    for (const p of stray) console.error(`   + ${p}`)
  }
  console.error('\n修法：重启 admin dev server（Vite 是父子进程，要杀整棵树）：')
  console.error('   taskkill /PID <vite 父进程> /T /F')
  console.error('   cd admin-frontend && node ./node_modules/vite/bin/vite.js')
  process.exit(1)
}

console.log(`✅ admin dev server 路由与磁盘一致（${disk.length} 条）`)
