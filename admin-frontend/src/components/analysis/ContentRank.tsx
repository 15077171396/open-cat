import type { ContentRow } from './mockData'
import { compactNumber } from './mockData'

interface ContentRankProps {
  rows: ContentRow[]
}

/**
 * 内容表现榜：阅读量条 + 完读率 + 环比。
 * 条长按本榜最大阅读量归一，避免不同时间范围（7/30/90 天）下长度失衡。
 */
export default function ContentRank({ rows }: ContentRankProps) {
  const maxViews = Math.max(1, ...rows.map((row) => row.views))

  return (
    <ol className="ana-rank">
      {rows.map((row, index) => {
        const width = Math.max(3, (row.views / maxViews) * 100)
        const up = row.delta >= 0
        return (
          <li className="ana-rank__row" key={row.id}>
            <span className={`ana-rank__no${index < 3 ? ` ana-rank__no--${index + 1}` : ''}`}>
              {String(index + 1).padStart(2, '0')}
            </span>
            <div className="ana-rank__body">
              <div className="ana-rank__title-line">
                <span className="ana-rank__title" title={row.title}>
                  {row.title}
                </span>
                <span className="ana-rank__views">{compactNumber(row.views)}</span>
              </div>
              <div className="ana-rank__meta-line">
                <span className="ana-rank__tag">{row.category}</span>
                <span className="ana-rank__track" aria-hidden="true">
                  <span className="ana-rank__fill" style={{ width: `${width}%` }} />
                </span>
                <span className="ana-rank__completion">完读 {row.completion}%</span>
              </div>
            </div>
            <span className={`ana-rank__delta${up ? ' is-up' : ' is-down'}`}>
              {up ? '+' : ''}
              {row.delta}%
            </span>
          </li>
        )
      })}
    </ol>
  )
}
