import { describe, expect, it } from 'vitest'
import { ARTICLE_TEXT_LIMIT, pickTutorialArticleText, truncateArticleText } from './text'

describe('truncateArticleText', () => {
  it('未超限时原样返回（不做无意义的改写）', () => {
    expect(truncateArticleText('短正文')).toBe('短正文')
  })

  it('超过 45k 时截断并带标记，总长度不超过上限（后端上限 50k）', () => {
    const text = 'a'.repeat(60_000)
    const out = truncateArticleText(text)
    expect(out.length).toBeLessThanOrEqual(ARTICLE_TEXT_LIMIT)
    expect(out.length).toBeLessThan(50_000)
    expect(out.endsWith('（全文过长，已截断）')).toBe(true)
    expect(out.startsWith('a')).toBe(true)
  })
})

describe('pickTutorialArticleText', () => {
  const full = '整本教程正文'
  const chapter = '当前章节正文'

  it('全文类操作发送拼接后的整篇正文', () => {
    for (const operation of ['summarize', 'seo_description', 'titles', 'keywords'] as const) {
      expect(pickTutorialArticleText(operation, full, chapter)).toBe(full)
    }
  })

  it('选中段落 / 续写等操作只发送当前章节', () => {
    for (const operation of ['rewrite', 'polish', 'expand', 'shorten', 'continue', 'illustrate_prompt'] as const) {
      expect(pickTutorialArticleText(operation, full, chapter)).toBe(chapter)
    }
  })

  it('全文类操作同样受 45k 截断保护（超大教程不会 422）', () => {
    const out = pickTutorialArticleText('summarize', 'b'.repeat(80_000), chapter)
    expect(out.length).toBeLessThanOrEqual(ARTICLE_TEXT_LIMIT)
  })
})
