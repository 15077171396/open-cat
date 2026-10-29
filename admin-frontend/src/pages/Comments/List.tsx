import { useState } from 'react'
import { Button, Card, DatePicker, Image, Input, Select, Space, Switch, Table, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import type { Dayjs } from 'dayjs'
import { DeleteOutlined, MessageOutlined, ReloadOutlined, RollbackOutlined } from '@ant-design/icons'
import { apiPost } from '../../api/client'
import { useCrudList } from '../../hooks/useCrudList'
import { PermGuard } from '../../components/PermGuard'
import { getErrorMessage } from '../../utils/error'
import { formatTime } from '../../utils/datetime'

import { toast } from '../../utils/toast'
import { TableEmpty } from '../../components/TableEmpty'
import PageHeader from '../../components/ui/PageHeader'
/**
 * 内容评论管理。四条刻意的设计（改造后仍保持不变）：
 * - 删除为**软删除**且可恢复（误删可逆 + 留痕）；
 * - **不支持编辑**评论内容（改用户发言有法律风险，处置只有「删除」这一种）；
 * - **配图必须可见**：纯图片评论正文为空，「只看文字」会漏掉最需人工过目的内容；
 * - **必须展示所属内容**：评论是多态目标，处置前得知道它属于哪篇内容。
 *
 * ⚠️ `TARGET_TYPE_META` 是「可评论内容类型」词汇表的 5 处登记点之一，
 * `backend/tests/test_comment_target_registry.py` 会用正则逐字比对它的键（要求 2 空格缩进）。
 * **不要改名、不要缩进改动、也不要把它搬到后端** —— 详见 `app/models/comment.py` 顶部注释。
 */

/** 可评论的内容类型（与后端 COMMENT_TARGET_TYPES 对齐；新增类型时同步这里） */
const TARGET_TYPE_META: Record<string, { label: string; color: string }> = {
  tutorial: { label: '教程', color: 'blue' },
}

function targetTypeLabel(t: string): string {
  return TARGET_TYPE_META[t]?.label ?? t
}

interface CommentItem {
  id: number
  target_type: string
  target_id: number
  target_title: string | null
  user_id: number
  /** NULL = 顶层评论；非 NULL = 挂在某个顶层评论下 */
  parent_id: number | null
  author_nickname: string
  author_account: string | null
  content: string
  images: string[]
  is_deleted: boolean
  created_at: string | null
  deleted_at: string | null
}

/** 删除确认里对这条评论的简述：纯图片评论没有正文，用「N 张图」代替 */
function describeComment(record: CommentItem): string {
  const text = record.content.trim()
  if (text) return `「${text.slice(0, 20)}${text.length > 20 ? '…' : ''}」`
  return `「${record.images?.length ?? 0} 张图片」`
}

export default function CommentsList() {
  // 搜索：keywordInput 仅受控输入，回车/点击后才提交进查询参数
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [targetType, setTargetType] = useState('')
  const [targetIdInput, setTargetIdInput] = useState('')
  const [targetId, setTargetId] = useState('')
  const [includeDeleted, setIncludeDeleted] = useState(false)
  /** 时间范围：存 dayjs 以便回填控件，提交时才转 ISO（后端统一换算成 naive UTC） */
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null)
  const [restoring, setRestoring] = useState<number | null>(null)

  const listParams: Record<string, string | number | boolean> = {
    ...(keyword ? { keyword } : {}),
    ...(targetType ? { target_type: targetType } : {}),
    ...(targetId ? { target_id: Number(targetId) } : {}),
    ...(includeDeleted ? { include_deleted: true } : {}),
    ...(range
      ? { start_at: range[0].startOf('day').toISOString(), end_at: range[1].endOf('day').toISOString() }
      : {}),
  }

  const { items, loading, page, pageSize, total, refresh, handleDelete, setPage } =
    useCrudList<CommentItem>({
      basePath: '/admin/comments',
      params: Object.keys(listParams).length ? listParams : undefined,
      deleteTitle: '确认删除评论',
      deleteSuccessMessage: '评论已删除',
    })

  const hasFilter = !!keyword || !!targetType || !!targetId || includeDeleted || !!range

  const handleResetFilters = () => {
    setKeywordInput('')
    setKeyword('')
    setTargetType('')
    setTargetIdInput('')
    setTargetId('')
    setIncludeDeleted(false)
    setRange(null)
    setPage(1)
  }

  const handleRestore = async (record: CommentItem) => {
    setRestoring(record.id)
    try {
      await apiPost(`/admin/comments/${record.id}/restore`)
      toast.success('评论已恢复')
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, '恢复失败'))
    } finally {
      setRestoring(null)
    }
  }

  const columns: TableProps<CommentItem>['columns'] = [
    {
      title: '所属内容',
      dataIndex: 'target_title',
      key: 'target_title',
      width: 220,
      render: (title: string | null, row: CommentItem) =>
        title ? (
          <Typography.Text ellipsis={{ tooltip: title }} style={{ maxWidth: 200 }}>
            {title}
          </Typography.Text>
        ) : (
          // 内容已被硬删除时标题回查不到 —— 退回「类型 + ID」，别显示成空白
          <span className="adm-muted">
            {targetTypeLabel(row.target_type)} {row.target_id}
          </span>
        ),
    },
    {
      title: '内容类型',
      dataIndex: 'target_type',
      key: 'target_type',
      width: 90,
      render: (v: string) => (
        <Tag color={TARGET_TYPE_META[v]?.color ?? 'default'} style={{ margin: 0 }}>
          {targetTypeLabel(v)}
        </Tag>
      ),
    },
    {
      title: '评论人',
      dataIndex: 'author_nickname',
      key: 'author_nickname',
      width: 140,
      render: (v: string) => <span className="adm-cell__name">{v || '—'}</span>,
    },
    {
      title: '评论人账号',
      dataIndex: 'author_account',
      key: 'author_account',
      width: 150,
      render: (v: string | null) =>
        v ? (
          <Typography.Text ellipsis={{ tooltip: v }} style={{ maxWidth: 130 }}>
            {v}
          </Typography.Text>
        ) : (
          <span className="adm-muted">—</span>
        ),
    },
    {
      title: '层级',
      dataIndex: 'parent_id',
      key: 'parent_id',
      width: 90,
      render: (parentId: number | null) =>
        parentId === null ? (
          <Tag color="blue" style={{ margin: 0 }}>
            评论
          </Tag>
        ) : (
          <Tooltip title={`回复父评论 ${parentId}`}>
            <Tag style={{ margin: 0 }}>回复</Tag>
          </Tooltip>
        ),
    },
    {
      title: '评论内容',
      dataIndex: 'content',
      key: 'content',
      width: 260,
      render: (v: string | null, row: CommentItem) => (
        <div>
          {v ? (
            <Typography.Paragraph
              ellipsis={{ tooltip: v, rows: 2 }}
              style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}
            >
              {v}
            </Typography.Paragraph>
          ) : (
            // 纯图片评论正文为空：别渲染成「-」，否则看起来像「这条没有内容」
            <Typography.Text type="secondary">（无正文）</Typography.Text>
          )}
          {/* 配图必须可见：纯图片评论的正文是空的，图就是它的全部内容 */}
          {row.images?.length > 0 && (
            <Image.PreviewGroup>
              <Space size={4} style={{ marginTop: 6 }} wrap>
                {row.images.map((url) => (
                  <Image
                    key={url}
                    src={url}
                    alt="评论配图"
                    width={48}
                    height={48}
                    style={{ objectFit: 'cover', borderRadius: 6, border: '1px solid #e2e8f0' }}
                  />
                ))}
              </Space>
            </Image.PreviewGroup>
          )}
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'is_deleted',
      key: 'is_deleted',
      width: 80,
      render: (deleted: boolean, row: CommentItem) =>
        deleted ? (
          <Tooltip title={row.deleted_at ? `删除于 ${formatTime(row.deleted_at)}` : undefined}>
            <Tag color="red" style={{ margin: 0 }}>
              已删除
            </Tag>
          </Tooltip>
        ) : (
          <Tag color="green" style={{ margin: 0 }}>
            正常
          </Tag>
        ),
    },
    {
      title: '发布时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 130,
      render: (v: string | null) => <span className="adm-muted">{formatTime(v)}</span>,
    },
    {
      title: '操作',
      key: 'action',
      width: 110,
      fixed: 'right',
      render: (_: unknown, record: CommentItem) =>
        record.is_deleted ? (
          <PermGuard perm="comment:delete">
            <Button
              type="link"
              size="small"
              icon={<RollbackOutlined />}
              loading={restoring === record.id}
              onClick={() => handleRestore(record)}
            >
              恢复
            </Button>
          </PermGuard>
        ) : (
          // 删除连带软删其回复，但仍可恢复 —— 确认框里说清后果，别让人以为是不可逆的
          <PermGuard perm="comment:delete">
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={() => handleDelete(record.id, describeComment(record))}
            >
              删除
            </Button>
          </PermGuard>
        ),
    },
  ]

  return (
    <div className="adm-page">
      <PageHeader
        icon={<MessageOutlined />}
        title="内容评论"
        subtitle="删除为软删除，可随时恢复；评论内容不支持编辑"
        count={{ total: total }}
        actions={
          <>
            <Input.Search
              allowClear
              placeholder="搜索评论内容"
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              onSearch={(v) => {
                setKeyword(v.trim())
                setPage(1)
              }}
              style={{ width: 220 }}
            />

          </>
        }
      />

      {/* 工具条：内容类型 / 内容 ID / 含已删除 / 时间范围 */}
      <Card className="adm-toolbar" variant="outlined">
        <div className="adm-toolbar__row">
          <Space wrap size={12}>
            <Select
              allowClear
              placeholder="全部内容类型"
              value={targetType || undefined}
              onChange={(v) => {
                setTargetType(v ?? '')
                setPage(1)
              }}
              style={{ width: 140 }}
              options={Object.entries(TARGET_TYPE_META).map(([value, meta]) => ({
                value,
                label: meta.label,
              }))}
            />
            <Input
              allowClear
              placeholder="按内容 ID 筛选"
              value={targetIdInput}
              onChange={(e) => setTargetIdInput(e.target.value)}
              onPressEnter={(e) => {
                setTargetId((e.target as HTMLInputElement).value.trim())
                setPage(1)
              }}
              style={{ width: 140 }}
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
            <Space size={6}>
              <Switch
                checked={includeDeleted}
                onChange={(v) => {
                  setIncludeDeleted(v)
                  setPage(1)
                }}
                size="small"
              />
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                含已删除
              </Typography.Text>
            </Space>
            {hasFilter && (
              <Tooltip title="清空全部筛选条件">
                <Button type="text" size="small" icon={<ReloadOutlined />} onClick={handleResetFilters}>
                  重置
                </Button>
              </Tooltip>
            )}
          </Space>
          <Tooltip title="重新拉取当前条件下的评论">
            <Button icon={<ReloadOutlined />} onClick={refresh}>
              刷新
            </Button>
          </Tooltip>
        </div>
      </Card>

      <Card className="adm-tablecard" variant="outlined">
        <Table<CommentItem>
          className="adm-table"
          dataSource={items}
          columns={columns}
          rowKey="id"
          loading={loading}
          scroll={{ x: 1270 }}
          locale={{
            emptyText: (
              <TableEmpty
                hasFilter={hasFilter}
                filteredText='没有符合筛选条件的评论'
                emptyText='暂无评论'
                onReset={handleResetFilters}
              />
          ),
          }}
          pagination={{
            current: page,
            pageSize,
            total,
            onChange: setPage,
            showTotal: (t) => `共 ${t} 条`,
            showSizeChanger: false,
          }}
        />
      </Card>

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        提示：删除评论会<b>连带软删其回复</b>（可从「含已删除」里找回并恢复）；处置记录写入操作审计。
        纯图片评论没有正文，配图就是它的全部内容 —— 别只看文字。
      </Typography.Text>
    </div>
  )
}
