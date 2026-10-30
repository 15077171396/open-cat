import { useEffect, useRef, useState } from 'react'
import { compactNumber } from './mockData'

interface AnimatedNumberProps {
  value: number
  format: 'compact' | 'percent'
  decimals?: number
  duration?: number
}

/**
 * KPI 数字滚动：从当前显示值平滑过渡到新值。
 *
 * 对齐参考项目 `FaCountTo`：默认 1500ms、`easeOutExpo`（开头极快、尾部收敛）。
 * 为什么不用 CSS 计数器：数值要经过「万」格式化与小数位控制，CSS 只能线性数整数。
 * 尊重 `prefers-reduced-motion`：系统要求减少动态时直接显示终值。
 */
export default function AnimatedNumber({
  value,
  format,
  decimals = 1,
  duration = 1500,
}: AnimatedNumberProps) {
  const [display, setDisplay] = useState(0)
  const displayRef = useRef(0)

  useEffect(() => {
    const reduce =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const from = displayRef.current

    if (reduce || typeof requestAnimationFrame === 'undefined' || from === value) {
      displayRef.current = value
      setDisplay(value)
      return
    }

    let frame = 0
    const start = performance.now()
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / duration)
      const eased = progress >= 1 ? 1 : 1 - Math.pow(2, -10 * progress)
      const next = from + (value - from) * eased
      displayRef.current = next
      setDisplay(next)
      if (progress < 1) {
        frame = requestAnimationFrame(tick)
      } else {
        displayRef.current = value
        setDisplay(value)
      }
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [value, duration])

  return (
    <>{format === 'percent' ? `${display.toFixed(decimals)}%` : compactNumber(Math.round(display))}</>
  )
}
