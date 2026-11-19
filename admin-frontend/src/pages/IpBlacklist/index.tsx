import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Input, Space, Statistic, Typography } from 'antd'
import { ReloadOutlined, SaveOutlined, SafetyOutlined } from '@ant-design/icons'

import { apiGet, apiPut } from '../../api/client'
import { PermGuard } from '../../components/PermGuard'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'
import { PageHeader } from '../../components/ui'

/**
 * IP 黑名单（/ip-blacklist）：运营级访问封禁。
 *
 * 每行一个 IPv4/IPv6 地址或 CIDR 网段（如 203.0.113.7 / 10.0.0.0/8），保存后
 * **即时生效**（本节点立即、其余后端进程 60 秒内跟上），命中请求直接 403；
 * 健康检查不受影响（探活被挡会让运维误判宕机）。
 * 保存动作写审计（拉黑/放行都是改变线上访问面的操作）。
 */

interface BlacklistData {
  entries: string[]
  invalid_entries: string[]
  max_entries: number
}

export default function IpBlacklistPage() {
  const [meta, setMeta] = useState<BlacklistData | null>(null)
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiGet<BlacklistData>('/admin/ip-blacklist')
      setMeta(data)
      setText(data.entries.join('\n'))
    } catch (err) {
      toast.error(getErrorMessage(err, '黑名单加载失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // 文本 → 词条（与服务端 parse 同口径：按行/逗号分隔）
  const parsed = useMemo(
    () =>
      text
        .replace(/\r/g, '\n')
        .split(/[\n,]/)
        .map((s) => s.trim())
        .filter(Boolean),
    [text],
  )
  const dirty = useMemo(() => {
    if (!meta) return false
    return JSON.stringify(parsed) !== JSON.stringify(meta.entries)
  }, [parsed, meta])

  const save = async () => {
    if (meta && parsed.length > meta.max_entries) {
      toast.error(`黑名单条目超过上限（${meta.max_entries}）`)
      return
    }
    setSaving(true)
    try {
      const result = await apiPut<{
        changed: boolean
        entries: string[]
        invalid_entries: string[]
        added: string[]
        removed: string[]
      }>('/admin/ip-blacklist', { raw_text: text })
      if (result.invalid_entries?.length) {
        toast.warning(`已保存，但 ${result.invalid_entries.length} 行格式无效不会生效`)
      } else {
        toast.success(result.changed ? '黑名单已保存，立即生效' : '内容未变化')
      }
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, '黑名单保存失败'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="adm-page">
      <PageHeader
        icon={<SafetyOutlined />}
        title="IP 黑名单"
        subtitle={['命中黑名单的请求直接 403，健康检查不受影响']}
        count={{ total: parsed.length, unit: '条' }}
        actions={
          <Space>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
              重载
            </Button>
            <PermGuard perm="ip_blacklist:update">
              <Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={!dirty} onClick={() => void save()}>
                保存并生效
              </Button>
            </PermGuard>
          </Space>
        }
      />

      {meta?.invalid_entries?.length ? (
        <Alert
          type="warning"
          showIcon
          title={`${meta.invalid_entries.length} 行格式无效，不会生效`}
          description={meta.invalid_entries.join('、')}
          style={{ marginBottom: 12 }}
        />
      ) : null}

      <Card variant="outlined">
        <Space wrap style={{ marginBottom: 12, width: '100%', justifyContent: 'space-between' }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            每行一个 IPv4/IPv6 地址或 CIDR 网段（例：203.0.113.7、10.0.0.0/8）；保存后立即生效，无需重启
          </Typography.Text>
          <Statistic title="条目数" value={parsed.length} suffix={`/ ${meta?.max_entries ?? 1000}`} />
        </Space>
        <Input.TextArea
          rows={14}
          style={{ fontFamily: 'monospace', fontSize: 13 }}
          placeholder={'203.0.113.7\n198.51.100.0/24\n2001:db8::/32'}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        {dirty && (
          <Typography.Paragraph type="warning" style={{ fontSize: 12, marginTop: 12, marginBottom: 0 }}>
            有未保存修改；保存动作会写入操作审计（含新增/移除的条目）。
          </Typography.Paragraph>
        )}
      </Card>

      <Alert
        type="info"
        showIcon
        title="误拉黑了自己怎么办"
        description="黑名单保存即时生效，若把管理出口 IP 拉黑会导致后台无法访问 —— 可通过服务器本机 curl 调 PUT /api/v1/admin/ip-blacklist 恢复（本机请求仍会被中间件拦截，需先清空 system_settings 表的 ip_blacklist 键），或等待其他未受影响的管理员移除该条目。保存前请确认出口 IP。"
        style={{ marginTop: 16 }}
      />
    </div>
  )
}
