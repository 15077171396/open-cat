import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Badge, Button, Input, Modal, Space, Tabs, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import { DeleteOutlined, ReloadOutlined, UndoOutlined } from '@ant-design/icons'
import { apiGet, apiPost } from '../../api/client'
import { getErrorMessage } from '../../utils/error'
import { formatDateTime } from '../../utils/datetime'
import { DEFAULT_PAGE_SIZE } from '../../constants'
import { toast } from '../../utils/toast'
import PageHeader from '../../components/ui/PageHeader'
import { DataTable, ListToolbar } from '../../components/ui'
import type { RecycleBinItem, RecycleBinSummary } from '../../types'

/**
 * 统一回收站：教程 / 评论 / 用户三类软删除记录。
 *
 * 计数与可恢复性来自后端（`can_restore`），前端不复制权限表；恢复动作仍走各业务域
 * 自己的服务，因此「教程恢复成草稿、评论恢复后重新可见、用户恢复原数据」的语义不会漂移。
 */

const API_SLUG: Record<string, string> = {
  tutorial: 'tutorials',
  comment: 'comments',
  user: 'users',
}

const RESTORE_HINT: Record<string, string> = {
  tutorial: '教程会恢复为草稿，需要再点一次「发布」才会重新上线；有购买记录的教程，购买权益仍在。',
  comment: '评论会重新对访客可见（若所属内容仍在线）；同一时刻被连带删除的回复会一起恢复。',
  user: '账号会恢复为注销前的状态，积分、会员与历史数据保留；用户需要重新登录。',
}

export interface RetentionText {
  text: string
  tone: 'muted' | 'warning' | 'danger'
}

export function retentionText(item: RecycleBinItem): RetentionText {
  if (item.retention_days === null || item.remaining_days === null) {
    return { text: item.retention_note || '不自动清理', tone: 'muted' }
  }
  if (item.remaining_days <= 0) {
    return { text: '已到期，等待清理', tone: 'danger' }
  }
  return {
    text: `剩余 ${item.remaining_days} 天`,
    tone: item.remaining_days <= 7 ? 'danger' : 'warning',
  }
}

const TONE_COLOR: Record<RetentionText['tone'], string> = {
  muted: 'default',
  warning: 'orange',
  danger: 'red',
}

function ContentCell({ item }: { item: RecycleBinItem }) {
  return (
    <div className="rb-cell">
      <Typography.Paragraph
        className="rb-cell__title"
        ellipsis={{ tooltip: item.title, rows: 1 }}
        style={{ marginBottom: 0 }}
      >
        {item.title}
      </Typography.Paragraph>
      <span className="rb-cell__sub">{item.subtitle || item.type_label}</span>
    </div>
  )
}

