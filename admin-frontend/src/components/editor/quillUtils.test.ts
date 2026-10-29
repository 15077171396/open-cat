/**
 * 编辑器纯逻辑的回归测试：钉住不依赖 Quill 内核的纯函数，为重构提供安全网。
 * 刻意不断言 Quill 自身行为（选区/Delta/Range）：jsdom 支持不完整，断言只会得到假结果。
 */

import { describe, expect, it, vi } from 'vitest'
import type { Quill } from 'react-quill-new'

import {
  BASE64_IMG_REGEX,
  DOMPURIFY_CONFIG,
  INSERT_PLACEHOLDER,
  PREVIEW_SANITIZE_CONFIG,
  SHORTCUT_GROUPS,
  VIDEO_HINT,
  collectMatches,
  computeStats,
  extractImageFiles,
  formatBytes,
  locateUploadPlaceholder,
  readableKeys,
  replaceBase64ImagesWithUrls,
  resolveSize,
  sanitizePreviewHtml,
} from './quillUtils'

describe('resolveSize', () => {
  it('undefined / null 时回落到默认值', () => {
    expect(resolveSize(undefined, '400px')).toBe('400px')
    // @ts-expect-error 显式覆盖 null 分支（运行时可能来自后端脏数据）
    expect(resolveSize(null, '400px')).toBe('400px')
  })

  it('数字补 px，字符串原样保留', () => {
    expect(resolveSize(600, '400px')).toBe('600px')
    expect(resolveSize('60vh', '400px')).toBe('60vh')
  })
})

describe('formatBytes', () => {
  it('空值返回空串（附件卡片据此不渲染体积）', () => {
    expect(formatBytes(0)).toBe('')
  })

  it('小于 1KB 用 B 且取整', () => {
    expect(formatBytes(512)).toBe('512 B')
  })

  it('1KB~10KB 保留一位小数，≥10 取整', () => {
    expect(formatBytes(1024)).toBe('1.0 KB')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(10240)).toBe('10 KB')
  })

  it('逐级进位到 MB / GB', () => {
    expect(formatBytes(1024 * 1024)).toBe('1.0 MB')
    expect(formatBytes(1024 ** 3)).toBe('1.0 GB')
  })
})

describe('extractImageFiles', () => {
  it('null / undefined 返回空数组', () => {
    expect(extractImageFiles(null)).toEqual([])
    expect(extractImageFiles(undefined)).toEqual([])
  })

  it('只保留 image/*，剔除其它类型', () => {
    const files = [
      new File(['a'], 'a.png', { type: 'image/png' }),
      new File(['b'], 'b.txt', { type: 'text/plain' }),
      new File(['c'], 'c.webp', { type: 'image/webp' }),
    ]
    const picked = extractImageFiles(files as unknown as FileList)
    expect(picked.map((f) => f.name)).toEqual(['a.png', 'c.webp'])
  })
})

describe('computeStats', () => {
  it('空内容全部为 0', () => {
    expect(computeStats('')).toEqual({ chars: 0, words: 0, blocks: 0, minutes: 0 })
  })

  it('script / style 的内容不计入统计', () => {
    const withScript = computeStats('<p>正文</p><script>var a=1</script>')
    const plain = computeStats('<p>正文</p>')
    expect(withScript.chars).toBe(plain.chars)
    expect(withScript.blocks).toBe(plain.blocks)
  })

  it('chars 不计空白，blocks 统计块级元素', () => {
    const s = computeStats('<p>a b</p><h2>标题</h2><ul><li>x</li></ul>')
    expect(s.chars).toBe(5) // a、b、标、题、x（空格不计）
    expect(s.blocks).toBe(3) // p + h2 + li
  })

  it('中英文都计入可读时长，且至少有 1 分钟', () => {
    const s = computeStats('<p>中文 English words here</p>')
    expect(s.minutes).toBe(1)
    expect(s.words).toBe(3) // English / words / here
  })

  it('长文本按时长估算向上取整', () => {
    const s = computeStats(`<p>${'猫'.repeat(900)}</p>`)
    expect(s.minutes).toBe(3) // ceil(900/400)
  })
})

describe('readableKeys', () => {
  it('把紧凑键位展开为易读写法', () => {
    expect(readableKeys('Ctrl+Alt+1~6')).toBe('Ctrl + Alt + 1 ~ 6')
  })
})

