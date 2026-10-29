import { useState } from 'react'
import { Button, Input, Segmented, Select, Space, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import { useNavigate } from 'react-router-dom'
import { ArrowDownOutlined, ArrowUpOutlined, DeleteOutlined, EditOutlined, ReloadOutlined, ToolOutlined } from '@ant-design/icons'
import client from '../../api/client'
import { useCrudList } from '../../hooks/useCrudList'
import { useCategoryOptions } from '../../hooks/useCategoryOptions'
import { PermGuard } from '../../components/PermGuard'
import { TOOL_ACCESS_MODES, TOOL_CATEGORIES, TOOL_TYPES } from '../../constants'
import { getErrorMessage } from '../../utils/error'
import type { Tool } from '../../types'

import { toast } from '../../utils/toast'
import { DataTable, ListToolbar, PageHeader, StatusTag, boolTone } from '../../components/ui'
/**
 * 在线工具列表。
 *
 * 后端 `GET /admin/tools` 一直支持 `category` / `tool_type` 两个筛选参数，
 * 但改版前前端只接了 keyword —— 分类与类型只能靠翻页找。这里补上。
 */
export default function ToolsList() {
  const navigate = useNavigate()
  // input 仅受控输入，keyword 才进入查询参数（避免每敲一个字符就发请求）
  const [input, setInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [category, setCategory] = useState('')
  const [toolType, setToolType] = useState('')
  const [togglingId, setTogglingId] = useState<number | null>(null)
  const categoryOptions = useCategoryOptions('tool', TOOL_CATEGORIES)

  const listParams: Record<string, string> = {}
  if (keyword.trim()) listParams.keyword = keyword.trim()
  if (category) listParams.category = category
  if (toolType) listParams.tool_type = toolType

  const { items, loading, page, pageSize, total, handleDelete, setPage, refresh } = useCrudList<Tool>({
    basePath: '/admin/tools',
    deleteTitle: '确认删除',
    deleteSuccessMessage: '删除成功',
    params: Object.keys(listParams).length ? listParams : undefined,
  })

  const hasFilter = !!keyword.trim() || !!category || !!toolType

  const handleResetFilters = () => {
    setInput('')
    setKeyword('')
    setCategory('')
    setToolType('')
    setPage(1)
  }

  const handleTogglePublish = async (t: Tool) => {
    setTogglingId(t.id)
    try {
      await client.patch(`/admin/tools/${t.id}/publish`)
      toast.success(t.is_published ? '已下架' : '已上架')
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, '操作失败'))
    } finally {
      setTogglingId(null)
    }
  }

  const typeLabel = (v: string) => TOOL_TYPES.find((t) => t.value === v)?.label ?? v

  /** 使用权限列渲染：free=免费 / auth=需认证 / points=需积分(含单次消耗) */
  const accessModeTag = (t: Tool) => {
    const mode = t.access_mode || 'auth'
    const cfg = TOOL_ACCESS_MODES.find((m) => m.value === mode)
    const label = cfg?.label ?? mode
    if (mode === 'points') {
      return (
        <Tag color="orange" style={{ margin: 0 }}>
          {label}（{t.points_cost ?? 0} 积分/次）
        </Tag>
      )
    }
    return (
      <Tag color={cfg?.color ?? 'blue'} style={{ margin: 0 }}>
        {label}
      </Tag>
    )
  }

  const columns: TableProps<Tool>['columns'] = [
    {
      title: '工具名称',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      render: (v: string) => <span className="adm-cell__name">{v}</span>,
    },
    {
      title: '分类',
      dataIndex: 'category',
      key: 'category',
      width: 110,
      render: (v: string) => (
        <Tag color="blue" style={{ margin: 0 }}>
          {v}
        </Tag>
      ),
    },
    {
      title: '类型',
      dataIndex: 'tool_type',
      key: 'tool_type',
      width: 100,
      render: (v: string) => (
        <Tag color={v === 'ai' ? 'purple' : 'green'} style={{ margin: 0 }}>
          {typeLabel(v)}
        </Tag>
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      width: 300,
      render: (v: string) =>
        v ? (
          <Typography.Text ellipsis={{ tooltip: v }} style={{ maxWidth: 280 }}>
            {v}
          </Typography.Text>
        ) : (
          <span className="adm-muted">—</span>
        ),
    },
    {
      title: '使用次数',
      dataIndex: 'usage_count',
      key: 'usage_count',
      width: 110,
      align: 'right',
      render: (v: number) => <span className="adm-num">{v ?? 0}</span>,
    },
    {
      title: '使用权限',
      key: 'access_mode',
      width: 150,
      render: (_: unknown, t: Tool) => accessModeTag(t),
    },
    {
      title: '状态',
      dataIndex: 'is_published',
      key: 'is_published',
      width: 90,
      render: (v: boolean) => (
        <StatusTag tone={boolTone(v)}>{v ? '已上架' : '已下架'}</StatusTag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 230,
      fixed: 'right',
      render: (_: unknown, t: Tool) => (
        <Space size={0}>
          <PermGuard perm="tool:edit">
            <Button
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={() => navigate(`/tools/${t.id}/edit`)}
            >
              编辑
            </Button>
          </PermGuard>
          <PermGuard perm="tool:edit">
            <Button
              type="link"
              size="small"
              icon={t.is_published ? <ArrowDownOutlined /> : <ArrowUpOutlined />}
              loading={togglingId === t.id}
              onClick={() => handleTogglePublish(t)}
            >
              {t.is_published ? '下架' : '上架'}
            </Button>
          </PermGuard>
          <PermGuard perm="tool:delete">
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={() => handleDelete(t.id, `工具「${t.name}」`)}
            >
              删除
            </Button>
          </PermGuard>
        </Space>
      ),
    },
  ]

  return (
    <div className="adm-page">
      {/* 页头：不放在 PermGuard 里 —— 原先整个页头（含搜索框）被 tool:create 包着，
          只有「能新建工具」的人才看得见，与其余模块「页头对 tool:view 可见」不一致 */}
      <PageHeader
        icon={<ToolOutlined />}
        title="在线工具"
        subtitle="AI 工具按提示词模板调用模型；前端工具在前台有专用工作台"
        count={{ total, unit: '个' }}
        actions={
          <>
            <Input.Search
              placeholder="搜索名称 / 简介"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onSearch={(v) => {
                setKeyword(v.trim())
                setPage(1)
              }}
              allowClear
              style={{ width: 220 }}
            />
            <PermGuard perm="tool:create">
              <Button type="primary" icon={<ToolOutlined />} onClick={() => navigate('/tools/new')}>
                新建工具
              </Button>
            </PermGuard>
          </>
        }
      />

      {/* 工具条：分类 + 类型（后端一直支持，改版前前端没接） */}
      <ListToolbar
        hasFilter={hasFilter}
        onReset={handleResetFilters}
        extra={
          <Tooltip title="重新拉取当前条件下的工具">
            <Button icon={<ReloadOutlined />} onClick={refresh}>
              刷新
            </Button>
          </Tooltip>
        }
      >
        <Select
          allowClear
          placeholder="全部分类"
          value={category || undefined}
          onChange={(v) => {
            setCategory(v ?? '')
            setPage(1)
          }}
          options={categoryOptions.map((c) => ({ value: c, label: c }))}
          style={{ width: 140 }}
        />
        <Segmented
          value={toolType}
          onChange={(v) => {
            setToolType(String(v))
            setPage(1)
          }}
          options={[{ value: '', label: '全部' }, ...TOOL_TYPES.map((t) => ({ value: t.value, label: t.label }))]}
        />
      </ListToolbar>

      <DataTable<Tool>
        dataSource={items}
        columns={columns}
        loading={loading}
        scrollX={1290}
        pagination={{ page, pageSize, total, onPageChange: setPage }}
        empty={{
          hasFilter,
          filteredText: '没有符合筛选条件的工具',
          emptyText: '还没有工具，先建一个吧',
          onReset: handleResetFilters,
          create: {
            perm: 'tool:create',
            icon: <ToolOutlined />,
            label: '新建工具',
            onClick: () => navigate('/tools/new'),
          },
        }}
      />

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        提示：AI 工具由「提示词模板 + 表单字段」驱动，模板里用 <code>{'{字段名}'}</code> 引用字段；
        前端工具（如图片压缩）在前台需要对应的专用工作台组件，否则会回落到通用表单页。
        上架状态与使用权限直接决定前台能否进入与是否扣积分。
      </Typography.Text>
    </div>
  )
}
