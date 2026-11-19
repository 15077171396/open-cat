import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, DatePicker, Input, Modal, Select, Space, Tooltip, Typography } from 'antd'
import { LoginOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs, { type Dayjs } from 'dayjs'

import { DataTable, ListToolbar, PageHeader, SelectionBar, StatusTag } from '../../components/ui'
import ExportButton from '../../components/ui/ExportButton'
import { PermGuard } from '../../components/PermGuard'
import { apiDelete, apiGet, idempotentConfig, newIdempotencyKey } from '../../api/client'
import { DEFAULT_PAGE_SIZE } from '../../constants'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'

/**
 * 登录日志（/login-logs）：登录成功 / 失败 / 登出的安全遥测。
 *
 * 与操作审计互补：审计回答「管理员做了什么」，这里回答「谁试过登录」。
 * 账号列显示的是后端脱敏后的形态（如 138****5678）—— 搜索时输入**完整账号**，
 * 后端按脱敏后精确匹配（脱敏串本身残缺，模糊匹配只会制造似是而非的结果）。
 */

interface LoginLogItem {
  id: number
  user_id: number | null
  account: string
  status: 'success' | 'failed' | 'logout'
  message: string
  ip: string | null
  user_agent: string | null
  request_id: string | null
  created_at: string | null
}

interface LoginLogPage {
  items: LoginLogItem[]
  total: number
  page: number
  page_size: number
}

/** 状态 → 标签配色（success=成功 / failed=危险 / logout=中性；与后端枚举同源） */
const STATUS_META: Record<LoginLogItem['status'], { label: string; tone: 'success' | 'danger' | 'neutral' }> = {
  success: { label: '登录成功', tone: 'success' },
  failed: { label: '登录失败', tone: 'danger' },
  logout: { label: '登出', tone: 'neutral' },
}

const STATUS_OPTIONS = [
  { value: 'success', label: '登录成功' },
  { value: 'failed', label: '登录失败' },
  { value: 'logout', label: '登出' },
]

type RangeValue = [Dayjs | null, Dayjs | null] | null

export default function LoginLogsList() {
  const [items, setItems] = useState<LoginLogItem[]>([])
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [selectedIds, setSelectedIds] = useState<number[]>([])

  // 受控输入与已提交查询分离：输入不实时触发请求（与用户管理页同一口径）
  const [accountInput, setAccountInput] = useState('')
  const [account, setAccount] = useState('')
  const [statusInput, setStatusInput] = useState<string | undefined>(undefined)
  const [status, setStatus] = useState<string | undefined>(undefined)
  const [rangeInput, setRangeInput] = useState<RangeValue>(null)
  const [range, setRange] = useState<RangeValue>(null)

  // 请求序号防竞态：快速换页/换筛选时丢弃过期响应
  const loadSeqRef = useRef(0)
  // 批量删除的幂等 key：在确认弹窗打开时生成，一次动作一个 key
  const [deleteKey, setDeleteKey] = useState('')

  const hasFilter = Boolean(account || status || range?.[0])

  const load = useCallback(async (overrides?: {
    page?: number
    account?: string
    status?: string | undefined
    range?: RangeValue
  }) => {
    const seq = ++loadSeqRef.current
    const nextPage = overrides?.page ?? page
    const nextAccount = overrides?.account ?? account
    const nextStatus = overrides?.status ?? status
    const nextRange = overrides?.range ?? range
    setLoading(true)
    try {
      const params: Record<string, string | number> = { page: nextPage, page_size: DEFAULT_PAGE_SIZE }
      if (nextAccount) params.account = nextAccount
      if (nextStatus) params.status = nextStatus
      if (nextRange?.[0]) params.start_at = nextRange[0].toISOString()
      if (nextRange?.[1]) params.end_at = nextRange[1].endOf('day').toISOString()
      const data = await apiGet<LoginLogPage>('/admin/login-logs', { params })
      if (seq !== loadSeqRef.current) return
      setItems(data.items)
      setTotal(data.total)
      setPage(nextPage)
      // 换筛选/换页必须清空选中，防止跨视图误删
      setSelectedIds([])
    } catch (err) {
      if (seq === loadSeqRef.current) toast.error(getErrorMessage(err, '登录日志加载失败'))
    } finally {
      if (seq === loadSeqRef.current) setLoading(false)
    }
  }, [page, account, status, range])

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const resetFilters = () => {
    setAccountInput('')
    setStatusInput(undefined)
    setRangeInput(null)
    setAccount('')
    setStatus(undefined)
    setRange(null)
    void load({ page: 1, account: '', status: undefined, range: null })
  }

  const applyFilters = () => {
    const nextAccount = accountInput.trim()
    setAccount(nextAccount)
    setStatus(statusInput)
    setRange(rangeInput)
    void load({ page: 1, account: nextAccount, status: statusInput, range: rangeInput })
  }

  const openBatchDelete = () => {
    setDeleteKey(newIdempotencyKey())
    Modal.confirm({
      title: `确认删除选中的 ${selectedIds.length} 条登录日志？`,
      content: '删除后无法恢复：这些「谁试过登录」的安全线索会被抹掉，请确认不再需要排查。',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          await apiDelete('/admin/login-logs', { data: { ids: selectedIds }, ...idempotentConfig(deleteKey) })
          toast.success(`已删除 ${selectedIds.length} 条登录日志`)
          // 删空当前页回退一页（与列表页通用口径一致）
          const remain = total - selectedIds.length
          const lastPage = Math.max(1, Math.ceil(remain / DEFAULT_PAGE_SIZE))
          void load({ page: Math.min(page, lastPage) })
        } catch (err) {
          toast.error(getErrorMessage(err, '删除失败'))
        }
      },
    })
  }

  const columns: ColumnsType<LoginLogItem> = [
    {
      title: '账号',
      dataIndex: 'account',
      width: 200,
      ellipsis: true,
      render: (value: string) => <Typography.Text copyable={false}>{value || '-'}</Typography.Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (value: LoginLogItem['status']) => {
        const meta = STATUS_META[value] ?? { label: value, tone: 'neutral' as const }
        return <StatusTag tone={meta.tone}>{meta.label}</StatusTag>
      },
    },
    {
      title: '结果说明',
      dataIndex: 'message',
      width: 240,
      ellipsis: true,
      render: (value: string) => value || '-',
    },
    { title: 'IP', dataIndex: 'ip', width: 140, ellipsis: true, render: (v: string | null) => v || '-' },
    {
      title: '设备 (User-Agent)',
      dataIndex: 'user_agent',
      ellipsis: true,
      width: 260,
      render: (v: string | null) =>
        v ? (
          <Tooltip title={v} placement="topLeft">
            <span>{v}</span>
          </Tooltip>
        ) : (
          '-'
        ),
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 180,
      render: (v: string | null) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-'),
    },
  ]

  const exportParams: Record<string, string | number> = {}
  if (account) exportParams.account = account
  if (status) exportParams.status = status
  if (range?.[0]) exportParams.start_at = range[0].toISOString()
  if (range?.[1]) exportParams.end_at = range[1].endOf('day').toISOString()

  return (
    <div className="adm-page">
      <PageHeader
        icon={<LoginOutlined />}
        title="登录日志"
        subtitle={['登录成功 / 失败 / 登出的安全遥测，与操作审计互补']}
        count={{ total, unit: '条' }}
        actions={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新
            </Button>
            {/* 导出与列表页同筛选口径（login_log:view）；数据外带，后端写审计 */}
            <ExportButton
              url="/admin/export/login-logs"
              params={exportParams}
              label="登录日志"
              fallbackName="login-logs.csv"
            />
          </Space>
        }
      />
      <ListToolbar
        hasFilter={hasFilter}
        onReset={resetFilters}
        extra={<span className="adm-toolbar__hint">账号需输入完整值（后端按脱敏形态精确匹配）</span>}
      >
        <Input.Search
          allowClear
          placeholder="账号（完整手机号 / 邮箱）"
          style={{ width: 240 }}
          value={accountInput}
          onChange={(e) => setAccountInput(e.target.value)}
          onSearch={applyFilters}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 140 }}
          options={STATUS_OPTIONS}
          value={statusInput}
          onChange={setStatusInput}
        />
        <DatePicker.RangePicker
          value={rangeInput}
          onChange={(v) => setRangeInput(v as RangeValue)}
          showTime
          style={{ width: 360 }}
        />
        <Button type="primary" onClick={applyFilters}>
          查询
        </Button>
      </ListToolbar>
      {selectedIds.length > 0 && (
        <SelectionBar count={selectedIds.length}>
          <PermGuard perm="login_log:clear">
            <Button danger onClick={openBatchDelete}>
              删除选中
            </Button>
          </PermGuard>
        </SelectionBar>
      )}
      <DataTable<LoginLogItem>
        rowKey="id"
        columns={columns}
        dataSource={items}
        loading={loading}
        scrollX={1120}
        rowSelection={{
          selectedRowKeys: selectedIds,
          onChange: (keys) => setSelectedIds(keys as number[]),
        }}
        pagination={{ page, pageSize: DEFAULT_PAGE_SIZE, total, onPageChange: (p) => void load({ page: p }) }}
        empty={{
          hasFilter,
          filteredText: '没有符合条件的登录记录',
          emptyText: '暂无登录记录',
          onReset: resetFilters,
        }}
      />
    </div>
  )
}
