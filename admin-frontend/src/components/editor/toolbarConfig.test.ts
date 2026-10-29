import { describe, expect, it } from 'vitest'
import {
  CUSTOM_ICONS, QUILL_TOOLBAR_CONTAINER, TOOLBAR_TITLES,
} from './toolbarConfig'

describe('toolbarConfig image search entry', () => {
  it('registers a search-image toolbar button next to upload', () => {
    const mediaRow = QUILL_TOOLBAR_CONTAINER.find((row) => row.includes('image'))
    expect(mediaRow).toContain('search-image')
  })

  it('has an icon and a title for the button', () => {
    expect(CUSTOM_ICONS['search-image']).toContain('<svg')
    expect(TOOLBAR_TITLES).toContainEqual(['button.ql-search-image', '搜索图片（图库）'])
  })
})
