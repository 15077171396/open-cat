import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Form, Input, Popconfirm, Space, Switch, Tag } from 'antd'
import type { TableProps } from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined, SolutionOutlined } from '@ant-design/icons'
import { apiDelete, apiGet, apiPost, apiPut } from '../../api/client'
import { useAuthStore } from '../../store/authStore'
import { toast } from '../../utils/toast'
import { getErrorMessage } from '../../utils/error'
import { PageHeader, ListToolbar, DataTable, ModalForm } from '../../components/ui'

interface PositionRow {
  id: number
  code: string
  name: string
  description: string | null
  sort_order: number
  is_enabled: boolean
}

interface PositionForm {
  code: string
  name: string
  description?: string
  sort_order?: number
  is_enabled?: boolean
}

/** 岗位目录：组织架构的独立一层；权限复用部门权限。 */
export default function Positions() {
  const { isSuper, permissions } = useAuthStore()
  const canEdit = isSuper || (permissions?.includes('department:edit') ?? false)
  const [rows, setRows] = useState<PositionRow[]>([])
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<PositionRow | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiGet<{ items: PositionRow[] }>('/admin/positions')
      setRows(data.items ?? [])
    } catch (error) {
      toast.error(getErrorMessage(error, '岗位加载失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const filtered = useMemo(() => {
    const needle = keyword.trim().toLowerCase()
    if (!needle) return rows
    return rows.filter((row) =>
      [row.name, row.code, row.description ?? ''].some((value) =>
        value.toLowerCase().includes(needle),
      ),
    )
  }, [rows, keyword])

  const submit = async (values: PositionForm) => {
    setOpen(false)
    try {
      if (editing) {
        await apiPut(`/admin/positions/${editing.id}`, {
          name: values.name,
          description: values.description || null,
          sort_order: Number(values.sort_order ?? 0),
          is_enabled: values.is_enabled ?? true,
        })
        toast.success('岗位已更新')
      } else {
        await apiPost('/admin/positions', {
          code: values.code,
          name: values.name,
          description: values.description || null,
          sort_order: Number(values.sort_order ?? 0),
          is_enabled: values.is_enabled ?? true,
        })
        toast.success('岗位已创建')
      }
      setEditing(null)
      await load()
    } catch (error) {
      toast.error(getErrorMessage(error, '保存失败'))
    }
  }

  const remove = async (row: PositionRow) => {
    try {
      await apiDelete(`/admin/positions/${row.id}`)
      toast.success('岗位已删除')
      await load()
    } catch (error) {
      toast.error(getErrorMessage(error, '删除失败'))
    }
  }

  const toggle = async (row: PositionRow, checked: boolean) => {
    try {
      await apiPut(`/admin/positions/${row.id}`, { is_enabled: checked })
      await load()
    } catch (error) {
      toast.error(getErrorMessage(error, '状态保存失败'))
    }
  }

  const columns: TableProps<PositionRow>['columns'] = [
    {
      title: '岗位名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, row) => (
        <Space size={6}>
          <span>{name}</span>
          {!row.is_enabled ? <Tag>停用</Tag> : null}
        </Space>
      ),
    },
    { title: '编码', dataIndex: 'code', key: 'code', render: (code: string) => <code>{code}</code> },
    {
      title: '说明',
      dataIndex: 'description',
      key: 'description',
      render: (value: string | null) => value || <span style={{ color: 'var(--app-text-3)' }}>—</span>,
    },
    { title: '排序', dataIndex: 'sort_order', key: 'sort_order', width: 80 },
    {
      title: '启用',
      dataIndex: 'is_enabled',
      key: 'is_enabled',
      width: 80,
      render: (enabled: boolean, row) => (
        <Switch
          size="small"
          checked={enabled}
          disabled={!canEdit}
          onChange={(checked) => void toggle(row, checked)}
          aria-label={`${row.name} 是否启用`}
        />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      render: (_, row) =>
        canEdit ? (
          <Space size={4}>
            <Button
              size="small"
              type="link"
              icon={<EditOutlined />}
              onClick={() => {
                setEditing(row)
                setOpen(true)
              }}
            >
              编辑
            </Button>
            <Popconfirm title={`删除岗位「${row.name}」？`} onConfirm={() => void remove(row)}>
              <Button size="small" type="link" danger icon={<DeleteOutlined />}>
                删除
              </Button>
            </Popconfirm>
          </Space>
        ) : null,
    },
  ]

  return (
    <div>
      <PageHeader
        icon={<SolutionOutlined />}
        title="岗位管理"
        subtitle="维护组织架构中的岗位目录；用户岗位绑定在后续版本接入"
        actions={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新
            </Button>
            {canEdit ? (
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => {
                  setEditing(null)
                  setOpen(true)
                }}
              >
                新建岗位
              </Button>
            ) : null}
          </Space>
        }
      />

      <ListToolbar
        hasFilter={keyword.trim().length > 0}
        onReset={() => setKeyword('')}
        extra={<span style={{ color: 'var(--app-text-3)', fontSize: 12 }}>共 {filtered.length} 个岗位</span>}
      >
        <Input.Search
          allowClear
          placeholder="搜索名称 / 编码 / 说明"
          style={{ width: 260 }}
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
        />
      </ListToolbar>

      <DataTable<PositionRow>
        columns={columns}
        dataSource={filtered}
        rowKey="id"
        loading={loading}
        scrollX={760}
        empty={{
          hasFilter: keyword.trim().length > 0,
          filteredText: '没有匹配的岗位',
          emptyText: '还没有岗位',
          onReset: () => setKeyword(''),
          create: canEdit
            ? {
                label: '新建岗位',
                onClick: () => {
                  setEditing(null)
                  setOpen(true)
                },
              }
            : undefined,
        }}
      />

      <ModalForm
        open={open}
        title={editing ? `编辑岗位「${editing.name}」` : '新建岗位'}
        okText="保存"
        onCancel={() => {
          setOpen(false)
          setEditing(null)
        }}
        onSubmit={(values) => submit(values as unknown as PositionForm)}
        initialValues={
          editing
            ? {
                code: editing.code,
                name: editing.name,
                description: editing.description ?? '',
                sort_order: editing.sort_order,
                is_enabled: editing.is_enabled,
              }
            : { sort_order: 0, is_enabled: true }
        }
      >
        <Form.Item
          label="岗位名称"
          name="name"
          rules={[{ required: true, message: '请输入岗位名称' }, { max: 50 }]}
        >
          <Input maxLength={50} placeholder="例如：内容运营" />
        </Form.Item>
        <Form.Item
          label="岗位编码"
          name="code"
          extra={editing ? '编码创建后不可修改' : '例如：content_operator'}
          rules={[{ required: true, message: '请输入岗位编码' }, { max: 50 }]}
        >
          <Input maxLength={50} disabled={!!editing} />
        </Form.Item>
        <Form.Item label="岗位说明" name="description">
          <Input.TextArea rows={2} maxLength={1000} placeholder="可选" />
        </Form.Item>
        <Form.Item label="排序" name="sort_order">
          <Input type="number" />
        </Form.Item>
        <Form.Item label="启用" name="is_enabled" valuePropName="checked">
          <Switch />
        </Form.Item>
      </ModalForm>
    </div>
  )
}
