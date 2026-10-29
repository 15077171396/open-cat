import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Button, Spin } from 'antd'
import { SaveOutlined } from '@ant-design/icons'
import client, { type ApiEnvelope } from '../../api/client'
import ListPageHeader from '../ListPageHeader'
import { PermGuard } from '../PermGuard'
import { useUnsavedChangesGuard } from '../../hooks/useUnsavedChangesGuard'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'
import type { SettingGroupView, SettingsPayload } from './types'

/**
 * 设置类页面的**全部有状态逻辑**：加载 / 草稿 / 差异判定 / 保存 / 离开提醒。
 *
 * 抽出来的原因：登录管理（`/admin/login-settings`）与系统设置（`/admin/settings`）
 * 共用同一套「只提交差异项 + 未保存拦截 + 掩码语义」。复制一份的代价不是多写 200 行，
 * 而是两处会慢慢分叉 —— 例如「只提交差异项」的规则漏一处，就会出现
 * 「打开页面、什么都没改、点保存 → 把服务器 .env 配置覆盖成空」。
 *
 * 布局由调用方通过 `children` 渲染函数决定（系统设置是 Tabs，登录管理是纵向区块），
 * 本组件只负责数据与保存。
 */
export interface SettingsEditorSlot<P extends SettingsPayload = SettingsPayload> {
  /** 按后端顺序分好组、且补齐了草稿值的设置项 */
  groups: SettingGroupView[]
  /** 编辑草稿（含全部键，空串 = 使用默认） */
  draft: Record<string, string>
  /** DB 中已保存的原始值（仅显式配置项） */
  saved: Record<string, string>
  /** 存在未保存修改的分组 key（用于 tab 红点 / 区块标记） */
  dirtyGroups: Set<string>
  /** 是否存在未保存修改 */
  isDirty: boolean
  setValue: (key: string, value: string) => void
  /** 重新拉取（保存后自动调用；页面上的「刷新」按钮也可用） */
  reload: () => void
  /** 接口原始载荷（页面自定义字段，如登录管理的 channels） */
  payload: P
}

interface SettingsEditorProps<P extends SettingsPayload> {
  /** 读写端点（GET 取定义+值，PUT 保存差异项） */
  endpoint: string
  title: string
  icon: ReactNode
  /** 保存按钮所需的权限码（不同模块不同） */
  savePerm: string
  /** 保存成功提示（各模块生效时机不同，由页面声明） */
  saveSuccessText: string
  loadErrorText: string
  /** 页头保存按钮左侧的额外操作 */
  headerExtra?: ReactNode
  children: (slot: SettingsEditorSlot<P>) => ReactNode
}

export default function SettingsEditor<P extends SettingsPayload>({
  endpoint,
  title,
  icon,
  savePerm,
  saveSuccessText,
  loadErrorText,
  headerExtra,
  children,
}: SettingsEditorProps<P>) {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [payload, setPayload] = useState<P | null>(null)
  /** 编辑草稿（全部键，空串=使用默认） */
  const [draft, setDraft] = useState<Record<string, string>>({})
  /** DB 中已保存的原始值（仅显式配置项） */
  const [saved, setSaved] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await client.get<unknown, ApiEnvelope<P>>(endpoint)
      if (res.code === 0 && res.data) {
        const definitions = res.data.definitions || []
        // 草稿补齐全部键（未配置项为空串），避免受控值 undefined
        const nextDraft: Record<string, string> = {}
        for (const d of definitions) nextDraft[d.key] = res.data.values?.[d.key] || ''
        setDraft(nextDraft)
        setSaved(res.data.values || {})
        setPayload(res.data)
      }
    } catch (err) {
      toast.error(getErrorMessage(err, loadErrorText))
    } finally {
      setLoading(false)
    }
  }, [endpoint, loadErrorText])

  useEffect(() => {
    load()
  }, [load])

  const setValue = (key: string, value: string) =>
    setDraft((s) => ({ ...s, [key]: value }))

  /** 是否有未保存修改（草稿与已保存值存在差异） */
  const isDirty = useMemo(() => {
    if (!payload) return false
    return payload.definitions.some((d) => (draft[d.key] || '') !== (saved[d.key] || ''))
  }, [payload, draft, saved])

  useUnsavedChangesGuard(() => isDirty)

  const handleSave = async () => {
    if (!payload) return
    // 仅提交差异项：未修改的键不写库，便于区分「用户显式配置」与「默认」
    const changes: Record<string, string> = {}
    for (const d of payload.definitions) {
      const v = draft[d.key] || ''
      if (v !== (saved[d.key] || '')) changes[d.key] = v
    }
    if (!Object.keys(changes).length) {
      toast.info('没有需要保存的修改')
      return
    }
    setSaving(true)
    try {
      await client.put(endpoint, { settings: changes })
      toast.success(saveSuccessText)
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, '保存失败'))
    } finally {
      setSaving(false)
    }
  }

  // 按分组聚合：顺序与说明来自后端，未声明的分组追加到末尾（不丢弃）
  const groups = useMemo<SettingGroupView[]>(() => {
    if (!payload) return []
    const map = new Map<string, SettingGroupView['items']>()
    for (const d of payload.definitions) {
      if (!map.has(d.group)) map.set(d.group, [])
      map.get(d.group)!.push(d)
    }
    const meta = payload.groups || []
    const declared = meta.map((g) => g.key).filter((k) => map.has(k))
    const extra = [...map.keys()].filter((k) => !meta.some((g) => g.key === k))
    return [...declared, ...extra].map((key) => {
      const items = map.get(key)!
      const found = meta.find((g) => g.key === key)
      return {
        key,
        // 标签优先用后端声明的（同一分组只有一个标题），缺失时退回定义里的 group_label
        label: found?.label || items[0].group_label,
        description: found?.description || '',
        items,
      }
    })
  }, [payload])

  /** 存在未保存修改的分组集合：区块 / tab 上标红点，防止切走后遗忘保存 */
  const dirtyGroups = useMemo(() => {
    const set = new Set<string>()
    for (const g of groups) {
      if (g.items.some((d) => (draft[d.key] || '') !== (saved[d.key] || ''))) set.add(g.key)
    }
    return set
  }, [groups, draft, saved])

  return (
    <div>
      <ListPageHeader
        icon={icon}
        title={title}
        extra={
          <>
            {headerExtra}
            <PermGuard perm={savePerm}>
              <Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={!isDirty} onClick={handleSave}>
                保存设置
              </Button>
            </PermGuard>
          </>
        }
      />

      {loading || !payload ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
          <Spin size="large" />
        </div>
      ) : (
        children({
          groups,
          draft,
          saved,
          dirtyGroups,
          isDirty,
          setValue,
          reload: load,
          payload,
        })
      )}
    </div>
  )
}
