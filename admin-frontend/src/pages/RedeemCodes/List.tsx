import { useState } from 'react'
import { Button, DatePicker, Form, Input, InputNumber, Modal, Progress, Radio, Segmented, Space, Table, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import type { Dayjs } from 'dayjs'
import { CopyOutlined, EyeOutlined, GiftOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons'
import { apiGet, apiPatch, apiPost } from '../../api/client'
import { useCrudList } from '../../hooks/useCrudList'
import { PermGuard } from '../../components/PermGuard'
import { useAuthStore } from '../../store/authStore'
import type { RedemptionCode, RedemptionRecordItem } from '../../types'
import { getErrorMessage } from '../../utils/error'
import { copyText } from '../../utils/clipboard'
import { formatDate, formatDateTime } from '../../utils/datetime'

import { toast } from '../../utils/toast'
import PageHeader from '../../components/ui/PageHeader'
import { DataTable, ListToolbar, ModalForm } from '../../components/ui'
/** 展示状态 → 标签（与后端 eff_status 口径一致：作废 > 过期 > 兑完 > 进行中） */
const EFF_STATUS_MAP: Record<string, { color: string; text: string }> = {
  active: { color: 'green', text: '进行中' },
  exhausted: { color: 'orange', text: '已兑完' },
  expired: { color: 'default', text: '已过期' },
  disabled: { color: 'red', text: '已作废' },
}

const STATUS_FILTER_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'active', label: '进行中' },
  { value: 'exhausted', label: '已兑完' },
  { value: 'expired', label: '已过期' },
  { value: 'disabled', label: '已作废' },
]

interface CreateFormValues {
  points: number
  code_type: 'single' | 'multi'
  max_uses: number
  expires_at: Dayjs | null
  count: number
  note: string
}

