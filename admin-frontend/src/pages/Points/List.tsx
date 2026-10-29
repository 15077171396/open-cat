import { useState } from 'react'
import { Button, DatePicker, Input, Segmented, Select, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import type { Dayjs } from 'dayjs'
import { ReloadOutlined, WalletOutlined } from '@ant-design/icons'
import { useCrudList } from '../../hooks/useCrudList'
import type { PointTransaction } from '../../types'
import { POINT_TX_TYPE_MAP } from '../../constants'
import { formatDateTime } from '../../utils/datetime'
import { DataTable, ExportButton, ListToolbar, PageHeader } from '../../components/ui'

/** 类型筛选项（取值与后端 VALID_TYPES 一致） */
const TYPE_FILTER_OPTIONS = [
  { value: '', label: '全部类型' },
  ...Object.entries(POINT_TX_TYPE_MAP).map(([value, cfg]) => ({ value, label: cfg.text })),
]

/**
 * 收支方向：按 `points` 正负过滤，**不能由类型推导** ——
 * `admin_set`（管理员设置）的变动值可正可负，只有看数值本身才知道是收还是支。
 */
const DIRECTION_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'income', label: '收入' },
  { value: 'expense', label: '支出' },
]

export default function PointsList() {
  // 搜索：keywordInput 仅受控输入，回车/点击后才提交进查询参数
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [type, setType] = useState('')
  const [direction, setDirection] = useState('')
  /** 时间范围：存 dayjs 以便回填控件，提交时才转 ISO（带时区偏移，后端统一换算成 UTC） */
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null)

  const listParams: Record<string, string | number | boolean> = {}
  if (keyword.trim()) listParams.keyword = keyword.trim()
  if (type) listParams.type = type
  if (direction) listParams.direction = direction
  if (range) {
    listParams.start_at = range[0].startOf('day').toISOString()
    listParams.end_at = range[1].endOf('day').toISOString()
  }

  const { items, loading, page, pageSize, total, refresh, setPage } = useCrudList<PointTransaction>({
    basePath: '/admin/points/transactions',
    params: listParams,
  })

  const hasFilter = !!keyword.trim() || !!type || !!direction || !!range

  const handleSearch = (value: string) => {
    setKeyword(value)
    setPage(1)
  }

  const handleResetFilters = () => {
    setKeywordInput('')
    setKeyword('')
    setType('')
    setDirection('')
    setRange(null)
    setPage(1)
  }

  const columns: TableProps<PointTransaction>['columns'] = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (v: string | null) => <span className="adm-muted">{formatDateTime(v)}</span>,
    },
    {
      title: '用户',
      dataIndex: 'user_account',
      key: 'user_account',
      width: 200,
      render: (v: string | undefined) => <span className="adm-cell__name">{v || '—'}</span>,
    },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      width: 110,
      render: (v: string) => {
        const cfg = POINT_TX_TYPE_MAP[v as keyof typeof POINT_TX_TYPE_MAP] || { color: 'default', text: v }
        return (
          <Tag color={cfg.color} style={{ margin: 0 }}>
            {cfg.text}
          </Tag>
        )
      },
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      // 显式给宽度：不给的话这一列会吃掉全部剩余空间，描述与「变动」之间留出大片空白
      // （table-layout: fixed 下无宽度列独占余量，有宽度的列则按比例分摊）
      width: 380,
      render: (v: string | null) => (
        <Typography.Text
          type={v ? undefined : 'secondary'}
          ellipsis={v ? { tooltip: v } : false}
          style={{ maxWidth: 360 }}
        >
          {v || '—'}
        </Typography.Text>
      ),
    },
    {
      title: '操作人',
      dataIndex: 'operator_account',
      key: 'operator_account',
      width: 180,
      render: (_: unknown, tx: PointTransaction) => {
        // 账号解析不到时回落到 id：consume / redeem 是系统写入（无操作人），
        // 管理员账号被删后也只剩 id —— 两种都不该显示成空白
        if (!tx.operator_id) return <span className="adm-muted">系统写入</span>
        const label = tx.operator_account || `#${tx.operator_id}`
        return (
          <Typography.Text ellipsis={{ tooltip: label }} style={{ maxWidth: 160 }}>
            {label}
          </Typography.Text>
        )
      },
    },
    {
      title: '变动',
      dataIndex: 'points',
      key: 'points',
      width: 100,
      align: 'right',
      render: (v: number) => (
        // 颜色沿用既有口径（收入绿 / 支出橙），0 变动单独用灰色 —— 它是数据异常，不该看起来像收入
        <span className="adm-delta" style={{ color: v > 0 ? '#52c41a' : v < 0 ? '#fa8c16' : '#bfbfbf' }}>
          {v > 0 ? `+${v}` : v}
        </span>
      ),
    },
    {
      title: '变动后余额',
      dataIndex: 'balance_after',
      key: 'balance_after',
      width: 110,
      align: 'right',
      render: (v: number) => <span className="adm-num">{v}</span>,
    },
  ]

  return (
    <div className="adm-page">
      <PageHeader
        icon={<WalletOutlined />}
        title="积分流水"
        subtitle="全站积分变动的不可变记录，仅供查看"
        count={{ total }}
        actions={
          <>
            <Input.Search
              placeholder="搜索用户账号"
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              onSearch={handleSearch}
              style={{ width: 220 }}
              allowClear
            />
            {/* 导出带上当前筛选：导出的必须是「页面看到的这批」，不是全表 */}
            <ExportButton
              url="/admin/export/point-transactions"
              params={listParams}
              label="积分流水"
              fallbackName="point-transactions.csv"
            />
            <Tooltip title="重新拉取当前条件下的流水">
              <Button icon={<ReloadOutlined />} onClick={refresh}>
                刷新
              </Button>
            </Tooltip>
          </>
        }
      />

      {/* 工具条：三个筛选维度 + 重置（本模块无任何写操作，故没有批量按钮） */}
      <ListToolbar
        hasFilter={hasFilter}
        onReset={handleResetFilters}
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            时间按本地时区取整日，查询时自动换算为库内 UTC
          </Typography.Text>
        }
      >
        <Select
          value={type}
          onChange={(v) => {
            setType(v)
            setPage(1)
          }}
          options={TYPE_FILTER_OPTIONS}
          style={{ width: 140 }}
        />
        <Segmented
          value={direction}
          onChange={(v) => {
            setDirection(String(v))
            setPage(1)
          }}
          options={DIRECTION_OPTIONS}
        />
        <DatePicker.RangePicker
          value={range}
          onChange={(dates) => {
            setRange(dates && dates[0] && dates[1] ? [dates[0], dates[1]] : null)
            setPage(1)
          }}
          placeholder={['开始日期', '结束日期']}
          allowClear
        />
      </ListToolbar>

      <DataTable<PointTransaction>
        dataSource={items}
        columns={columns}
        loading={loading}
        scrollX={1250}
        pagination={{ page, pageSize, total, onPageChange: setPage }}
        empty={{
          hasFilter,
          filteredText: '没有符合筛选条件的流水',
          emptyText: '暂无积分流水',
          onReset: handleResetFilters,
        }}
      />

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        提示：流水由工具消耗、教程购买、兑换码充值、管理员调整自动写入，后台不支持修改或删除；
        「变动后余额」是该笔变动发生后的余额快照，可用于逐笔对账。
      </Typography.Text>
    </div>
  )
}
