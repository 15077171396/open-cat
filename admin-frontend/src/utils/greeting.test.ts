import { describe, expect, it } from 'vitest'
import { greetingKey } from './greeting'

describe('greetingKey', () => {
  it('按小时返回早上/下午/晚上', () => {
    expect(greetingKey(new Date('2026-10-31T08:00:00'))).toBe('morning')
    expect(greetingKey(new Date('2026-10-31T13:00:00'))).toBe('afternoon')
    expect(greetingKey(new Date('2026-10-31T21:00:00'))).toBe('evening')
  })

  it('边界：12 点算下午，18 点算晚上', () => {
    expect(greetingKey(new Date('2026-10-31T12:00:00'))).toBe('afternoon')
    expect(greetingKey(new Date('2026-10-31T18:00:00'))).toBe('evening')
  })
})
