import { describe, expect, it } from 'vitest'
import { modelsForModality } from './modelModality'

const rows = [
  { code: 'deepseek', modality: 'text' },
  { code: 'wanx', modality: 'image' },
]

describe('modelsForModality', () => {
  it('只返回目标模态', () => {
    expect(modelsForModality(rows, 'image').map((r) => r.code)).toEqual(['wanx'])
    expect(modelsForModality(rows, 'text').map((r) => r.code)).toEqual(['deepseek'])
  })
})
