/**
 * 生成 TDesign 组件按需打包的 packOptions.ignore 清单：扫描全项目 usingComponents
 * 得到实际使用的组件，沿依赖闭包扩展，未用到的组件目录输出为 ignore JSON，
 * 手动粘回 miniprogram/project.config.json 的 packOptions.ignore。
 * 保守规则：无同名组件 json 的共享库目录（common/mixins 等）一律保留，防止运行时缺文件；
 * locale 已手工加入忽略清单，未来引入多语言/config-provider 时需从 ignore 移除。
 * 用法：node scripts/gen-tdesign-ignore.js（新增 TDesign 组件后重跑一次）
 */
const fs = require('node:fs')
const path = require('node:path')

const ROOT = path.join(__dirname, '..', 'miniprogram')
const TD = path.join(ROOT, 'miniprogram_npm', 'tdesign-miniprogram')

function listJsonFiles(dir) {
  const out = []
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name)
    const st = fs.statSync(p)
    if (st.isDirectory()) out.push(...listJsonFiles(p))
    else if (name.endsWith('.json')) out.push(p)
  }
  return out
}

// 1) seed：扫描 app 侧所有 json 的 usingComponents
const appJsonDirs = [ROOT, path.join(ROOT, 'pages'), path.join(ROOT, 'components'), path.join(ROOT, 'custom-tab-bar')]
const seeds = new Set()
for (const dir of appJsonDirs) {
  if (!fs.existsSync(dir)) continue
  for (const f of listJsonFiles(dir)) {
    let json
    try { json = JSON.parse(fs.readFileSync(f, 'utf8')) } catch (e) { continue }
    for (const val of Object.values(json.usingComponents || {})) {
      const m = String(val).match(/tdesign-miniprogram\/([a-z-]+)\/[a-z-]+/)
      if (m) seeds.add(m[1])
    }
  }
}

// 2) 沿组件间 usingComponents 引用做闭包
const PARENT = '..'
function usingComponentsOf(comp) {
  const compDir = path.join(TD, comp)
  if (!fs.existsSync(compDir)) return []
  const out = []
  for (const f of listJsonFiles(compDir)) {
    let json
    try { json = JSON.parse(fs.readFileSync(f, 'utf8')) } catch (e) { continue }
    for (const val of Object.values(json.usingComponents || {})) {
      const parts = String(val).split('/')
      if (parts[0] === PARENT && parts[1] && /^[a-z-]+$/.test(parts[1])) out.push(parts[1])
    }
  }
  return out
}

const allDirs = fs.readdirSync(TD).filter((n) => fs.statSync(path.join(TD, n)).isDirectory())
const isComponentDir = (d) => fs.existsSync(path.join(TD, d, d + '.json'))

const closure = new Set(seeds)
let queue = [...seeds]
while (queue.length) {
  const comp = queue.pop()
  for (const dep of usingComponentsOf(comp)) {
    if (!closure.has(dep)) {
      closure.add(dep)
      queue.push(dep)
    }
  }
}

// 3) 保留 = 闭包 + 共享库目录；其余输出为 ignore 条目（locale 特殊，见文件头说明）
const LOCALE_IGNORED_MANUALLY = true
const sharedDirs = allDirs.filter((d) => !isComponentDir(d) && d !== 'locale')
const kept = new Set([...closure, ...sharedDirs])
const ignored = allDirs.filter((d) => !kept.has(d))
if (LOCALE_IGNORED_MANUALLY && !ignored.includes('locale')) ignored.push('locale')

function dirSize(p) {
  let total = 0
  for (const name of fs.readdirSync(p)) {
    const fp = path.join(p, name)
    const st = fs.statSync(fp)
    total += st.isDirectory() ? dirSize(fp) : st.size
  }
  return total
}
const keptSize = [...kept].reduce((s, d) => s + dirSize(path.join(TD, d)), 0)
const tdSize = allDirs.reduce((s, d) => s + dirSize(path.join(TD, d)), 0)

console.log('seed components (' + seeds.size + '):', [...seeds].sort().join(', '))
console.log('closure kept (' + closure.size + '):', [...closure].sort().join(', '))
console.log('shared lib dirs kept:', sharedDirs.sort().join(', '))
console.log('tdesign total: ' + (tdSize / 1024).toFixed(0) + ' KB, kept: ' + (keptSize / 1024).toFixed(0) + ' KB, cut: ' + ((tdSize - keptSize) / 1024).toFixed(0) + ' KB')
console.log('\n"ignore": ' + JSON.stringify(ignored.map((d) => ({ type: 'folder', value: 'miniprogram_npm/tdesign-miniprogram/' + d })), null, 2))