export default function RecycleBinPage() {
  const [summary, setSummary] = useState<RecycleBinSummary>({ total: 0, items: [] })
  const [summaryLoading, setSummaryLoading] = useState(true)
  const [type, setType] = useState('')
  const [items, setItems] = useState<RecycleBinItem[]>([])
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const seqRef = useRef(0)

  const loadList = useCallback(async (itemType: string, nextPage: number, nextKeyword: string) => {
    if (!itemType) return
    const seq = ++seqRef.current
    setLoading(true)
    try {
      const params: Record<string, string | number> = {
        page: nextPage,
        page_size: DEFAULT_PAGE_SIZE,
      }
      if (nextKeyword.trim()) params.keyword = nextKeyword.trim()
      const data = await apiGet<{ items: RecycleBinItem[]; total: number }>(
        `/admin/recycle-bin/${API_SLUG[itemType]}`,
        { params },
      )
      if (seq !== seqRef.current) return
      setItems(data.items || [])
      setTotal(data.total ?? 0)
    } catch (err) {
      if (seq === seqRef.current) toast.error(getErrorMessage(err, '加载回收站失败'))
    } finally {
      if (seq === seqRef.current) setLoading(false)
    }
  }, [])

  const loadSummary = useCallback(async (preferredType?: string) => {
    setSummaryLoading(true)
    try {
      const data = await apiGet<RecycleBinSummary>('/admin/recycle-bin/summary')
      setSummary(data)
      const types = data.items || []
      const nextType =
        (preferredType && types.some((item) => item.type === preferredType) && preferredType) ||
        types.find((item) => item.count > 0)?.type ||
        types[0]?.type ||
        ''
      setType(nextType)
      setPage(1)
      setSelectedIds([])
      if (nextType) await loadList(nextType, 1, keyword)
    } catch (err) {
      toast.error(getErrorMessage(err, '加载回收站汇总失败'))
    } finally {
      setSummaryLoading(false)
    }
  }, [keyword, loadList])

  useEffect(() => {
    void loadSummary()
    // 首次进入只加载一次；后续由类型/搜索/分页显式触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const current = useMemo(
    () => summary.items.find((item) => item.type === type) || null,
    [summary.items, type],
  )

  const handleTypeChange = (nextType: string) => {
    setType(nextType)
    setPage(1)
    setKeyword('')
    setKeywordInput('')
    setSelectedIds([])
    setTotal(0)
    void loadList(nextType, 1, '')
  }

  const handleSearch = (value: string) => {
    setKeyword(value)
    setKeywordInput(value)
    setPage(1)
    setSelectedIds([])
    void loadList(type, 1, value)
  }

  const handleReset = () => {
    setKeyword('')
    setKeywordInput('')
    setPage(1)
    setSelectedIds([])
    void loadList(type, 1, '')
  }

  const handlePageChange = (nextPage: number) => {
    setPage(nextPage)
    setSelectedIds([])
    void loadList(type, nextPage, keyword)
  }

  const doRestore = async (ids: number[]) => {
    if (!type || ids.length === 0) return
    try {
      const result = await apiPost<{ restored?: number }>(
        `/admin/recycle-bin/${API_SLUG[type]}/restore`,
        { ids },
      )
      toast.success(`已恢复 ${result?.restored ?? ids.length} 条${current?.type_label ?? '记录'}`)
      await loadSummary(type)
    } catch (err) {
      toast.error(getErrorMessage(err, '恢复失败'))
    }
  }

  const confirmRestore = (ids: number[]) => {
    if (ids.length === 0) return
    Modal.confirm({
      title: `恢复 ${ids.length} 条${current?.type_label ?? '记录'}？`,
      content: RESTORE_HINT[type] || '恢复后可重新在正常列表中看到。',
      okText: '恢复',
      cancelText: '取消',
      onOk: () => doRestore(ids),
    })
  }

  const canRestore = current?.can_restore ?? false

  const columns: TableProps<RecycleBinItem>['columns'] = [
    {
      title: '内容',
      key: 'content',
      width: 280,
      render: (_: unknown, item: RecycleBinItem) => <ContentCell item={item} />,
    },
    {
      title: '删除时间',
      dataIndex: 'deleted_at',
      key: 'deleted_at',
      width: 140,
      render: (value: string | null) => (
        <span className="adm-muted">{formatDateTime(value)}</span>
      ),
    },
    {
      title: '保留期',
      key: 'retention',
      width: 110,
      render: (_: unknown, item: RecycleBinItem) => {
        const retention = retentionText(item)
        const tag = (
          <Tag color={TONE_COLOR[retention.tone]} style={{ margin: 0 }}>
            {retention.text}
          </Tag>
        )
        return item.expires_at ? (
          <Tooltip title={`预计 ${formatDateTime(item.expires_at)} 清理`}>{tag}</Tooltip>
        ) : (
          tag
        )
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_: unknown, item: RecycleBinItem) => (
        <Button
          type="link"
          size="small"
          icon={<UndoOutlined />}
          disabled={!item.can_restore}
          onClick={() => confirmRestore([item.id])}
        >
          恢复
        </Button>
      ),
    },
  ]

  const tabItems = summary.items.map((item) => ({
    key: item.type,
    label: (
      <Space size={6}>
        {item.type_label}
        <Badge count={item.count} size="small" showZero overflowCount={999} />
      </Space>
    ),
  }))

  return (
    <div className="adm-page">
      <PageHeader
        icon={<DeleteOutlined />}
        title="回收站"
        subtitle={[
          '统一查看与恢复已删除的教程 / 评论 / 用户',
          '教程 90 天、评论 30 天自动清理；用户账号与有购买记录的教程不自动清理',
        ]}
        count={{ total, unit: '条' }}
        actions={
          <Button
            icon={<ReloadOutlined />}
            onClick={() => void loadSummary(type)}
            loading={summaryLoading}
          >
            刷新
          </Button>
        }
      />

      {summary.items.length === 0 && !summaryLoading ? (
        <Alert
          type="info"
          showIcon
          title="没有可查看的回收站类型"
          description="当前账号没有教程 / 评论 / 用户的查看权限。"
        />
      ) : (
        <>
          <Tabs
            activeKey={type || undefined}
            onChange={handleTypeChange}
            items={tabItems}
            className="rb-tabs"
          />

          <ListToolbar
            hasFilter={!!keyword.trim()}
            onReset={handleReset}
            extra={
              <Space>
                <Button
                  type="primary"
                  danger
                  icon={<UndoOutlined />}
                  disabled={!canRestore || selectedIds.length === 0}
                  onClick={() => confirmRestore(selectedIds)}
                >
                  恢复选中{selectedIds.length > 0 ? `（${selectedIds.length}）` : ''}
                </Button>
              </Space>
            }
          >
            <Input.Search
              allowClear
              placeholder="搜索标题 / 内容 / 账号"
              value={keywordInput}
              onChange={(event) => setKeywordInput(event.target.value)}
              onSearch={handleSearch}
              style={{ width: 260 }}
            />
          </ListToolbar>

          <DataTable<RecycleBinItem>
            dataSource={items}
            columns={columns}
            rowKey="id"
            loading={loading || summaryLoading}
            scrollX={610}
            rowSelection={{
              selectedRowKeys: selectedIds,
              onChange: (keys) => setSelectedIds(keys as number[]),
              getCheckboxProps: (item) => ({ disabled: !item.can_restore }),
            }}
            pagination={{
              page,
              pageSize: DEFAULT_PAGE_SIZE,
              total,
              onPageChange: handlePageChange,
            }}
            empty={{
              hasFilter: !!keyword.trim(),
              filteredText: '没有符合搜索条件的回收站记录',
              emptyText: '回收站是空的',
              onReset: handleReset,
            }}
          />
        </>
      )}
    </div>
  )
}
