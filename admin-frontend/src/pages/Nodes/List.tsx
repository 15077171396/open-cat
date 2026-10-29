import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Card, Input, Modal, Select, Space, Table, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import { AppstoreOutlined, DeleteOutlined, EditOutlined, EyeOutlined, ReloadOutlined } from '@ant-design/icons'
import { apiPost } from '../../api/client'
import { useCrudList } from '../../hooks/useCrudList'
import ContentThumb from '../../components/ContentThumb'
import { PermGuard } from '../../components/PermGuard'
import { useCategoryOptions } from '../../hooks/useCategoryOptions'
import { getErrorMessage } from '../../utils/error'
import { formatDate } from '../../utils/datetime'
import { shouldRetreatOnePage } from '../../utils/pagination'
import { categoryMeta } from '../../utils/categoryColor'
import { NODE_CATEGORIES } from '../../constants'
import type { Node } from '../../types'

import { toast } from '../../utils/toast'
import { TableEmpty } from '../../components/TableEmpty'
import PageHeader from '../../components/ui/PageHeader'
/** 千分位；缺值显示「—」而不是 0（旧后端不返回该字段时不该谎报 0） */
function formatCount(value: number | undefined | null): string {
  if (typeof value !== 'number') return '—'
  return value.toLocaleString('zh-CN')
}

