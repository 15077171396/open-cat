import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Card, DatePicker, Input, Segmented, Select, Space, Table, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import type { Dayjs } from 'dayjs'
import { AuditOutlined, ReloadOutlined } from '@ant-design/icons'
import { apiGet } from '../../api/client'
import { getErrorMessage } from '../../utils/error'
import { formatDateTime } from '../../utils/datetime'

import { toast } from '../../utils/toast'
import { TableEmpty } from '../../components/TableEmpty'
/**
 * 管理操作审计：**只读**页面（能改能删的审计等于没有审计，故不用 useCrudList）。
 *
 * ⚠️ 动作中文名、目标类型中文名、风险等级**全部来自接口**，本文件不保留任何映射表 ——
 * 前端自己维护一份的话，写入端新增动作码/目标类型时这里会静默漏项（显示成裸代码、
 * 或把高危操作标成常规色）。筛选下拉的候选项同样由接口给出。
 */

interface AuditLogItem {
  id: number
  admin_id: number
  admin_account: string
  action: string
  action_label: string
  /** 风险等级：danger / money / normal（后端按动作码归类） */
  risk_level: string
  target_type: string
  target_type_label: string
  target_id: number | null
  summary: string
  detail: Record<string, unknown> | null
  /** 来源字段：共享或被劫持的管理会话只能靠这几项区分「同一个人两台设备」 */
  ip: string | null
  user_agent: string | null
  request_id: string | null
  created_at: string | null
}

interface Option {
  value: string
  label: string
}

interface AuditLogResponse {
  items: AuditLogItem[]
  total: number
  page: number
  page_size: number
  action_options: Option[]
  target_type_options: Option[]
  risk_options: Option[]
}

/** 风险等级 → 标签配色（取值由接口给出，这里只负责把等级翻译成颜色） */
const RISK_COLOR: Record<string, string> = {
  danger: 'red',
  money: 'gold',
  normal: 'blue',
}

