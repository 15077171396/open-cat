/**
 * Markdown / HTML → 消毒 HTML 的回归测试（内容导入链路入口，重点钉死消毒不能被绕过）。
 */

import { describe, expect, it } from 'vitest'

import { markdownToHtml, markdownToSafeHtml, normalizeTableMarkup } from './markdown'

describe('markdownToHtml', () => {
  it('空输入返回空串', () => {
    expect(markdownToHtml('')).toBe('')
  })

  it('把 Markdown 转为 HTML', () => {
    expect(markdownToHtml('# 标题')).toContain('<h1>')
    expect(markdownToHtml('**粗体**')).toContain('<strong>')
    expect(markdownToHtml('- a\n- b')).toContain('<li>')
  })

  it('HTML 块原样透传（与 GitHub 行为一致，README 徽章依赖它）', () => {
    const out = markdownToHtml('<p align="center"><img src="https://x/y.svg"></p>')
    expect(out).toContain('align="center"')
  })

  it('XSS：script 与 on* 事件被清除', () => {
    const out = markdownToHtml('<script>alert(1)</script>\n\n正文')
    expect(out).not.toContain('<script')
    expect(out).toContain('正文')

    const evt = markdownToHtml('<img src="x" onerror="alert(1)">')
    expect(evt).not.toContain('onerror')
  })
})

describe('表格导入（表头不能被隔开）', () => {
  const MD = [
    '| 能力 | 选型 | 作用 |',
    '| --- | --- | --- |',
    '| UI 自动化执行 | `@midscene/web` | 驱动浏览器 |',
    '| 脚本运行 | `tsx` | 直接运行测试文件 |',
  ].join('\n')

  it('marked 原始输出会在 thead 与 tbody 之间留换行（问题来源）', () => {
    // 这条断言锁的是「为什么需要 normalizeTableMarkup」：
    // 该换行会被 quill-table-better 当成表格内的一行 → 表头被劈成独立表格
    expect(normalizeTableMarkup('<table><thead><tr><th>a</th></tr></thead>\n<tbody><tr><td>b</td></tr></tbody></table>'))
      .toBe('<table><thead><tr><th>a</th></tr></thead><tbody><tr><td>b</td></tr></tbody></table>')
  })

  it('转换后的表格结构标签之间没有空白文本节点', () => {
    const html = markdownToHtml(MD)
    expect(html).not.toMatch(/<\/thead>\s+<tbody>/)
    expect(html).not.toMatch(/<\/tr>\s+<tr>/)
  })

  it('表格仍是一整张，表头与表体都在', () => {
    const html = markdownToHtml(MD)
    expect(html.match(/<table/g)?.length).toBe(1)
    expect(html).toContain('<thead>')
    expect(html).toContain('<tbody>')
    expect(html).toContain('能力')
    expect(html).toContain('直接运行测试文件')
  })

  it('不误伤单元格内部的行内间距', () => {
    const html = markdownToHtml('| a | b |\n| --- | --- |\n| <b>x</b> <code>y</code> | 2 |')
    expect(html).toContain('<b>x</b> <code>y</code>')
  })

  it('直传 HTML 表格（走 HTML 分支）同样归一整', () => {
    const out = markdownToSafeHtml(
      '<table><thead><tr><th>a</th></tr></thead>\n<tbody><tr><td>b</td></tr></tbody></table>',
    )
    expect(out).not.toMatch(/<\/thead>\s+<tbody>/)
    expect(out.match(/<table/g)?.length).toBe(1)
  })
})

describe('markdownToSafeHtml', () => {
  it('空输入返回空串', () => {
    expect(markdownToSafeHtml('')).toBe('')
  })

  it('以 HTML 标签开头时走 HTML 分支，不做 Markdown 转换', () => {
    // 下划线在 Markdown 里会变 <em>，走 HTML 分支应保持原样
    const out = markdownToSafeHtml('<div>a_b_c</div>')
    expect(out).toContain('a_b_c')
    expect(out).not.toContain('<em>')
  })

  it('以纯文本 / Markdown 开头时走 Markdown 分支', () => {
    expect(markdownToSafeHtml('# 标题')).toContain('<h1>')
    expect(markdownToSafeHtml('普通段落**加粗**')).toContain('<strong>')
  })

  it('HTML 分支同样消毒', () => {
    const out = markdownToSafeHtml('<p>安全</p><script>alert(1)</script>')
    expect(out).not.toContain('<script')
    expect(out).toContain('安全')
  })

  it('HTML 分支：伪装成合法标签的 XSS 载荷也被清除', () => {
    expect(markdownToSafeHtml('<img src=x onerror=alert(1)>')).not.toContain('onerror')
    expect(markdownToSafeHtml('<iframe src="javascript:alert(1)"></iframe>')).not.toContain('<iframe')
  })

  it('Markdown 分支：内嵌的 XSS 载荷也被清除', () => {
    const out = markdownToSafeHtml('正文\n\n<img src=x onerror=alert(1)>')
    expect(out).not.toContain('onerror')
  })

  it('两条分支对同一段合法内容都不丢主体文字', () => {
    expect(markdownToSafeHtml('<p>hello</p>')).toContain('hello')
    expect(markdownToSafeHtml('hello')).toContain('hello')
  })
})