describe('collectMatches', () => {
  /**
   * 最小 Quill 桩：只需 getLength / getLines / getIndex 三个方法。
   * 每行额外携带 `docIndex`，用来断言「结果索引 = 行首索引 + 行内偏移」。
   */
  function makeQuill(
    lines: { text: string; docIndex: number }[],
  ): Quill {
    return {
      getLength: () => lines.reduce((n, l) => n + l.text.length + 1, 0),
      getLines: () =>
        lines.map((l) => ({ domNode: { textContent: l.text }, ...l })) as unknown as {
          domNode: HTMLElement
        }[],
      getIndex: (line: unknown) => (line as { docIndex: number }).docIndex,
    } as unknown as Quill
  }

  it('空关键字返回空数组', () => {
    expect(collectMatches(makeQuill([{ text: 'abc', docIndex: 0 }]), '', false)).toEqual([])
  })

  it('逐行查找并叠加行的文档起始索引（不能只用行内偏移）', () => {
    // 若实现退化为「只取行内偏移」，第二行的命中会错误地报告 index=0
    const found = collectMatches(
      makeQuill([
        { text: 'abc', docIndex: 0 },
        { text: 'abc', docIndex: 5 },
      ]),
      'abc',
      false,
    )
    expect(found).toEqual([
      { index: 0, length: 3 },
      { index: 5, length: 3 },
    ])
  })

  it('同一行内多次命中都要产出，且位置递进', () => {
    const found = collectMatches(makeQuill([{ text: 'abc-abc', docIndex: 7 }]), 'abc', false)
    expect(found).toEqual([
      { index: 7, length: 3 },
      { index: 11, length: 3 },
    ])
  })

  it('大小写敏感可切换', () => {
    const q = makeQuill([{ text: 'Abc abc', docIndex: 0 }])
    expect(collectMatches(q, 'abc', false)).toHaveLength(2)
    expect(collectMatches(q, 'abc', true)).toHaveLength(1)
  })

  it('getLines 抛错时返回空数组而不是向上抛（编辑器未就绪时不该崩）', () => {
    const stub = {
      getLength: () => 0,
      getLines: () => {
        throw new Error('editor not ready')
      },
      getIndex: () => 0,
    } as unknown as Quill
    expect(collectMatches(stub, 'x', false)).toEqual([])
  })
})

describe('BASE64_IMG_REGEX 与 replaceBase64ImagesWithUrls', () => {
  const PNG_B64 =
    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=='

  it('正则可匹配 base64 图片标签', () => {
    BASE64_IMG_REGEX.lastIndex = 0
    const html = `<p>x</p><img src="${PNG_B64}" alt="" />`
    expect(BASE64_IMG_REGEX.test(html)).toBe(true)
    BASE64_IMG_REGEX.lastIndex = 0
  })

  it('无 base64 图片时原样返回，且不调用上传', async () => {
    const uploadFn = vi.fn()
    const html = '<p>纯文本</p>'
    await expect(replaceBase64ImagesWithUrls(html, uploadFn)).resolves.toBe(html)
    expect(uploadFn).not.toHaveBeenCalled()
  })

  it('把 base64 图片替换为上传后的远程 URL', async () => {
    const uploadFn = vi.fn().mockResolvedValue('https://cdn.example.com/a.png')
    const html = `<p>x</p><img src="${PNG_B64}" alt="" />`
    const out = await replaceBase64ImagesWithUrls(html, uploadFn)
    expect(uploadFn).toHaveBeenCalledTimes(1)
    expect(out).toContain('https://cdn.example.com/a.png')
    expect(out).not.toContain('base64,')
  })

  it('上传失败时保留原标签（不丢内容）', async () => {
    const uploadFn = vi.fn().mockRejectedValue(new Error('network'))
    const html = `<img src="${PNG_B64}" alt="" />`
    const out = await replaceBase64ImagesWithUrls(html, uploadFn)
    expect(out).toBe(html)
  })

  it('连续多次调用互不干扰（模块级 g 正则的 lastIndex 必须重置）', async () => {
    const uploadFn = vi.fn().mockResolvedValue('https://cdn.example.com/x.png')
    const html = `<img src="${PNG_B64}" alt="" />`
    await replaceBase64ImagesWithUrls(html, uploadFn)
    const second = await replaceBase64ImagesWithUrls(html, uploadFn)
    // 若 lastIndex 未重置，第二次会从中间位置开始扫描而漏掉首个（也是唯一）标签
    expect(second).toContain('https://cdn.example.com/x.png')
  })
})

