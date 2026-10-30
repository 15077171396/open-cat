import { useCallback, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import {
  Button,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import type { TableProps } from 'antd'
import {
  ApiOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeInvisibleOutlined,
  EyeOutlined,
  KeyOutlined,
  LinkOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SaveOutlined,
  StarFilled,
  StarOutlined,
} from '@ant-design/icons'
import { apiDelete, apiGet, apiPost, apiPut } from '../../api/client'
import ListPageHeader from '../../components/ListPageHeader'
import { PermGuard } from '../../components/PermGuard'
import { TableEmpty } from '../../components/TableEmpty'
import { SettingField } from '../../components/settings/SettingField'
import type { SettingDef } from '../../components/settings/types'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'
import { modelsForModality, type ModelModality } from './modelModality'
import './index.css'
import ModalForm from '../../components/ui/ModalForm'

/** 后端 AI 模型列表项（GET /admin/ai-models） */
interface AiModelRow {
  id: number
  code: string
  display_name: string
  provider: string
  provider_label: string
  model_name: string
  /** 未显式配置时回退供应商 .env 后的实际值 */
  effective_model_name: string
  base_url: string
  effective_base_url: string
  api_key_source: 'database' | 'env' | 'none'
  api_key_masked: string
  /** 后端 extra=allow 输出；旧数据缺省时按 text 处理 */
  modality: string
  /** 图像模型按张计费 */
  image_price: number
  input_price: number
  output_price: number
  is_enabled: boolean
  is_default: boolean
  sort_order: number
  remark: string
  tool_refs: number
  created_at: string | null
  updated_at: string | null
}

interface ProviderOption {
  name: string
  display_name: string
  env_prefix: string
  /** OpenAPI 未必声明；旧后端缺省时按 text 处理 */
  modality?: string
}

interface ListPayload {
  items: AiModelRow[]
  providers: ProviderOption[]
  total: number
}

interface ModelFormValues {
  display_name: string
  code?: string
  provider: string
  modality: ModelModality
  model_name: string
  base_url: string
  api_key: string
  image_price?: number
  input_price: number
  output_price: number
  sort_order: number
  remark: string
  is_enabled: boolean
  is_default: boolean
}

const API_KEY_SOURCE_LABEL: Record<AiModelRow['api_key_source'], { text: string; color: string }> = {
  database: { text: '已配置（独立）', color: 'green' },
  env: { text: '回退 .env', color: 'blue' },
  none: { text: '未配置', color: 'default' },
}

/**
 * 全站成本护栏（GET /admin/ai-models/cost-settings）。
 *
 * `quota` 是**当前生效**的限额与实际用量：与看板、真实限流同源（后端 ai_usage_service）。
 * 只给上限、不给用量时没法判断该不该调，所以这里把「今天用了多少 / 还剩多少」一起显示。
 */
interface CostQuota {
  today_calls: number
  month_calls: number
  today_tokens: number
  month_tokens: number
  today_blocked: number
  daily_limit: number
  monthly_limit: number
  /** null = 未设上限（必须显示「未限制」，不能当成 0 次可用） */
  remaining_today: number | null
  remaining_month: number | null
  today_cost: number | null
  month_cost: number | null
  price_configured: boolean
  /** 已有独立单价的模型数；其余按兜底单价计价 */
  priced_models: number
}

interface CostPayload {
  definitions: SettingDef[]
  groups: { key: string; label: string }[]
  values: Record<string, string>
  quota: CostQuota
}

/** 后端 POST /admin/ai-models/{id}/test 的结果（失败也是 200，ok=false + error） */
interface ProbeResult {
  ok: boolean
  model: string
  display_name: string
  provider: string
  upstream_model: string
  latency_ms: number
  reply?: string
  error?: string
}

/** 供应商色点：只用于视觉区分，不参与业务判断 */
const PROVIDER_COLORS: Record<string, string> = {
  deepseek: 'var(--app-primary)',
  openai: 'var(--app-success)',
  qwen: 'var(--app-purple)',
}

function priceLabel(row: AiModelRow): string {
  if ((row.modality || 'text') === 'image') return `¥${row.image_price ?? 0} / 张`
  if (!row.input_price && !row.output_price) return '兜底单价'
  return `入 ${row.input_price} / 出 ${row.output_price}`
}

function formatCount(value: number): string {
  return value.toLocaleString('zh-CN')
}

function formatLimit(limit: number): string {
  return limit > 0 ? `${formatCount(limit)} 次` : '未限制'
}

function formatRemaining(limit: number, remaining: number | null): string {
  if (limit <= 0 || remaining === null) return '未限制'
  return `剩 ${formatCount(remaining)} 次`
}

function formatCost(value: number | null | undefined, configured: boolean): string {
  if (!configured) return '未配置单价'
  if (value === null || value === undefined) return '—'
  return `¥${value.toFixed(2)}`
}

function Section({
  index,
  title,
  hint,
  children,
}: {
  index: string
  title: string
  hint?: string
  children: ReactNode
}) {
  return (
    <section className="ai-models-section">
      <div className="ai-models-section__head">
        <span className="ai-models-section__index">{index}</span>
        <span className="ai-models-section__title">{title}</span>
        {hint ? <span className="ai-models-section__hint">{hint}</span> : null}
      </div>
      {children}
    </section>
  )
}

function Meta({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <div className="ai-models-meta__label">{label}</div>
      <div className="ai-models-meta__value">{value}</div>
    </div>
  )
}

export default function AiModelsPage() {
  const [loading, setLoading] = useState(true)
  const [rows, setRows] = useState<AiModelRow[]>([])
  const [providers, setProviders] = useState<ProviderOption[]>([])
  const [modality, setModality] = useState<ModelModality>('text')
  const [selectedProvider, setSelectedProvider] = useState('')
  const [cost, setCost] = useState<CostPayload | null>(null)
  /** 正在测试连接的模型 id */
  const [probingId, setProbingId] = useState<number | null>(null)
  /** 最近一次测试结果（仅本次会话有效；刷新页面即清空） */
  const [probeResults, setProbeResults] = useState<Record<number, ProbeResult>>({})
  const [costDraft, setCostDraft] = useState<Record<string, string>>({})
  const [costSaving, setCostSaving] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<AiModelRow | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [clearKey, setClearKey] = useState(false)
  const [revealKey, setRevealKey] = useState(false)
  const [form] = Form.useForm<ModelFormValues>()
  /** 弹窗回填值：ModalForm 打开时写入表单（弹窗内联动要改字段，故传外部实例） */
  const [formValues, setFormValues] = useState<Record<string, unknown>>({})
  const enabled = Form.useWatch('is_enabled', form)
  const providerValue = Form.useWatch('provider', form)
  const formModalityValue = Form.useWatch('modality', form)
  /** 老数据/旧后端可能没有 modality，按 text 处理 */
  const formModality: ModelModality = formModalityValue === 'image' ? 'image' : 'text'
  /** 自定义供应商：没有 .env 兜底，Base URL 与上游模型名必须显式填 */
  const isCustomProvider = providerValue === 'custom'

  const modalityProviders = useMemo(
    () => providers.filter((p) => (p.modality || 'text') === modality),
    [providers, modality],
  )
  const modalityModels = useMemo(
    () => modelsForModality(rows, modality),
    [rows, modality],
  )

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiGet<ListPayload>('/admin/ai-models')
      setRows(data.items || [])
      setProviders(data.providers || [])
    } catch (err) {
      toast.error(getErrorMessage(err, '加载模型列表失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // 选中项失效（如切换模态或数据刷新后供应商列表变化）时，优先落到当前模态默认模型所属供应商
  useEffect(() => {
    if (!modalityProviders.length) {
      if (selectedProvider) setSelectedProvider('')
      return
    }
    if (selectedProvider && modalityProviders.some((p) => p.name === selectedProvider)) return
    const defaultProvider = modalityModels.find((row) => row.is_default)?.provider
    setSelectedProvider(defaultProvider || modalityProviders[0].name)
  }, [modalityProviders, modalityModels, selectedProvider])

  // 成本护栏与模型同页：只读展示改为**就地可编辑**（2026-10-27）。
  // 此前它留在系统设置，页面只显示四个数字 + 「去系统设置调整」，
  // 于是「把某模型单价填 0 回退兜底单价」这件事要看两个页面才能确认。
  const loadCost = useCallback(async () => {
    try {
      const data = await apiGet<CostPayload>('/admin/ai-models/cost-settings')
      setCost(data)
      const next: Record<string, string> = {}
      for (const d of data.definitions || []) next[d.key] = data.values?.[d.key] || ''
      setCostDraft(next)
    } catch (err) {
      // 成本护栏读不到不该让整页失败：模型列表照常可用
      setCost(null)
      toast.error(getErrorMessage(err, '加载成本护栏失败'))
    }
  }, [])

  useEffect(() => {
    void loadCost()
  }, [loadCost])

  const costDirtyKeys = useMemo(() => {
    if (!cost) return []
    return cost.definitions
      .filter((d) => (costDraft[d.key] || '') !== (cost.values[d.key] || ''))
      .map((d) => d.key)
  }, [cost, costDraft])

  const saveCost = async () => {
    if (!cost || !costDirtyKeys.length) return
    // 只提交差异项：未改动的键不写库，保留「显式配置 / 用默认」的区别
    const changes: Record<string, string> = {}
    for (const key of costDirtyKeys) changes[key] = costDraft[key] || ''
    setCostSaving(true)
    try {
      await apiPut('/admin/ai-models/cost-settings', { settings: changes })
      toast.success('成本护栏已保存')
      await loadCost()
    } catch (err) {
      toast.error(getErrorMessage(err, '保存失败'))
    } finally {
      setCostSaving(false)
    }
  }

  // 弹窗里的供应商按表单模态过滤：切到图像后不能再选文本供应商（后端会 400）
  const modalProviderOptions = useMemo(
    () =>
      providers
        .filter((p) => (p.modality || 'text') === formModality)
        .map((p) => ({
          value: p.name,
          label: `${p.display_name}（${p.name}）`,
        })),
    [providers, formModality],
  )

  const selectedProviderInfo = modalityProviders.find((p) => p.name === selectedProvider)
  const selectedModels = useMemo(
    () => modalityModels.filter((row) => row.provider === selectedProvider),
    [modalityModels, selectedProvider],
  )
  const defaultModel = selectedModels.find((row) => row.is_default) || selectedModels[0]
  const defaultProviderName = modalityModels.find((row) => row.is_default)?.provider || ''
  const modelCountByProvider = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const row of modalityModels) counts[row.provider] = (counts[row.provider] || 0) + 1
    return counts
  }, [modalityModels])
  const keyCounts = useMemo(() => {
    const counts = { database: 0, env: 0, none: 0 }
    for (const row of selectedModels) counts[row.api_key_source] += 1
    return counts
  }, [selectedModels])

  const openCreate = (providerName?: string) => {
    setEditing(null)
    setClearKey(false)
    setRevealKey(false)
    setFormValues({
      display_name: '',
      code: undefined,
      modality,
      provider: providerName || selectedProvider || modalityProviders[0]?.name || '',
      model_name: '',
      base_url: '',
      api_key: '',
      image_price: 0,
      input_price: 0,
      output_price: 0,
      sort_order: 0,
      remark: '',
      is_enabled: true,
      // 每个模态各有一个默认：图像模型不抢文本默认，反之亦然
      is_default: modalityModels.length === 0,
    })
    setModalOpen(true)
  }

  const openEdit = (row: AiModelRow) => {
    setEditing(row)
    setClearKey(false)
    setRevealKey(false)
    setFormValues({
      display_name: row.display_name,
      code: row.code,
      modality: row.modality === 'image' ? 'image' : 'text',
      provider: row.provider,
      model_name: row.model_name,
      base_url: row.base_url,
      api_key: '',
      image_price: row.image_price ?? 0,
      input_price: row.input_price,
      output_price: row.output_price,
      sort_order: row.sort_order,
      remark: row.remark,
      is_enabled: row.is_enabled,
      is_default: row.is_default,
    })
    setModalOpen(true)
  }

  const handleSubmit = async (raw: Record<string, unknown>) => {
    // ModalForm 传宽类型；字段定义在 ModelFormValues，收敛回具体类型
    const values = raw as unknown as ModelFormValues
    try {
      const targetModality: ModelModality = values.modality === 'image' ? 'image' : 'text'
      const basePayload: Record<string, unknown> = {
        display_name: values.display_name.trim(),
        provider: values.provider,
        modality: targetModality,
        model_name: (values.model_name || '').trim(),
        base_url: (values.base_url || '').trim(),
        image_price: targetModality === 'image' ? values.image_price ?? 0 : 0,
        sort_order: values.sort_order ?? 0,
        remark: (values.remark || '').trim(),
        is_enabled: values.is_enabled,
        is_default: values.is_enabled ? values.is_default : false,
      }
      // 图像模型按张计费，表单没有 token 单价时不要伪造 0 覆盖库内值
      if (targetModality === 'text') {
        basePayload.input_price = values.input_price ?? 0
        basePayload.output_price = values.output_price ?? 0
      }
      if (editing === null) {
        const payload: Record<string, unknown> = {
          ...basePayload,
          api_key: (values.api_key || '').trim(),
        }
        const code = (values.code || '').trim()
        if (code) payload.code = code
        await apiPost('/admin/ai-models', payload)
        toast.success('模型已创建')
      } else {
        const payload: Record<string, unknown> = { ...basePayload }
        if (clearKey) {
          payload.api_key = ''
        } else if ((values.api_key || '').trim()) {
          payload.api_key = values.api_key.trim()
        }
        await apiPut(`/admin/ai-models/${editing.id}`, payload)
        toast.success('模型已更新')
      }
      setModalOpen(false)
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, '保存失败'))
    }
  }

  const toggleEnabled = async (row: AiModelRow, checked: boolean) => {
    setBusyId(row.id)
    try {
      await apiPut(`/admin/ai-models/${row.id}`, { is_enabled: checked })
      toast.success(checked ? '模型已启用' : '模型已禁用')
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, '操作失败'))
    } finally {
      setBusyId(null)
    }
  }

  const setDefault = async (row: AiModelRow) => {
    setBusyId(row.id)
    try {
      await apiPut(`/admin/ai-models/${row.id}`, { is_default: true })
      toast.success(`已将「${row.display_name}」设为默认模型`)
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, '操作失败'))
    } finally {
      setBusyId(null)
    }
  }

  const probeModel = async (row: AiModelRow) => {
    setProbingId(row.id)
    try {
      const result = await apiPost<ProbeResult>(`/admin/ai-models/${row.id}/test`)
      setProbeResults((s) => ({ ...s, [row.id]: result }))
      if (!result.ok) {
        // 失败原因必须看得见：这是这个按钮存在的全部意义
        toast.error(`连接失败：${result.error || '未知原因'}`)
      }
    } catch (err) {
      toast.error(getErrorMessage(err, '测试失败'))
    } finally {
      setProbingId(null)
    }
  }

  const handleDelete = async (row: AiModelRow) => {
    setBusyId(row.id)
    try {
      await apiDelete(`/admin/ai-models/${row.id}`)
      toast.success('模型已删除')
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, '删除失败'))
    } finally {
      setBusyId(null)
    }
  }

  const columns: TableProps<AiModelRow>['columns'] = [
    {
      title: '模型',
      dataIndex: 'display_name',
      key: 'display_name',
      width: 220,
      render: (_value, row) => (
        <Space orientation="vertical" size={2}>
          <Space size={6}>
            <Typography.Text strong>{row.display_name}</Typography.Text>
            {row.is_default && (
              <Tag color="gold" icon={<StarFilled />} style={{ marginInlineEnd: 0 }}>
                默认
              </Tag>
            )}
          </Space>
          <Space size={6} wrap>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {row.code}
            </Typography.Text>
            {row.tool_refs > 0 && (
              <Tooltip title={`有 ${row.tool_refs} 个在线工具引用此模型`}>
                <Tag color="orange" style={{ marginInlineEnd: 0 }}>
                  {row.tool_refs} 个工具
                </Tag>
              </Tooltip>
            )}
          </Space>
        </Space>
      ),
    },
    {
      title: '上游模型',
      key: 'model',
      width: 170,
      render: (_value, row) => {
        const probe = probeResults[row.id]
        return (
          <Space orientation="vertical" size={2}>
            <Typography.Text>{row.effective_model_name || '未配置'}</Typography.Text>
            {!row.model_name && row.effective_model_name && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                回退 .env
              </Typography.Text>
            )}
            {/* 探测结果只保留在本次会话里：它反映的是「刚才那一刻」的连通性，
                刷新页面即清空，避免把昨天的绿灯当成今天的证据 */}
            {probe && (
              <Tooltip
                title={
                  probe.ok
                    ? `已连通 · ${probe.latency_ms}ms${
                        probe.reply ? ` · 回复「${probe.reply}」` : ''
                      }`
                    : probe.error || '连接失败'
                }
              >
                <Tag
                  color={probe.ok ? 'green' : 'red'}
                  style={{ marginInlineEnd: 0, fontSize: 11 }}
                >
                  {probe.ok ? `连通 ${probe.latency_ms}ms` : '连接失败'}
                </Tag>
              </Tooltip>
            )}
          </Space>
        )
      },
    },
    {
      title: 'API Key',
      key: 'api_key',
      width: 120,
      render: (_value, row) => {
        const meta = API_KEY_SOURCE_LABEL[row.api_key_source]
        return (
          <Space orientation="vertical" size={2}>
            <Tag color={meta.color} style={{ marginInlineEnd: 0 }}>
              {meta.text}
            </Tag>
            {row.api_key_masked && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {row.api_key_masked}
              </Typography.Text>
            )}
          </Space>
        )
      },
    },
    {
      title: modality === 'image' ? '单价（元/张）' : '单价（元/百万 tokens）',
      key: 'price',
      width: 140,
      render: (_value, row) => <span style={{ fontSize: 12 }}>{priceLabel(row)}</span>,
    },
    {
      title: '启用',
      key: 'enabled',
      width: 70,
      render: (_value, row) => (
        <PermGuard perm="ai_model:edit">
          <Switch
            size="small"
            checked={row.is_enabled}
            loading={busyId === row.id}
            onChange={(checked) => toggleEnabled(row, checked)}
          />
        </PermGuard>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 300,
      render: (_value, row) => (
        <Space size={4}>
          <PermGuard perm="ai_model:edit">
            <Tooltip title="发一次极短的真实请求，验证 Key / Base URL / 上游模型名是否正确">
              <Button
                type="link"
                size="small"
                icon={<ApiOutlined />}
                loading={probingId === row.id}
                onClick={() => void probeModel(row)}
              >
                测试
              </Button>
            </Tooltip>
          </PermGuard>
          <PermGuard perm="ai_model:edit">
            {!row.is_default && row.is_enabled ? (
              <Button
                type="link"
                size="small"
                icon={<StarOutlined />}
                loading={busyId === row.id}
                onClick={() => setDefault(row)}
              >
                设为默认
              </Button>
            ) : null}
            <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(row)}>
              编辑
            </Button>
          </PermGuard>
          <PermGuard perm="ai_model:delete">
            <Popconfirm
              title="删除模型"
              description={
                row.tool_refs > 0
                  ? `该模型仍被 ${row.tool_refs} 个工具引用，需先改工具配置或改为禁用。`
                  : `删除「${row.display_name}」后不可恢复，确定继续？`
              }
              okText="删除"
              okType="danger"
              cancelText="取消"
              disabled={row.tool_refs > 0}
              onConfirm={() => handleDelete(row)}
            >
              <Tooltip title={row.tool_refs > 0 ? '被工具引用时不可删除，请改为禁用' : undefined}>
                <Button type="link" size="small" danger icon={<DeleteOutlined />} disabled={row.tool_refs > 0}>
                  删除
                </Button>
              </Tooltip>
            </Popconfirm>
          </PermGuard>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <ListPageHeader
        icon={<RobotOutlined />}
        title="模型管理"
        extra={
          <Space wrap>
            <Segmented
              value={modality}
              onChange={(value) => {
                setModality(value as ModelModality)
                // 供应商列表随模态整体替换，旧选中项在新列表里没有意义
                setSelectedProvider('')
              }}
              options={[
                { value: 'text', label: '文本模型' },
                { value: 'image', label: '图像模型' },
              ]}
            />
            <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
              刷新
            </Button>
            <PermGuard perm="ai_model:create">
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openCreate(selectedProvider)}>
                新增模型
              </Button>
            </PermGuard>
          </Space>
        }
      />

      <div className="ai-models-layout">
        <aside className="ai-models-providers">
          <div className="ai-models-providers__head">
            <span className="ai-models-providers__title">供应商</span>
            <span className="ai-models-providers__count">{modalityProviders.length}</span>
            <PermGuard perm="ai_model:create">
              <Tooltip title="为当前供应商新增模型">
                <Button
                  type="text"
                  size="small"
                  icon={<PlusOutlined />}
                  aria-label="为当前供应商新增模型"
                  onClick={() => openCreate(selectedProvider)}
                />
              </Tooltip>
            </PermGuard>
          </div>

          <div className="ai-models-providers__list">
            {loading && modalityProviders.length === 0 ? (
              <div className="ai-models-providers__empty">加载中…</div>
            ) : null}
            {!loading && modalityProviders.length === 0 ? (
              <div className="ai-models-providers__empty">该模态暂无可用供应商</div>
            ) : null}
            {modalityProviders.map((provider) => {
              const active = provider.name === selectedProvider
              return (
                <button
                  key={provider.name}
                  type="button"
                  className={`ai-models-provider${active ? ' is-active' : ''}`}
                  aria-pressed={active}
                  onClick={() => setSelectedProvider(provider.name)}
                >
                  <span
                    className="ai-models-provider__dot"
                    style={{ background: PROVIDER_COLORS[provider.name] || 'var(--app-text-3)' }}
                  />
                  <span className="ai-models-provider__main">
                    <span className="ai-models-provider__name">{provider.display_name}</span>
                    <span className="ai-models-provider__code">{provider.name}</span>
                  </span>
                  <span className="ai-models-provider__meta">
                    {provider.name === defaultProviderName ? (
                      <span className="ai-models-provider__default">默认</span>
                    ) : null}
                    <span>{modelCountByProvider[provider.name] || 0} 个模型</span>
                  </span>
                </button>
              )
            })}
          </div>

          <div className="ai-models-providers__note">
            <div>密钥加密存储且不回显，变更写入审计日志。</div>
            <div>供应商由代码注册，模型可自由扩展。</div>
          </div>
        </aside>

        <section className="ai-models-detail">
          <header className="ai-models-detail__head">
            <div>
              <div className="ai-models-detail__title-row">
                <Typography.Title level={4} style={{ margin: 0 }}>
                  {selectedProviderInfo?.display_name || '选择供应商'}
                </Typography.Title>
                {selectedProvider && selectedProvider === defaultProviderName ? (
                  <Tag color="gold" icon={<StarFilled />}>
                    默认供应商
                  </Tag>
                ) : null}
              </div>
              <Space size={8} wrap style={{ marginTop: 8 }}>
                <Tag>{modality === 'image' ? '图像生成' : 'OpenAI 兼容'}</Tag>
                <Tag>{selectedModels.length} 个模型</Tag>
                {selectedProviderInfo ? (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {selectedProviderInfo.name}
                  </Typography.Text>
                ) : null}
              </Space>
            </div>
          </header>

          <Section
            index="01"
            title="连接"
            hint={modality === 'image' ? '图像生成协议' : 'OpenAI 兼容协议'}
          >
            <div className="ai-models-meta">
              <Meta
                label="供应商实现"
                value={
                  selectedProviderInfo
                    ? `${selectedProviderInfo.display_name}（${selectedProviderInfo.name}）`
                    : '—'
                }
              />
              <Meta
                label="环境变量前缀"
                value={
                  selectedProviderInfo ? <code>{selectedProviderInfo.env_prefix}</code> : '—'
                }
              />
              <Meta
                label="默认模型"
                value={
                  defaultModel ? (
                    <Space size={6} wrap>
                      <span>{defaultModel.display_name}</span>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {defaultModel.code}
                      </Typography.Text>
                    </Space>
                  ) : (
                    '未设置'
                  )
                }
              />
              <Meta
                label="生效 Base URL"
                value={
                  defaultModel?.effective_base_url ? (
                    <code>{defaultModel.effective_base_url}</code>
                  ) : (
                    '未配置'
                  )
                }
              />
            </div>
            <Typography.Text type="secondary" className="ai-models-section__note">
              Base URL 与上游模型名在每个模型里单独维护，留空回退该供应商的 .env；连接实现由代码注册表提供。
            </Typography.Text>
          </Section>

          <Section index="02" title="密钥" hint="按模型独立配置 · 加密存储">
            <Space size={8} wrap>
              <Tag color="green">独立 Key {keyCounts.database}</Tag>
              <Tag color="blue">回退 .env {keyCounts.env}</Tag>
              <Tag>未配置 {keyCounts.none}</Tag>
            </Space>
            <Typography.Text type="secondary" className="ai-models-section__note">
              页面只回显末四位，不返回明文；Key 在模型编辑弹窗里维护，变更会写入操作审计。
            </Typography.Text>
          </Section>

          <Section index="03" title="全站成本护栏" hint="限额 + 兜底单价 · 与看板同源">
            {cost === null ? (
              <Typography.Text type="secondary">
                成本护栏加载失败，刷新重试（模型列表不受影响）。
              </Typography.Text>
            ) : (
              <>
                <div className="ai-models-quota">
                  <div className="ai-models-quota__item">
                    <div className="ai-models-quota__label">今日调用</div>
                    <div className="ai-models-quota__value">
                      {formatCount(cost.quota.today_calls)}
                      <span className="ai-models-quota__unit">
                        / 上限 {formatLimit(cost.quota.daily_limit)}
                      </span>
                    </div>
                    <div className="ai-models-quota__sub">
                      {formatRemaining(cost.quota.daily_limit, cost.quota.remaining_today)}
                      {cost.quota.today_blocked > 0
                        ? ` · 已拦截 ${formatCount(cost.quota.today_blocked)} 次`
                        : ''}
                    </div>
                  </div>
                  <div className="ai-models-quota__item">
                    <div className="ai-models-quota__label">本月调用</div>
                    <div className="ai-models-quota__value">
                      {formatCount(cost.quota.month_calls)}
                      <span className="ai-models-quota__unit">
                        / 上限 {formatLimit(cost.quota.monthly_limit)}
                      </span>
                    </div>
                    <div className="ai-models-quota__sub">
                      {formatRemaining(cost.quota.monthly_limit, cost.quota.remaining_month)}
                    </div>
                  </div>
                  <div className="ai-models-quota__item">
                    <div className="ai-models-quota__label">估算花费</div>
                    <div className="ai-models-quota__value">
                      {formatCost(cost.quota.today_cost, cost.quota.price_configured)}
                      <span className="ai-models-quota__unit">今日</span>
                    </div>
                    <div className="ai-models-quota__sub">
                      本月 {formatCost(cost.quota.month_cost, cost.quota.price_configured)}
                    </div>
                  </div>
                  <div className="ai-models-quota__item">
                    <div className="ai-models-quota__label">单价覆盖</div>
                    <div className="ai-models-quota__value">
                      {cost.quota.priced_models}
                      <span className="ai-models-quota__unit">个模型有独立单价</span>
                    </div>
                    <div className="ai-models-quota__sub">其余按下方的兜底单价计价</div>
                  </div>
                </div>

                <div className="ai-models-cost-grid">
                  {cost.definitions.map((def) => (
                    <SettingField
                      key={def.key}
                      def={def}
                      value={costDraft[def.key] ?? ''}
                      onChange={(next) => setCostDraft((s) => ({ ...s, [def.key]: next }))}
                    />
                  ))}
                </div>

                <Space size={12} align="center" style={{ marginTop: 12 }}>
                  <PermGuard perm="ai_model:edit">
                    <Button
                      size="small"
                      type="primary"
                      icon={<SaveOutlined />}
                      loading={costSaving}
                      disabled={!costDirtyKeys.length}
                      onClick={() => void saveCost()}
                    >
                      保存护栏
                    </Button>
                  </PermGuard>
                  {costDirtyKeys.length > 0 ? (
                    <Typography.Text type="warning" style={{ fontSize: 12 }}>
                      有 {costDirtyKeys.length} 项改动未保存
                    </Typography.Text>
                  ) : (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      留空保存 = 回退服务器 .env 的默认值
                    </Typography.Text>
                  )}
                </Space>

                <Typography.Text type="secondary" className="ai-models-section__note">
                  {cost.quota.price_configured
                    ? '上限为 0 表示不限制该维度（不是「0 次可用」）；模型单价填 0 时按下方的兜底单价计价。'
                    : '当前兜底单价为 0 且没有模型单价：看板只统计次数，不估算金额。'}
                </Typography.Text>
              </>
            )}
          </Section>

          <Section index="04" title="模型" hint={`${selectedModels.length} 个`}>
            <Table<AiModelRow>
              rowKey="code"
              columns={columns}
              dataSource={selectedModels}
              loading={loading}
              pagination={false}
              size="small"
              scroll={{ x: 1000 }}
              locale={{
                emptyText: (
                  <TableEmpty
                    hasFilter={false}
                    filteredText=""
                    emptyText="该供应商还没有配置模型"
                    create={{
                      perm: 'ai_model:create',
                      label: '新增模型',
                      onClick: () => openCreate(selectedProvider),
                    }}
                  />
                ),
              }}
            />
          </Section>
        </section>
      </div>

      <ModalForm
        open={modalOpen}
        title={editing ? '编辑模型' : '新增模型'}
        okText="保存"
        onCancel={() => setModalOpen(false)}
        onSubmit={handleSubmit}
        width={680}
        form={form}
        initialValues={formValues}
        formStyle={{ marginTop: 16 }}
      >
          <Form.Item
            label="展示名"
            name="display_name"
            rules={[
              { required: true, message: '请填写展示名' },
              { max: 100, message: '最多 100 字' },
            ]}
          >
            <Input placeholder="如：DeepSeek 对话" />
          </Form.Item>

          <Space size={16} style={{ display: 'flex' }} align="start">
            <Form.Item
              label="模型标识（code）"
              name="code"
              style={{ flex: 1 }}
              extra={
                editing
                  ? '创建后不可修改：工具与翻译按它引用模型'
                  : '留空自动生成；工具配置里存的就是这个值'
              }
              rules={[
                {
                  pattern: /^[a-z0-9][a-z0-9._-]{0,63}$/,
                  message: '只能包含小写字母、数字、点、下划线和短横线',
                },
              ]}
            >
              <Input placeholder="如 deepseek-reasoner" disabled={!!editing} />
            </Form.Item>
            <Form.Item
              label="供应商"
              name="provider"
              style={{ width: 200 }}
              rules={[{ required: true, message: '请选择供应商' }]}
              extra={providerValue ? `协议实现：${providerValue}` : undefined}
            >
              <Select options={modalProviderOptions} />
            </Form.Item>
            <Form.Item
              label="模态"
              name="modality"
              style={{ width: 120 }}
              rules={[{ required: true, message: '请选择模态' }]}
              extra={editing ? '创建后不可修改' : undefined}
            >
              <Select
                disabled={!!editing}
                options={[
                  { value: 'text', label: '文本' },
                  { value: 'image', label: '图像' },
                ]}
                onChange={(next) => {
                  // 供应商与模态强绑定，切换后重置为该模态的第一个供应商
                  const target: ModelModality = next === 'image' ? 'image' : 'text'
                  const first = providers.find((p) => (p.modality || 'text') === target)
                  form.setFieldValue('provider', first?.name || '')
                }}
              />
            </Form.Item>
          </Space>

          <Form.Item
            label="上游模型名"
            name="model_name"
            extra={
              formModality === 'image'
                ? '调用时传给上游的 model，如 wanx2.1-t2i-turbo；留空回退供应商 .env'
                : isCustomProvider
                  ? '必填：上游要求的确切模型名，如 moonshot-v1-8k / glm-4-plus / qwen2.5:7b'
                  : '调用时传给上游的 model，如 deepseek-chat / gpt-4o-mini；留空回退供应商 .env'
            }
            rules={[
              { max: 100, message: '最多 100 字符' },
              // 自定义供应商没有 .env 默认值，留空只会在调用期报一个看不懂的错
              { required: isCustomProvider, message: '自定义供应商必须填上游模型名' },
            ]}
          >
            <Input
              placeholder={
                formModality === 'image'
                  ? '如 wanx2.1-t2i-turbo'
                  : isCustomProvider
                    ? '如 moonshot-v1-8k'
                    : '如 deepseek-chat'
              }
            />
          </Form.Item>

          <Form.Item
            label="Base URL"
            name="base_url"
            extra={
              isCustomProvider
                ? '必填：OpenAI 兼容端点，填到 /v1 这一层（如 https://api.moonshot.cn/v1）；仅支持 http(s)，内网/环回地址会被拒绝'
                : '留空回退供应商 .env；仅支持 http(s)，内网/环回地址会被拒绝'
            }
            rules={[
              { max: 500, message: '最多 500 字符' },
              { required: isCustomProvider, message: '自定义供应商必须填 Base URL' },
            ]}
          >
            <Input
              prefix={<LinkOutlined />}
              placeholder={
                isCustomProvider
                  ? 'https://api.moonshot.cn/v1'
                  : formModality === 'image'
                    ? 'https://dashscope.aliyuncs.com'
                    : 'https://api.deepseek.com/v1'
              }
            />
          </Form.Item>

          <Form.Item label="API Key" style={{ marginBottom: 8 }}>
            <Space orientation="vertical" size={6} style={{ width: '100%' }}>
              <Form.Item name="api_key" noStyle>
                <Input
                  type="text"
                  autoComplete="off"
                  disabled={clearKey}
                  placeholder={
                    editing
                      ? editing.api_key_source === 'database'
                        ? `已配置 ${editing.api_key_masked}，留空表示不修改`
                        : '当前回退服务器 .env；填写则改用独立 Key'
                      : '留空则回退服务器 .env'
                  }
                  style={
                    revealKey
                      ? undefined
                      : ({ WebkitTextSecurity: 'disc' } as unknown as CSSProperties)
                  }
                  suffix={
                    <Button
                      type="text"
                      size="small"
                      aria-label={revealKey ? '隐藏密钥' : '显示密钥'}
                      icon={revealKey ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                      onClick={() => setRevealKey((v) => !v)}
                    />
                  }
                />
              </Form.Item>
              {editing && (
                <Space size={8} wrap>
                  <Button
                    size="small"
                    icon={<KeyOutlined />}
                    type={clearKey ? 'primary' : 'default'}
                    onClick={() => {
                      setClearKey((v) => !v)
                      form.setFieldValue('api_key', '')
                    }}
                  >
                    {clearKey ? '取消清除' : '清除独立 Key（回退 .env）'}
                  </Button>
                  {clearKey && (
                    <Typography.Text type="danger" style={{ fontSize: 12 }}>
                      保存后将删除库内 Key，改用服务器 .env
                    </Typography.Text>
                  )}
                </Space>
              )}
            </Space>
          </Form.Item>

          <Space size={16} style={{ display: 'flex' }} align="start">
            {formModality === 'image' ? (
              <Form.Item label="图片单价（元/张）" name="image_price" style={{ flex: 1 }}>
                <InputNumber
                  min={0}
                  max={1_000_000}
                  precision={4}
                  style={{ width: '100%' }}
                  suffix="元/张"
                />
              </Form.Item>
            ) : (
              <>
                <Form.Item label="输入单价" name="input_price" style={{ flex: 1 }}>
                  <InputNumber
                    min={0}
                    max={1_000_000}
                    precision={6}
                    style={{ width: '100%' }}
                    suffix="元/百万 tokens"
                  />
                </Form.Item>
                <Form.Item label="输出单价" name="output_price" style={{ flex: 1 }}>
                  <InputNumber
                    min={0}
                    max={1_000_000}
                    precision={6}
                    style={{ width: '100%' }}
                    suffix="元/百万 tokens"
                  />
                </Form.Item>
              </>
            )}
            <Form.Item label="排序" name="sort_order" style={{ width: 130 }}>
              <InputNumber min={0} max={1_000_000} style={{ width: '100%' }} />
            </Form.Item>
          </Space>
          <Typography.Text type="secondary" style={{ display: 'block', marginTop: -12, marginBottom: 16, fontSize: 12 }}>
            {formModality === 'image'
              ? '图片按张计费；填 0 表示不估算图片花费'
              : '单价填 0 表示回退「全站成本护栏」里的兜底单价'}
          </Typography.Text>

          <Form.Item label="备注" name="remark" rules={[{ max: 500, message: '最多 500 字' }]}>
            <Input.TextArea rows={2} placeholder="可选，如：用于长文本改写" />
          </Form.Item>

          <Space size={28}>
            <Form.Item label="启用" name="is_enabled" valuePropName="checked" style={{ marginBottom: 0 }}>
              <Switch
                onChange={(checked) => {
                  if (!checked) form.setFieldValue('is_default', false)
                }}
              />
            </Form.Item>
            <Form.Item label="设为默认" name="is_default" valuePropName="checked" style={{ marginBottom: 0 }}>
              <Switch disabled={!enabled} />
            </Form.Item>
          </Space>
      </ModalForm>
    </div>
  )
}
