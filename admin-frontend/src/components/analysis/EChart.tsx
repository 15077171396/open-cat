import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import type { EChartsOption } from 'echarts'
import './echartsSetup'

type EChartsInstance = ReturnType<typeof echarts.init>

interface EChartProps {
  option: EChartsOption
  height?: number
  ariaLabel?: string
  className?: string
}

/**
 * ECharts 容器：初始化 / 尺寸监听 / 卸载销毁，页面只负责给 option。
 *
 * 参考项目（FastapiAdmin）的丝滑感来自 ECharts 内置能力：tooltip 跟随、
 * hover emphasis、饼图 expansion、系列动画错峰。手写 SVG 复刻这些细节不划算，
 * 分析页统一走 ECharts。
 */
export default function EChart({ option, height = 260, ariaLabel, className }: EChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<EChartsInstance | null>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    // 指定初始宽高：jsdom 没有布局，不给宽度会得到 0×0 的画布
    const chart = echarts.init(el, undefined, {
      renderer: 'svg',
      width: el.clientWidth || 600,
      height,
    })
    chartRef.current = chart

    const resize = () => {
      const width = el.clientWidth
      if (width > 0) chart.resize({ width, height })
    }
    // 首帧布局可能还没稳定（移动端尤其明显）：下一帧按真实容器宽度重算一次
    const frame = window.requestAnimationFrame(resize)
    let observer: ResizeObserver | undefined
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(resize)
      observer.observe(el)
    }
    // 窗口尺寸变化始终兜底：ResizeObserver 在部分嵌入环境里不会触发
    window.addEventListener('resize', resize)

    return () => {
      window.cancelAnimationFrame(frame)
      observer?.disconnect()
      window.removeEventListener('resize', resize)
      chart.dispose()
      chartRef.current = null
    }
  }, [height])

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true })
  }, [option])

  return (
    <div
      ref={containerRef}
      className={className}
      style={{ width: '100%', height }}
      role="img"
      aria-label={ariaLabel}
    />
  )
}
