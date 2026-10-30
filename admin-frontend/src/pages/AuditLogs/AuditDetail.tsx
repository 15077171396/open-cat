import { Typography } from 'antd'

/**
 * 审计明细：把 `detail.changes` 渲染成字段级「旧值 → 新值」，而不是甩一段 JSON。
 *
 * 后端有两种历史写法需要兼容：
 * - `changes: { field: [old, new] }` —— 标准形状（教程 / 公告 / 反馈 / 用户 / 角色 / 套餐 / 模型）；
 * - `added` / `removed` —— 权限集合的增删（没有单一旧值与新值）；
 * - `from` / `to` —— 定时任务配置的计划快照。
 *
 * 结构判断全部在前端做，后端不再为展示重复存一份文案；无法识别的旧记录回退为键值表。
 */

interface AuditDetailSource {
  ip?: string | null
  request_id?: string | null
  user_agent?: string | null
}

interface AuditDetailProps {
  detail: Record<string, unknown> | null
  source?: AuditDetailSource
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function isPair(value: unknown): value is [unknown, unknown] {
  return Array.isArray(value) && value.length === 2
}

function display(value: unknown): string {
  if (value === null || value === undefined) return '（空）'
  if (typeof value === 'string') return value === '' ? '（空字符串）' : value
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'number') return String(value)
  return JSON.stringify(value)
}

function collectChanges(detail: Record<string, unknown>): Record<string, [unknown, unknown]> {
  const collected: Record<string, [unknown, unknown]> = {}
  if (isRecord(detail.changes)) {
    for (const [field, pair] of Object.entries(detail.changes)) {
      if (isPair(pair)) collected[field] = pair
    }
  }
  // 定时任务配置：from/to 是两份完整配置快照，只取真正变化的键
  if (isRecord(detail.from) && isRecord(detail.to)) {
    const keys = new Set([...Object.keys(detail.from), ...Object.keys(detail.to)])
    for (const key of keys) {
      const oldValue = detail.from[key]
      const newValue = detail.to[key]
      if (JSON.stringify(oldValue) !== JSON.stringify(newValue)) {
        collected[key] = [oldValue, newValue]
      }
    }
  }
  return collected
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : []
}

function GenericDetails({ entries }: { entries: [string, unknown][] }) {
  return (
    <div className="aud-detail">
      {entries.map(([key, value]) => (
        <div key={key} style={{ display: 'contents' }}>
          <span className="aud-detail__key">{key}</span>
          <span className="aud-detail__val">{display(value)}</span>
        </div>
      ))}
    </div>
  )
}

export function AuditDetail({ detail, source = {} }: AuditDetailProps) {
  const payload = detail ?? {}
  const changes = collectChanges(payload)
  const added = stringList(payload.added)
  const removed = stringList(payload.removed)
  const hasChanges = Object.keys(changes).length > 0
  const hasSetDiff = added.length > 0 || removed.length > 0

  // 已专门渲染的键不再进通用键值表；fields 只是字段名清单，与 changes 重复
  const consumed = new Set(['changes', 'added', 'removed', 'from', 'to', 'fields'])
  const genericEntries = Object.entries(payload).filter(([key]) => !consumed.has(key))
  const sourceEntries = ([
    ['IP', source.ip],
    ['请求 ID', source.request_id],
    ['来源 UA', source.user_agent],
  ] as [string, string | null | undefined][]).filter(([, value]) => !!value)

  if (!hasChanges && !hasSetDiff && genericEntries.length === 0 && sourceEntries.length === 0) {
    return <Typography.Text type="secondary">无结构化明细</Typography.Text>
  }

  return (
    <div className="aud-details">
      {hasChanges && (
        <div className="aud-diff" aria-label="字段变更">
          {Object.entries(changes).map(([field, [oldValue, newValue]]) => (
            <div className="aud-diff__row" key={field}>
              <span className="aud-diff__key">{field}</span>
              <span className="aud-diff__values">
                <span className="aud-diff__old">{display(oldValue)}</span>
                <span className="aud-diff__arrow" aria-hidden="true">→</span>
                <span className="aud-diff__new">{display(newValue)}</span>
              </span>
            </div>
          ))}
        </div>
      )}

      {hasSetDiff && (
        <div className="aud-sets" aria-label="权限集合变更">
          {removed.length > 0 && (
            <div className="aud-sets__row">
              <span className="aud-sets__label">移除</span>
              {removed.map((code) => (
                <span key={`removed-${code}`} className="aud-chip aud-chip--removed">
                  - {code}
                </span>
              ))}
            </div>
          )}
          {added.length > 0 && (
            <div className="aud-sets__row">
              <span className="aud-sets__label">新增</span>
              {added.map((code) => (
                <span key={`added-${code}`} className="aud-chip aud-chip--added">
                  + {code}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {genericEntries.length > 0 && <GenericDetails entries={genericEntries} />}

      {sourceEntries.length > 0 && (
        <>
          <div className="aud-detail__section">来源</div>
          <div className="aud-detail">
            {sourceEntries.map(([key, value]) => (
              <div key={key} style={{ display: 'contents' }}>
                <span className="aud-detail__key">{key}</span>
                <span className="aud-detail__val">{value}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
