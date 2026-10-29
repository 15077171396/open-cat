import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Card, Input, Modal, Select, Space, Table, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import { ApiOutlined, DeleteOutlined, EditOutlined, EyeOutlined, ReloadOutlined } from '@ant-design/icons'
import { apiPost } from '../../api/client'
import { useCrudList } from '../../hooks/useCrudList'
import ContentThumb from '../../components/ContentThumb'
import { PermGuard } from '../../components/PermGuard'
import { useCategoryOptions } from '../../hooks/useCategoryOptions'
import { getErrorMessage } from '../../utils/error'
import { formatDate } from '../../utils/datetime'
import { shouldRetreatOnePage } from '../../utils/pagination'
import { categoryMeta, type CategoryMeta } from '../../utils/categoryColor'
import { PLUGIN_CATEGORIES } from '../../constants'
import type { Plugin } from '../../types'

import { toast } from '../../utils/toast'
import { TableEmpty } from '../../components/TableEmpty'
/**
 * 三个内置分类的配色与前台扩展卡片对齐（geekblue / cyan / green）。
 * 分类值可被运营改动，取不到就退回哈希取色 —— 与「PLUGIN_CARD_META 的 key 必须
 * 等于分类值」是同一类漂移风险，这里取不到色只影响观感，不会静默错成别的分类。
 */
const PLUGIN_CATEGORY_STYLE: Record<string, CategoryMeta> = {
  'ComfyUI 节点': {
    text: '#2f54eb',
    bg: '#f0f5ff',
    gradient: 'linear-gradient(135deg, #2f54eb, #597ef7)',
  },
  'Coze 扩展': {
    text: '#006d75',
    bg: '#e6fffb',
    gradient: 'linear-gradient(135deg, #13c2c2, #36cfc9)',
  },
  '其他扩展': {
    text: '#389e0d',
    bg: '#f6ffed',
    gradient: 'linear-gradient(135deg, #52c41a, #73d13d)',
  },
}

const pluginCategoryMeta = (v: string): CategoryMeta => PLUGIN_CATEGORY_STYLE[v] || categoryMeta(v)

/** 千分位；缺值显示「—」而不是 0 */
function formatCount(value: number | undefined | null): string {
  if (typeof value !== 'number') return '—'
  return value.toLocaleString('zh-CN')
}

export default function PluginsList() {
  const navigate = useNavigate()
  const categories = useCategoryOptions('plugin', PLUGIN_CATEGORIES.map((c) => c.value))

  // 搜索：keywordInput 仅受控输入，回车/点击后才提交进查询参数
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  // 下拉筛选：空字符串表示不筛选
  const [category, setCategory] = useState('')

  const params: Record<string, string> = {}
  if (keyword.trim()) params.keyword = keyword.trim()
  if (category) params.category = category

  const { items, loading, page, pageSize, total, handleDelete, setPage, refresh } = useCrudList<Plugin>({
    basePath: '/admin/plugins',
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
      content: `确认删除选中的 ${selectedIds.length} 个扩展？来源链接将一并清除，不可恢复。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        setBatchLoading(true)
        try {
          const data = await apiPost<{ deleted: number }>('/admin/plugins/batch-delete', {
            ids: selectedIds,
          })
          toast.success(`已删除 ${data.deleted} 个扩展`)
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

  const columns: TableProps<Plugin>['columns'] = [
    {
      title: '扩展',
      dataIndex: 'name',
      key: 'name',
      width: 560,
      render: (_: unknown, p: Plugin) => {
        const meta = pluginCategoryMeta(p.category)
        return (
          <div className="adm-cell">
            {/* 扩展没有封面字段，首列固定为「分类图标色块」（不是图片占位） */}
            <ContentThumb gradient={meta.gradient} icon={<ApiOutlined />} />
            <div className="adm-cell__body">
              <div className="adm-cell__name" title={p.name}>
                {p.name}
              </div>
              {p.description && <div className="adm-cell__desc">{p.description}</div>}
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
        const meta = pluginCategoryMeta(v)
        return (
          <span className="adm-typepill" style={{ color: meta.text, background: meta.bg }}>
            {v}
          </span>
        )
      },
    },
    {
      title: '浏览量',
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
      render: (_: unknown, p: Plugin) => (
        <Space size={0}>
          <PermGuard perm="plugin:edit">
            <Button
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={() => navigate(`/plugins/${p.id}/edit`)}
            >
              编辑
            </Button>
          </PermGuard>
          <PermGuard perm="plugin:delete">
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={() => handleDelete(p.id, `「${p.name}」`)}
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
      <div className="adm-header">
        <div className="adm-header__main">
          <span className="adm-header__icon">
            <ApiOutlined />
          </span>
          <div style={{ minWidth: 0 }}>
            <div className="adm-header__title">扩展管理</div>
            <div className="adm-header__sub">
              <span>维护扩展条目、获取来源与正文内容</span>
              <span className="adm-header__dot">·</span>
              <span>
                共 <strong>{total}</strong> 条
              </span>
            </div>
          </div>
        </div>
        <div className="adm-header__actions">
          <Input.Search
            placeholder="搜索名称 / 简介"
            value={keywordInput}
            onChange={(e) => setKeywordInput(e.target.value)}
            onSearch={handleSearch}
            style={{ width: 240 }}
            allowClear
          />
          <PermGuard perm="plugin:create">
            <Button type="primary" icon={<ApiOutlined />} onClick={() => navigate('/plugins/new')}>
              新建扩展
            </Button>
          </PermGuard>
        </div>
      </div>

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
              style={{ width: 180 }}
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
            <PermGuard perm="plugin:delete">
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
        <Table<Plugin>
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
                filteredText='没有符合筛选条件的扩展'
                emptyText='还没有扩展，先创建第一个吧'
                onReset={handleResetFilters}
                create={{ perm: 'plugin:create', icon: <ApiOutlined />, label: '新建扩展', onClick: () => navigate('/plugins/new') }}
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
        提示：批量删除会同时清除所选扩展的来源链接，操作不可恢复。
      </Typography.Text>
    </div>
  )
}