export default function RedeemCodesList() {
  // 搜索：keywordInput 仅受控输入，回车/点击后才提交进查询参数
  // （避免未提交的输入文字意外影响作废/刷新等隐式重新请求的过滤条件）
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState('')

  // 兑换码明文只发给能创建码的账号；只读角色拿到的是掩码
  // 按字段订阅，别整份 store 拉进来（会因 user/permissions 的新对象触发整页重渲染）
  const canRevealCode = useAuthStore(
    (s) => s.isSuper || (s.permissions?.includes('redeem_code:create') ?? false),
  )

  const listParams: Record<string, string | number | boolean> = {}
  if (keyword.trim()) listParams.keyword = keyword.trim()
  if (status) listParams.status = status

  const { items: codes, loading, page, pageSize, total, refresh, setPage } =
    useCrudList<RedemptionCode>({
      basePath: '/admin/redemption-codes',
      params: Object.keys(listParams).length ? listParams : undefined,
    })

  // 批量选择与作废
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [batchLoading, setBatchLoading] = useState(false)

  // 生成兑换码弹窗
  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm<CreateFormValues>()
  const codeType = Form.useWatch('code_type', form)

  // 生成结果弹窗（展示新码列表，便于复制发放）
  const [resultOpen, setResultOpen] = useState(false)
  const [createdCodes, setCreatedCodes] = useState<RedemptionCode[]>([])

  // 兑换记录弹窗
  const [recordsState, setRecordsState] = useState<{
    open: boolean
    loading: boolean
    code: RedemptionCode | null
    items: RedemptionRecordItem[]
    total: number
    page: number
  }>({ open: false, loading: false, code: null, items: [], total: 0, page: 1 })

  const hasFilter = !!keyword.trim() || !!status

  const handleSearch = (value: string) => {
    setKeyword(value)
    setSelectedIds([])
    setPage(1)
  }

  const handleResetFilters = () => {
    setKeywordInput('')
    setKeyword('')
    setStatus('')
    setSelectedIds([])
    setPage(1)
  }

  /**
   * 生成成功后回到「无筛选 + 第 1 页」再刷新。
   *
   * 为什么不能只 setState：残留的搜索词/状态筛选会把新码过滤掉，表现为
   * 「生成成功但列表里看不到」。参数真的变了的话，setState 会触发重新请求；
   * **参数本来就没变时状态更新不会触发任何请求**（paramsKey 相同），
   * 这种情况必须显式 refresh，否则新码同样不出现。
   */
  const resetFiltersAndReload = () => {
    const paramsUnchanged = !keyword.trim() && !status && page === 1
    setKeyword('')
    setKeywordInput('')
    setStatus('')
    setSelectedIds([])
    setPage(1)
    if (paramsUnchanged) refresh()
  }

  /** 提交生成：单人码 max_uses 固定 1，多人码用表单输入的人数 */
  const handleCreate = async (raw: Record<string, unknown>) => {
    // ModalForm 传宽类型；字段定义在 CreateFormValues，收敛回具体类型
    const values = raw as unknown as CreateFormValues
    setCreating(true)
    try {
      const payload: Record<string, unknown> = {
        points: values.points,
        max_uses: values.code_type === 'single' ? 1 : values.max_uses,
        count: values.count,
      }
      if (values.expires_at) payload.expires_at = values.expires_at.toISOString()
      if (values.note?.trim()) payload.note = values.note.trim()
      const data = await apiPost<{ items: RedemptionCode[]; count: number }>(
        '/admin/redemption-codes',
        payload,
      )
      setCreateOpen(false)
      setCreatedCodes(data.items || [])
      setResultOpen(true)
      resetFiltersAndReload()
    } catch (err) {
      toast.error(getErrorMessage(err, '生成兑换码失败'))
    } finally {
      setCreating(false)
    }
  }

  const handleCopyAll = async () => {
    if (!createdCodes.length) return
    const ok = await copyText(createdCodes.map((c) => c.code).join('\n'))
    if (ok) toast.success('已复制全部兑换码')
    else toast.error('复制失败，请手动复制')
  }

  /** 加载某兑换码的兑换记录 */
  const loadRecords = async (code: RedemptionCode, targetPage = 1) => {
    setRecordsState((s) => ({ ...s, loading: true }))
    try {
      const data = await apiGet<{ items: RedemptionRecordItem[]; total?: number }>(
        `/admin/redemption-codes/${code.id}/records`,
        { params: { page: targetPage, page_size: 10 } },
      )
      setRecordsState((s) => ({
        ...s,
        loading: false,
        items: data.items || [],
        total: data.total ?? 0,
        page: targetPage,
      }))
    } catch (err) {
      setRecordsState((s) => ({ ...s, loading: false }))
      toast.error(getErrorMessage(err, '加载兑换记录失败'))
    }
  }

  const handleViewRecords = (code: RedemptionCode) => {
    setRecordsState({ open: true, loading: false, code, items: [], total: 0, page: 1 })
    loadRecords(code, 1)
  }

  const handleDisable = (code: RedemptionCode) => {
    Modal.confirm({
      title: '确认作废兑换码',
      content: `作废后 ${code.code} 将无法再被兑换（已兑换用户不受影响），确认作废？`,
      okText: '作废',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await apiPatch(`/admin/redemption-codes/${code.id}/disable`)
          toast.success('兑换码已作废')
          refresh()
        } catch (err) {
          toast.error(getErrorMessage(err, '作废失败'))
        }
      },
    })
  }

  const handleBatchDisable = () => {
    Modal.confirm({
      title: '批量作废兑换码',
      content: `确认作废选中的 ${selectedIds.length} 个兑换码？作废后无法再被兑换（已兑换用户不受影响），且不可恢复；其中已是「已作废」的会被自动跳过。`,
      okText: '作废',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        setBatchLoading(true)
        try {
          const data = await apiPost<{ disabled: number; skipped: number }>(
            '/admin/redemption-codes/batch-disable',
            { ids: selectedIds },
          )
          const skipped = data.skipped ? `，跳过 ${data.skipped} 个已作废` : ''
          toast.success(`已作废 ${data.disabled} 个兑换码${skipped}`)
          setSelectedIds([])
          refresh()
        } catch (err) {
          toast.error(getErrorMessage(err, '批量作废失败'))
        } finally {
          setBatchLoading(false)
        }
      },
    })
  }

  const columns: TableProps<RedemptionCode>['columns'] = [
    {
      title: '兑换码',
      dataIndex: 'code',
      key: 'code',
      width: 240,
      // 无创建权限时后端返回的是掩码 —— 复制一串星号毫无意义，故只在可揭示时给复制按钮
      render: (v: string) => (
        <Typography.Text
          copyable={canRevealCode ? { text: v } : false}
          style={{ fontWeight: 600, letterSpacing: 0.5 }}
        >
          {v}
        </Typography.Text>
      ),
    },
    {
      title: '积分',
      dataIndex: 'points',
      key: 'points',
      width: 80,
      align: 'right',
      render: (v: number) => (
        <span className="adm-delta" style={{ color: 'var(--app-success)' }}>
          +{v}
        </span>
      ),
    },
    {
      title: '类型',
      dataIndex: 'is_single',
      key: 'is_single',
      width: 80,
      render: (v: boolean) =>
        v ? (
          <Tag color="blue" style={{ margin: 0 }}>
            单人码
          </Tag>
        ) : (
          <Tag color="purple" style={{ margin: 0 }}>
            多人码
          </Tag>
        ),
    },
    {
      title: '兑换进度',
      key: 'progress',
      width: 150,
      render: (_: unknown, row: RedemptionCode) => (
        <Progress
          percent={Math.min(100, Math.round((row.used_count / Math.max(1, row.max_uses)) * 100))}
          size="small"
          format={() => `${row.used_count}/${row.max_uses}`}
        />
      ),
    },
    {
      title: '状态',
      dataIndex: 'eff_status',
      key: 'eff_status',
      width: 80,
      render: (v: string) => {
        const cfg = EFF_STATUS_MAP[v] || { color: 'default', text: v }
        return (
          <Tag color={cfg.color} style={{ margin: 0 }}>
            {cfg.text}
          </Tag>
        )
      },
    },
    {
      title: '过期时间',
      dataIndex: 'expires_at',
      key: 'expires_at',
      width: 160,
      render: (v: string | null) =>
        v ? (
          <span className="adm-muted">{formatDateTime(v)}</span>
        ) : (
          <span className="adm-muted">永久有效</span>
        ),
    },
    {
      title: '备注',
      dataIndex: 'note',
      key: 'note',
      width: 140,
      render: (v: string | null) =>
        v ? (
          <Typography.Text ellipsis={{ tooltip: v }} style={{ maxWidth: 150 }}>
            {v}
          </Typography.Text>
        ) : (
          <span className="adm-muted">—</span>
        ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      // 只到日：精确到秒的创建时刻在操作审计里能查到，这一列留给兑换码本身更值
      width: 110,
      render: (v: string | null) => <span className="adm-muted">{formatDate(v)}</span>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
      fixed: 'right',
      render: (_: unknown, row: RedemptionCode) => (
        <Space size={0}>
          <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => handleViewRecords(row)}>
            记录
          </Button>
          {/* 仍按原始 status 判断（与改造前一致）：已过期/已兑完的码也允许作废，
              只是把它们从「还能兑」的池子里摘出去，不改变任何已兑换用户的状态 */}
          {row.status === 'active' && (
            <PermGuard perm="redeem_code:disable">
              <Button type="link" size="small" danger icon={<StopOutlined />} onClick={() => handleDisable(row)}>
                作废
              </Button>
            </PermGuard>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div className="adm-page">
      {/* 页头：不放在 PermGuard 里 —— 原先整个页头（含搜索框与状态筛选）被
          redeem_code:create 包着，只有「能生成兑换码」的人才看得见，
          与其余模块「页头对 redeem_code:view 可见」不一致 */}
      <PageHeader
        icon={<GiftOutlined />}
        title="积分兑换码"
        subtitle="生成后请及时发放；作废不可恢复"
        count={{ total: total, unit: "个" }}
        actions={
          <>
            <Input.Search
              placeholder="搜索兑换码 / 备注"
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              onSearch={handleSearch}
              style={{ width: 220 }}
              allowClear
            />
            <PermGuard perm="redeem_code:create">
              <Button type="primary" icon={<GiftOutlined />} onClick={() => setCreateOpen(true)}>
                生成兑换码
              </Button>
            </PermGuard>

          </>
        }
      />

      {/* 工具条：状态筛选 + 批量作废 */}
      <ListToolbar
        hasFilter={hasFilter}
        onReset={handleResetFilters}
        extra={(
          <Space size={12}>
            {selectedIds.length > 0 && (
              <span className="adm-selinfo">
                已选 <strong>{selectedIds.length}</strong> 项
                <Button type="link" size="small" onClick={() => setSelectedIds([])}>
                  取消选择
                </Button>
              </span>
            )}
            <PermGuard perm="redeem_code:disable">
              <Button
                danger
                icon={<StopOutlined />}
                disabled={!selectedIds.length}
                loading={batchLoading}
                onClick={handleBatchDisable}
              >
                批量作废
              </Button>
            </PermGuard>
            <Tooltip title="重新拉取当前条件下的兑换码">
              <Button icon={<ReloadOutlined />} onClick={refresh}>
                刷新
              </Button>
            </Tooltip>
          </Space>
        )}
      >
        <Segmented
          value={status}
          onChange={(v) => {
            setStatus(String(v))
            setSelectedIds([])
            setPage(1)
          }}
          options={STATUS_FILTER_OPTIONS}
        />
      </ListToolbar>

      <DataTable<RedemptionCode>
        dataSource={codes}
        columns={columns}
        rowKey="id"
        loading={loading}
        scrollX={1210}
        rowSelection={{
        selectedRowKeys: selectedIds,
        onChange: (keys) => setSelectedIds(keys as number[]),
        }}
        pagination={{ page: page, pageSize: pageSize, total: total, onPageChange: setPage }}
        empty={{ hasFilter: hasFilter, filteredText: "没有符合筛选条件的兑换码", emptyText: "还没有兑换码，先生成一批吧", onReset: handleResetFilters }}
      />

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        提示：兑换码是积分的入金口，生成即等于凭空造出「积分 × 每人上限 × 数量」的潜在积分债务；
        生成与作废都会写入操作审计。明文兑换码只对持有「生成兑换码」权限的账号可见，其余角色看到的是掩码。
      </Typography.Text>

      {/* 生成兑换码弹窗 */}
      <ModalForm
        title={
          <Space size={8}>
            <GiftOutlined />
            生成兑换码
          </Space>
        }
        open={createOpen}
        // 生成中不允许关闭：关掉会丢掉「生成结果」弹窗里那批码
        onCancel={() => {
          if (!creating) setCreateOpen(false)
        }}
        onSubmit={handleCreate}
        okText="生成"
        width={480}
        form={form}
        initialValues={{ code_type: 'single', max_uses: 10, count: 1, points: 100 }}
        formStyle={{ marginTop: 8 }}
      >
          <Form.Item
            name="points"
            label="兑换积分（每人兑换可获得）"
            rules={[{ required: true, message: '请输入兑换积分' }]}
          >
            <InputNumber min={1} max={1000000} precision={0} style={{ width: '100%' }} placeholder="如 100" />
          </Form.Item>
          <Form.Item name="code_type" label="兑换方式" rules={[{ required: true }]}>
            <Radio.Group>
              <Space orientation="vertical">
                <Radio value="single">单人码：一个兑换码仅限 1 人兑换</Radio>
                <Radio value="multi">多人码：一个兑换码可被多人兑换（限量先到先得）</Radio>
              </Space>
            </Radio.Group>
          </Form.Item>
          {codeType === 'multi' && (
            <Form.Item
              name="max_uses"
              label="可兑换人数上限"
              rules={[{ required: true, message: '请输入可兑换人数' }]}
              extra="名额兑完后，后续用户将无法再使用该兑换码"
            >
              <InputNumber min={2} max={100000} precision={0} style={{ width: '100%' }} placeholder="如 50" />
            </Form.Item>
          )}
          <Form.Item name="count" label="生成数量" rules={[{ required: true, message: '请输入生成数量' }]}>
            <InputNumber min={1} max={100} precision={0} style={{ width: '100%' }} placeholder="一次最多生成 100 个" />
          </Form.Item>
          <Form.Item name="expires_at" label="有效期" extra="不设置则永久有效；按本地时区选择，提交时自动换算为 UTC">
            <DatePicker showTime style={{ width: '100%' }} placeholder="选择过期时间（可选）" />
          </Form.Item>
          <Form.Item name="note" label="备注">
            <Input maxLength={200} placeholder="选填，如：活动发放（仅管理端可见）" />
          </Form.Item>
      </ModalForm>

      {/* 生成结果弹窗 */}
      <Modal
        title={
          <Space size={8}>
            <CopyOutlined />
            生成成功
          </Space>
        }
        open={resultOpen}
        onCancel={() => setResultOpen(false)}
        footer={[
          <Button key="copy" type="primary" icon={<CopyOutlined />} onClick={handleCopyAll}>
            复制全部
          </Button>,
          <Button key="close" onClick={() => setResultOpen(false)}>
            关闭
          </Button>,
        ]}
        destroyOnHidden
        width={480}
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
          成功生成 {createdCodes.length} 个兑换码，积分 {createdCodes[0]?.points ?? 0} 分/份，
          请妥善保存并及时发放给用户。
        </Typography.Paragraph>
        <div className="rc-codelist">
          {createdCodes.map((c) => (
            <div key={c.id}>{c.code}</div>
          ))}
        </div>
      </Modal>

      {/* 兑换记录弹窗 */}
      <Modal
        title={
          <Space size={8}>
            <EyeOutlined />
            <span>兑换记录{recordsState.code ? `（${recordsState.code.code}）` : ''}</span>
          </Space>
        }
        open={recordsState.open}
        onCancel={() => setRecordsState((s) => ({ ...s, open: false }))}
        footer={null}
        destroyOnHidden
        width={560}
      >
        <Table<RedemptionRecordItem>
          dataSource={recordsState.items}
          columns={[
            {
              title: '用户',
              dataIndex: 'user_account',
              key: 'user_account',
              render: (v: string | undefined) => <span className="adm-cell__name">{v || '—'}</span>,
            },
            {
              title: '获得积分',
              dataIndex: 'points',
              key: 'points',
              width: 110,
              align: 'right',
              render: (v: number) => (
                <span className="adm-delta" style={{ color: 'var(--app-success)' }}>
                  +{v}
                </span>
              ),
            },
            {
              title: '兑换时间',
              dataIndex: 'created_at',
              key: 'created_at',
              width: 170,
              render: (v: string | null) => <span className="adm-muted">{formatDateTime(v)}</span>,
            },
          ]}
          rowKey="id"
          loading={recordsState.loading}
          size="small"
          locale={{ emptyText: '还没有人兑换这个码' }}
          pagination={{
            current: recordsState.page,
            pageSize: 10,
            total: recordsState.total,
            onChange: (p) => recordsState.code && loadRecords(recordsState.code, p),
            showTotal: (t) => `共 ${t} 条`,
          }}
        />
      </Modal>
    </div>
  )
}
