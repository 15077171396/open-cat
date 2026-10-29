import { memo, useMemo } from 'react'
import { Skeleton } from 'antd'

export interface BarItem {
  /** React key（用稳定业务标识，不要用下标） */
  key: string
  label: string
  value: number
  /** 右侧次要说明，如「浏览 99」 */
  extra?: string
  /** 条形填充色；不传则用统一品牌蓝 */
  color?: string
}

interface BarListProps {
  items: BarItem[]
  /**
   * 条形长度基准。不传则取 items 里的最大值。
   * 显式传入的场景：希望多条榜单共用同一比例尺时。
   */
  max?: number
  emptyText?: string
  /** 是否显示 1 / 2 / 3 名次徽标 */
  ranked?: boolean
  loading?: boolean
}

const DEFAULT_COLOR = '#1677ff'

/** 名次徽标样式：前三名给区分色，其余保持中性灰 */
function rankClass(index: number): string {
  if (index === 0) return 'dash-bars__rank dash-bars__rank--1'
  if (index === 1) return 'dash-bars__rank dash-bars__rank--2'
  if (index === 2) return 'dash-bars__rank dash-bars__rank--3'
  return 'dash-bars__rank'
}

/**
 * 横向条形榜：内容资产总览与资源下载榜共用。
 * 纯 DOM + CSS 宽度实现（不用 SVG），文字天然清晰、可选中，也便于加 title 提示。
 */
function BarListInner({
  items,
  max,
  emptyText = '暂无数据',
  ranked = false,
  loading = false,
}: BarListProps) {
  // 基准值兜底：全 0 时用 1，避免 value / 0 → NaN → width:NaN%
  const basis = useMemo(() => {
    const provided = typeof max === 'number' && Number.isFinite(max) ? max : undefined
    const own = items.length ? Math.max(...items.map((it) => it.value)) : 0
    return Math.max(1, provided ?? own)
  }, [items, max])

  if (loading) {
    return (
      <div className="dash-bars">
        <Skeleton active paragraph={{ rows: 4 }} title={false} />
      </div>
    )
  }

  if (items.length === 0) {
    return <div className="dash-bars__empty">{emptyText}</div>
  }

  return (
    <div className="dash-bars">
      {items.map((it, index) => {
        const ratio = Math.min(1, Math.max(0, it.value / basis))
        // 有值但比例极小时给 2% 的可见宽度，否则条形会细成一根发丝看不见
        const widthPct = it.value > 0 ? Math.max(2, ratio * 100) : 0
        return (
          <div className="dash-bars__row" key={it.key}>
            <div className="dash-bars__head">
              {ranked && <span className={rankClass(index)}>{index + 1}</span>}
              <span className="dash-bars__label" title={it.label}>
                {it.label}
              </span>
              {it.extra ? <span className="dash-bars__extra">{it.extra}</span> : null}
              <span className="dash-bars__value">{it.value}</span>
            </div>
            <div className="dash-bars__track">
              <div
                className="dash-bars__fill"
                style={{ width: `${widthPct}%`, background: it.color ?? DEFAULT_COLOR }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default memo(BarListInner)