function formatValue(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

/** 展开区：结构化明细 + 来源；比直接甩一段 JSON 好读得多 */
function AuditDetail({ row }: { row: AuditLogItem }) {
  const entries = row.detail ? Object.entries(row.detail) : []
  const source = ([
    ['IP', row.ip],
    ['请求 ID', row.request_id],
    ['来源 UA', row.user_agent],
  ] as [string, string | null][]).filter(([, v]) => !!v)

  return (
    <div className="aud-detail">
      {entries.length === 0 && source.length === 0 && (
        <Typography.Text type="secondary">无结构化明细</Typography.Text>
      )}
      {entries.map(([key, value]) => (
        <div key={key} style={{ display: 'contents' }}>
          <span className="aud-detail__key">{key}</span>
          <span className="aud-detail__val">{formatValue(value)}</span>
        </div>
      ))}
      {source.length > 0 && <div className="aud-detail__section">来源</div>}
      {source.map(([key, value]) => (
        <div key={key} style={{ display: 'contents' }}>
          <span className="aud-detail__key">{key}</span>
          <span className="aud-detail__val">{value}</span>
        </div>
      ))}
    </div>
  )
}

export default function AuditLogsList() {
  const [items, setItems] = useState<AuditLogItem[]>([])
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [actionOptions, setActionOptions] = useState<Option[]>([])
  const [targetTypeOptions, setTargetTypeOptions] = useState<Option[]>([])
  const [riskOptions, setRiskOptions] = useState<Option[]>([])

  // 搜索：*Input 仅受控输入，回车/点击后才提交进查询参数
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [adminIdInput, setAdminIdInput] = useState('')
  const [adminId, setAdminId] = useState('')
  const [action, setAction] = useState('')
  const [targetType, setTargetType] = useState('')
  const [risk, setRisk] = useState('')
  /** 时间范围：存 dayjs 以便回填控件，提交时才转 ISO（后端统一换算成 naive UTC） */
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null)

  // 请求序号：快速改筛选时旧请求可能晚于新请求返回，仅最新一次允许落盘
  const loadSeqRef = useRef(0)

  const load = useCallback(async () => {
    const seq = ++loadSeqRef.current
    setLoading(true)
    try {
      const params: Record<string, string | number> = { page }
      if (action) params.action = action
      if (targetType) params.target_type = targetType
      if (risk) params.risk = risk
      if (keyword.trim()) params.keyword = keyword.trim()
      if (adminId.trim()) params.admin_id = Number(adminId.trim())
      if (range) {
        params.start_at = range[0].startOf('day').toISOString()
        params.end_at = range[1].endOf('day').toISOString()
      }
      const data = await apiGet<AuditLogResponse>('/admin/audit-logs', { params })
      if (seq !== loadSeqRef.current) return
      setItems(data.items || [])
      setTotal(data.total ?? 0)
      setActionOptions(data.action_options || [])
      setTargetTypeOptions(data.target_type_options || [])
      setRiskOptions(data.risk_options || [])
    } catch (err) {
      if (seq !== loadSeqRef.current) return
      toast.error(getErrorMessage(err, '加载审计日志失败'))
    } finally {
      if (seq === loadSeqRef.current) setLoading(false)
    }
  }, [page, action, targetType, risk, keyword, adminId, range])

  useEffect(() => {
    load()
  }, [load])

  const hasFilter =
    !!action || !!targetType || !!risk || !!keyword.trim() || !!adminId.trim() || !!range

  const resetPageAnd = (fn: () => void) => {
    fn()
    setPage(1)
  }

  const handleResetFilters = () => {
    setKeywordInput('')
    setKeyword('')
    setAdminIdInput('')
    setAdminId('')
    setAction('')
    setTargetType('')
    setRisk('')
    setRange(null)
    setPage(1)
  }

  const columns: TableProps<AuditLogItem>['columns'] = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (v: string | null) => <span className="adm-muted">{formatDateTime(v)}</span>,
    },
    {
      title: '操作者',
      dataIndex: 'admin_account',
      key: 'admin_account',
      width: 200,
      render: (v: string) => <span className="adm-cell__name">{v || '—'}</span>,
    },
    {
      title: '操作者 ID',
      dataIndex: 'admin_id',
      key: 'admin_id',
      width: 110,
      render: (v: number) => <span className="adm-num">{v}</span>,
    },
    {
      title: '动作',
      dataIndex: 'action',
      key: 'action',
      width: 150,
      render: (_: unknown, row: AuditLogItem) => (
        // 颜色按后端给的 risk_level，不在前端另判 —— 否则新增动作码会静默显示成常规色
        <Tag color={RISK_COLOR[row.risk_level] ?? 'default'} style={{ margin: 0 }}>
          {row.action_label}
        </Tag>
      ),
    },
    {
      title: '目标',
      key: 'target',
      width: 160,
      render: (_: unknown, row: AuditLogItem) =>
        row.target_type ? (
          <span>
            {row.target_type_label || row.target_type}
            {row.target_id != null && (
              <span className="adm-muted" style={{ marginLeft: 4 }}>
                #{row.target_id}
              </span>
            )}
          </span>
        ) : (
          <span className="adm-muted">—</span>
        ),
    },
    {
      title: '摘要',
      dataIndex: 'summary',
      key: 'summary',
      width: 400,
      render: (v: string) => (
        <Typography.Paragraph
          ellipsis={{ tooltip: v || '', rows: 2 }}
          style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}
        >
          {v || '—'}
        </Typography.Paragraph>
      ),
    },
  ]

  return (
    <div className="adm-page">
      <div className="adm-header">
        <div className="adm-header__main">
          <span className="adm-header__icon">
            <AuditOutlined />
          </span>
          <div style={{ minWidth: 0 }}>
            <div className="adm-header__title">操作审计</div>
            <div className="adm-header__sub">
              <span>仅记录、不可修改与删除</span>
              <span className="adm-sep">·</span>
              <span>
                共 <strong>{total}</strong> 条
              </span>
            </div>
          </div>
        </div>
        <div className="adm-header__actions">
          <Input.Search
            allowClear
            placeholder="搜索摘要 / 操作者账号"
            value={keywordInput}
            onChange={(e) => setKeywordInput(e.target.value)}
            onSearch={(v) => resetPageAnd(() => setKeyword(v.trim()))}
            style={{ width: 240 }}
          />
        </div>
      </div>

      {/* 工具条：四个筛选维度 */}
      <Card className="adm-toolbar" variant="outlined">
        <div className="adm-toolbar__row">
          <Space wrap size={12}>
            <Select
              allowClear
              placeholder="按动作筛选"
              value={action || undefined}
              onChange={(v) => resetPageAnd(() => setAction(v ?? ''))}
              options={actionOptions}
              style={{ width: 170 }}
            />
            <Select
              allowClear
              placeholder="按目标类型筛选"
              value={targetType || undefined}
              onChange={(v) => resetPageAnd(() => setTargetType(v ?? ''))}
              options={targetTypeOptions}
              style={{ width: 150 }}
            />
            <Segmented
              value={risk}
              onChange={(v) => resetPageAnd(() => setRisk(String(v)))}
              options={[{ value: '', label: '全部' }, ...riskOptions]}
            />
            <DatePicker.RangePicker
              value={range}
              onChange={(dates) =>
                resetPageAnd(() => setRange(dates && dates[0] && dates[1] ? [dates[0], dates[1]] : null))
              }
              placeholder={['开始日期', '结束日期']}
              allowClear
            />
            <Input
              allowClear
              placeholder="操作者 ID"
              value={adminIdInput}
              onChange={(e) => setAdminIdInput(e.target.value)}
              onPressEnter={(e) => {
                const v = (e.target as HTMLInputElement).value
                resetPageAnd(() => setAdminId(v.trim()))
              }}
              style={{ width: 130 }}
            />
            {hasFilter && (
              <Tooltip title="清空全部筛选条件">
                <Button type="text" size="small" icon={<ReloadOutlined />} onClick={handleResetFilters}>
                  重置
                </Button>
              </Tooltip>
            )}
          </Space>
          <Tooltip title="重新拉取当前条件下的审计记录">
            <Button icon={<ReloadOutlined />} onClick={load}>
              刷新
            </Button>
          </Tooltip>
        </div>
      </Card>

      <Card className="adm-tablecard" variant="outlined">
        <Table<AuditLogItem>
          className="adm-table"
          dataSource={items}
          columns={columns}
          rowKey="id"
          loading={loading}
          scroll={{ x: 1190 }}
          locale={{
            emptyText: (
              <TableEmpty
                hasFilter={hasFilter}
                filteredText='没有符合筛选条件的审计记录'
                emptyText='暂无审计记录'
                onReset={handleResetFilters}
              />
          ),
          }}
          pagination={{
            current: page,
            pageSize: 20,
            total,
            onChange: setPage,
            showTotal: (t) => `共 ${t} 条`,
            showSizeChanger: false,
          }}
          expandable={{
            // 明细默认收起：一屏要能扫过更多记录，需要细节时再展开
            expandedRowRender: (row) => <AuditDetail row={row} />,
            rowExpandable: (row) => !!row.detail || !!row.ip || !!row.request_id,
          }}
        />
      </Card>

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        提示：审计保留 365 天；操作者账号是{' '}
        <strong>当时</strong>
        的快照（账号可改名/注销），展开可见结构化明细与来源 IP、请求 ID ——
        共享或被劫持的管理会话只能靠这几项做归因。
      </Typography.Text>
    </div>
  )
}