export default function NodesList() {
  const navigate = useNavigate()
  const categories = useCategoryOptions('node', NODE_CATEGORIES)

  // 搜索：keywordInput 仅受控输入，回车/点击后才提交进查询参数
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  // 下拉筛选：空字符串表示不筛选
  const [category, setCategory] = useState('')

  const params: Record<string, string> = {}
  if (keyword.trim()) params.keyword = keyword.trim()
  if (category) params.category = category

  const { items, loading, page, pageSize, total, handleDelete, setPage, refresh } = useCrudList<Node>({
    basePath: '/admin/nodes',
    params: Object.keys(params).length ? params : undefined,
    deleteTitle: '确认删除',
    deleteSuccessMessage: '删除成功',
  })

  // 批量选择与操作
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [batchLoading, setBatchLoading] = useState(false)

  const handleSearch = (value: string) => {
    setKeyword(value)
    // 换了筛选条件就把选择清掉：否则「选中 A 页的几条 → 改筛选 → 批量删除」会删掉看不见的行
    setSelectedIds([])
    setPage(1)
  }

  const handleFilterChange = () => {
    setSelectedIds([])
    setPage(1)
  }

  /** 清空全部筛选条件并回到第一页 */
  const handleResetFilters = () => {
    setKeywordInput('')
    setKeyword('')
    setCategory('')
    setSelectedIds([])
    setPage(1)
  }

  const hasFilter = !!keyword.trim() || !!category

  const handleBatchDelete = () => {
    Modal.confirm({
      title: '批量删除',
      content: `确认删除选中的 ${selectedIds.length} 个节点？不可恢复。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        setBatchLoading(true)
        try {
          const data = await apiPost<{ deleted: number }>('/admin/nodes/batch-delete', {
            ids: selectedIds,
          })
          toast.success(`已删除 ${data.deleted} 个节点`)
          setSelectedIds([])
          // 当前页删空时回退一页，避免停留在越界空页
          if (shouldRetreatOnePage(total - data.deleted, page, pageSize)) {
            setPage(page - 1)
          } else {
            refresh()
          }
        } catch (err) {
          toast.error(getErrorMessage(err, '批量删除失败'))
        } finally {
          setBatchLoading(false)
        }
      },
    })
  }

  const columns: TableProps<Node>['columns'] = [
    {
      title: '节点',
      dataIndex: 'name',
      key: 'name',
      width: 560,
      render: (_: unknown, n: Node) => {
        const meta = categoryMeta(n.category)
        return (
          <div className="adm-cell">
            <ContentThumb cover={n.cover_image} gradient={meta.gradient} icon={<AppstoreOutlined />} />
            <div className="adm-cell__body">
              <div className="adm-cell__name" title={n.name}>
                {n.name}
              </div>
              {n.description && <div className="adm-cell__desc">{n.description}</div>}
            </div>
          </div>
        )
      },
    },
    {
      title: '分类',
      dataIndex: 'category',
      key: 'category',
      width: 150,
      render: (v: string) => {
        const meta = categoryMeta(v)
        return (
          <span className="adm-typepill" style={{ color: meta.text, background: meta.bg }}>
            {v}
          </span>
        )
      },
    },
    {
      title: '预览',
      dataIndex: 'view_count',
      key: 'view_count',
      width: 110,
      align: 'right',
      render: (v: number | undefined) => (
        <span className="adm-metric">
          <EyeOutlined />
          {formatCount(v)}
        </span>
      ),
    },
    {
      title: '最近更新',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 140,
      render: (v: string | null | undefined) => <span className="adm-muted">{formatDate(v)}</span>,
    },
    {
      title: '操作',
      key: 'action',
      width: 170,
      fixed: 'right',
      render: (_: unknown, n: Node) => (
        <Space size={0}>
          <PermGuard perm="node:edit">
            <Button
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={() => navigate(`/nodes/${n.id}/edit`)}
            >
              编辑
            </Button>
          </PermGuard>
          <PermGuard perm="node:delete">
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={() => handleDelete(n.id, `「${n.name}」`)}
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
      {/* 页头 */}
      <PageHeader
        icon={<AppstoreOutlined />}
        title="知识百科"
        subtitle="维护节点词条、参数说明与正文内容"
        count={{ total: total }}
        actions={
          <>
            <Input.Search
              placeholder="搜索名称 / 简介"
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              onSearch={handleSearch}
              style={{ width: 240 }}
              allowClear
            />
            <PermGuard perm="node:create">
              <Button type="primary" icon={<AppstoreOutlined />} onClick={() => navigate('/nodes/new')}>
                新建节点
              </Button>
            </PermGuard>

          </>
        }
      />

      {/* 工具条：筛选 + 批量操作 */}
      <Card className="adm-toolbar" variant="outlined">
        <div className="adm-toolbar__row">
          <Space wrap size={12}>
            <Select
              value={category}
              onChange={(v) => {
                setCategory(v)
                handleFilterChange()
              }}
              options={[{ value: '', label: '全部分类' }, ...categories.map((c) => ({ value: c, label: c }))]}
              style={{ width: 150 }}
            />
            {hasFilter && (
              <Tooltip title="清空关键词与筛选条件">
                <Button type="text" size="small" icon={<ReloadOutlined />} onClick={handleResetFilters}>
                  重置
                </Button>
              </Tooltip>
            )}
          </Space>
          <Space size={12}>
            {selectedIds.length > 0 && (
              <span className="adm-selinfo">
                已选 <strong>{selectedIds.length}</strong> 项
                <Button type="link" size="small" onClick={() => setSelectedIds([])}>
                  取消选择
                </Button>
              </span>
            )}
            <PermGuard perm="node:delete">
              <Button
                danger
                icon={<DeleteOutlined />}
                disabled={!selectedIds.length}
                loading={batchLoading}
                onClick={handleBatchDelete}
              >
                批量删除
              </Button>
            </PermGuard>
          </Space>
        </div>
      </Card>

      {/* 列表 */}
      <Card className="adm-tablecard" variant="outlined">
        <Table<Node>
          className="adm-table"
          dataSource={items}
          columns={columns}
          rowKey="id"
          loading={loading}
          scroll={{ x: 1130 }}
          rowSelection={{
            selectedRowKeys: selectedIds,
            onChange: (keys) => setSelectedIds(keys as number[]),
          }}
          locale={{
            emptyText: (
              <TableEmpty
                hasFilter={hasFilter}
                filteredText='没有符合筛选条件的节点'
                emptyText='还没有节点，先创建第一个吧'
                onReset={handleResetFilters}
                create={{ perm: 'node:create', icon: <AppstoreOutlined />, label: '新建节点', onClick: () => navigate('/nodes/new') }}
              />
          ),
          }}
          pagination={{
            current: page,
            pageSize,
            total,
            onChange: (p) => {
              setSelectedIds([])
              setPage(p)
            },
            showTotal: (t) => `共 ${t} 条`,
            showSizeChanger: false,
          }}
        />
      </Card>

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        提示：批量删除会同时清除所选节点，操作不可恢复。
      </Typography.Text>
    </div>
  )
}