describe('导出常量（供工具栏与提示复用）', () => {
  it('快捷键表结构稳定', () => {
    expect(Array.isArray(SHORTCUT_GROUPS)).toBe(true)
    expect(SHORTCUT_GROUPS.length).toBeGreaterThan(0)
    for (const group of SHORTCUT_GROUPS) {
      expect(typeof group.title).toBe('string')
      expect(Array.isArray(group.items)).toBe(true)
      for (const [keys, desc] of group.items) {
        expect(typeof keys).toBe('string')
        expect(typeof desc).toBe('string')
      }
    }
  })

  it('视频提示涵盖已支持的平台', () => {
    expect(VIDEO_HINT).toContain('哔哩哔哩')
    expect(VIDEO_HINT).toContain('YouTube')
  })

  it('图片占位文案保持稳定（上传流程靠它定位占位元素）', () => {
    expect(INSERT_PLACEHOLDER).toBe('图片上传中...')
  })

  it('消毒配置：通用配置禁用 script，预览配置额外放行 iframe', () => {
    expect(DOMPURIFY_CONFIG.FORBID_TAGS).toContain('script')
    expect(DOMPURIFY_CONFIG.FORBID_TAGS).toContain('iframe')
    // 预览会放行 iframe（视频），但 script 必须始终禁用
    expect(PREVIEW_SANITIZE_CONFIG.ADD_TAGS).toContain('iframe')
    expect(PREVIEW_SANITIZE_CONFIG.FORBID_TAGS).toContain('script')
  })
})

describe('sanitizePreviewHtml（iframe 域名白名单，P1-46④）', () => {
  it('放行白名单内的 https 播放器 iframe', () => {
    const out = sanitizePreviewHtml(
      '<p>x</p><iframe src="https://player.bilibili.com/player.html?bvid=BV1"></iframe>',
    )
    expect(out).toContain('<iframe')
    expect(out).toContain('player.bilibili.com')
  })

  it('整块移除白名单外的 iframe —— 源码模式可直插任意 iframe', () => {
    const out = sanitizePreviewHtml(
      '<p>x</p><iframe src="https://evil.example.com/phish"></iframe>',
    )
    expect(out).not.toContain('iframe')
    expect(out).not.toContain('evil.example.com')
    expect(out).toContain('<p>x</p>')
  })

  it('拒绝 http 与协议相对地址（否则可借本站协议绕过 https 校验）', () => {
    expect(
      sanitizePreviewHtml('<iframe src="http://player.bilibili.com/x"></iframe>'),
    ).not.toContain('iframe')
    expect(
      sanitizePreviewHtml('<iframe src="//player.bilibili.com/x"></iframe>'),
    ).not.toContain('iframe')
  })

  it('脚本标签仍然被剥离（白名单只对 iframe 放行）', () => {
    expect(sanitizePreviewHtml('<script>alert(1)</script>')).not.toContain('script')
  })
})

describe('locateUploadPlaceholder', () => {
  it('按文本定位：上传期间作者新增的文字不会让它指错位置（P0-13）', () => {
    // 作者点了「图片」→ 占位插在索引 5 → 上传没回来前他又打了「后面有字」
    // 若仍按插入时捕获的索引删除 INSERT_PLACEHOLDER.length 个字符，吃掉的就是新写的字
    const text = `你好世界${INSERT_PLACEHOLDER}后面有字`
    expect(locateUploadPlaceholder(text)).toBe(4)
  })

  it('占位已不存在时返回 -1（调用方据此只插图、不删除任何内容）', () => {
    expect(locateUploadPlaceholder('作者自己把占位删掉了')).toBe(-1)
    expect(locateUploadPlaceholder('')).toBe(-1)
  })

  it('定位 + 删除该长度 = 原文两侧完整保留', () => {
    const text = `前文${INSERT_PLACEHOLDER}后文`
    const at = locateUploadPlaceholder(text)
    expect(text.slice(0, at) + text.slice(at + INSERT_PLACEHOLDER.length)).toBe('前文后文')
  })
})
