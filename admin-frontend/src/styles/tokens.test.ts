// @ts-expect-error 本工程未安装 @types/node；Vitest 运行在 Node 环境，运行时可用
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import themeRaw from '../theme.ts?raw'

declare const process: { cwd(): string }

/**
 * token 同步守卫。
 *
 * theme.ts 用 `?raw` 读取；tokens.css 用 node:fs 直接读磁盘
 * （Vitest 对 CSS 的 `?raw` 会返回空字符串，不能用来做文本断言）。
 */
const themeSource = themeRaw.toLowerCase()
const tokenSource = readFileSync(
  `${process.cwd()}/src/styles/sections/tokens.css`,
  'utf8',
).toLowerCase()
const lightSource = tokenSource.split("[data-theme='dark']")[0]
const darkSource = tokenSource.split("[data-theme='dark']")[1] ?? ''

describe('tokens.css 与 theme.ts 同步', () => {
  it('亮色品牌/表面/文字 token 一致', () => {
    expect(themeSource).toContain('#165dff')
    expect(themeSource).toContain('#f5f7fd')
    expect(themeSource).toContain('#121314')
    expect(themeSource).toContain('#e5e6eb')
    expect(themeSource).toContain('borderradius: 4')

    expect(lightSource).toContain('--app-primary: #165dff')
    expect(lightSource).toContain('--app-canvas: #f5f7fd')
    expect(lightSource).toContain('--app-text: #121314')
    expect(lightSource).toContain('--app-border: #e5e6eb')
    expect(lightSource).toContain('--app-radius: 4px')
    expect(lightSource).toContain('--app-tabs-h: 32px')
  })

  it('暗色主色与画布独立覆盖', () => {
    expect(darkSource).toContain('--app-primary: #4080ff')
    expect(darkSource).toContain('--app-canvas: #0f1115')
  })

  it('不残留旧靛蓝主色', () => {
    expect(lightSource).not.toContain('--app-primary: var(--app-primary)')
  })
})
