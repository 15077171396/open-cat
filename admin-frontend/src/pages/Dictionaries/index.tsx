import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Button,
  Card,
  Col,
  Dropdown,
  Form,
  Input,
  List,
  Popconfirm,
  Row,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import type { TableProps } from 'antd'
import {
  BookOutlined,
  DeleteOutlined,
  EditOutlined,
  MoreOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { apiDelete, apiGet, apiPost, apiPut } from '../../api/client'
import { useAuthStore } from '../../store/authStore'
import { toast } from '../../utils/toast'
import { getErrorMessage } from '../../utils/error'
import { PageHeader, DataTable, ModalForm } from '../../components/ui'

interface DictTypeRow {
  id: number
  code: string
  name: string
  description: string | null
  is_enabled: boolean
  item_count: number
}

interface DictItemRow {
  id: number
  type_id: number
  label: string
  value: string
  sort_order: number
  is_enabled: boolean
  is_default: boolean
  remark: string | null
}

interface TypeFormValues {
  name: string
  code: string
  description?: string
  is_enabled?: boolean
}

interface ItemFormValues {
  label: string
  value: string
  sort_order?: number
  is_enabled?: boolean
  is_default?: boolean
  remark?: string
}

/**
 * 数据字典：左侧字典类型、右侧字典项。
 *
 * 参考系统的字典管理也是这个结构。字典是**运营可维护的枚举**，本次先把
 * 「内容状态 / 机审状态 / 反馈状态 / 用户状态」四组常用值播种进去；
 * 业务代码后续可按 code 取启用项，改文案不必发版。
 */
export default function Dictionaries() {
  const { isSuper, permissions } = useAuthStore()
  const canUpdate = isSuper || (permissions?.includes('setting:update') ?? false)

  const [types, setTypes] = useState<DictTypeRow[]>([])
  const [activeTypeId, setActiveTypeId] = useState<number | null>(null)
  const [items, setItems] = useState<DictItemRow[]>([])
  const [typeKeyword, setTypeKeyword] = useState('')
  const [itemKeyword, setItemKeyword] = useState('')
  const [loadingTypes, setLoadingTypes] = useState(false)
  const [loadingItems, setLoadingItems] = useState(false)

  const [typeModalOpen, setTypeModalOpen] = useState(false)
  const [editingType, setEditingType] = useState<DictTypeRow | null>(null)
  const [itemModalOpen, setItemModalOpen] = useState(false)
  const [editingItem, setEditingItem] = useState<DictItemRow | null>(null)

  const activeType = useMemo(
    () => types.find((type) => type.id === activeTypeId) ?? null,
    [types, activeTypeId],
  )

  const filteredTypes = useMemo(() => {
    const keyword = typeKeyword.trim().toLowerCase()
    if (!keyword) return types
    return types.filter((type) =>
      [type.name, type.code, type.description ?? ''].some((value) =>
        value.toLowerCase().includes(keyword),
      ),
    )
  }, [types, typeKeyword])

  const filteredItems = useMemo(() => {
    const keyword = itemKeyword.trim().toLowerCase()
    if (!keyword) return items
    return items.filter((item) =>
      [item.label, item.value, item.remark ?? ''].some((value) =>
        value.toLowerCase().includes(keyword),
      ),
    )
  }, [items, itemKeyword])

  const loadItems = useCallback(async (typeId: number) => {
    setLoadingItems(true)
    try {
      const rows = await apiGet<DictItemRow[]>(`/admin/dicts/${typeId}/items`)
      setItems(rows)
    } catch (error) {
      toast.error(getErrorMessage(error, '字典项加载失败'))
      setItems([])
    } finally {
      setLoadingItems(false)
    }
  }, [])

  const loadTypes = useCallback(async (preferredId?: number | null) => {
    setLoadingTypes(true)
    try {
      const data = await apiGet<{ items: DictTypeRow[] }>('/admin/dicts')
      setTypes(data.items ?? [])
      const nextId =
        preferredId ??
        (activeTypeId && data.items.some((item) => item.id === activeTypeId)
          ? activeTypeId
          : data.items[0]?.id ?? null)
      setActiveTypeId(nextId)
      if (nextId) await loadItems(nextId)
      else setItems([])
    } catch (error) {
      toast.error(getErrorMessage(error, '字典类型加载失败'))
    } finally {
      setLoadingTypes(false)
    }
  }, [activeTypeId, loadItems])

  useEffect(() => {
    void loadTypes()
    // 只在首次进入时拉取；切换类型由 click 直接 loadItems
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const selectType = (type: DictTypeRow) => {
    setActiveTypeId(type.id)
    setItemKeyword('')
    void loadItems(type.id)
  }

  const submitType = async (values: TypeFormValues) => {
    setTypeModalOpen(false)
    try {
      if (editingType) {
        await apiPut(`/admin/dicts/${editingType.id}`, {
          name: values.name,
          description: values.description || null,
          is_enabled: values.is_enabled ?? true,
        })
        toast.success('字典类型已更新')
        await loadTypes(editingType.id)
      } else {
        const created = await apiPost<DictTypeRow>('/admin/dicts', {
          code: values.code,
          name: values.name,
          description: values.description || null,
          is_enabled: values.is_enabled ?? true,
        })
        toast.success('字典类型已创建')
        await loadTypes(created.id)
      }
      setEditingType(null)
    } catch (error) {
      toast.error(getErrorMessage(error, '保存失败'))
    }
  }

  const deleteType = async (type: DictTypeRow) => {
    try {
      await apiDelete(`/admin/dicts/${type.id}`)
      toast.success('字典类型已删除')
      if (activeTypeId === type.id) setActiveTypeId(null)
      await loadTypes(null)
    } catch (error) {
      toast.error(getErrorMessage(error, '删除失败'))
    }
  }

  const updateTypeEnabled = async (type: DictTypeRow, checked: boolean) => {
    try {
      await apiPut(`/admin/dicts/${type.id}`, { is_enabled: checked })
      await loadTypes(type.id)
    } catch (error) {
      toast.error(getErrorMessage(error, '状态保存失败'))
    }
  }

  const submitItem = async (values: ItemFormValues) => {
    if (!activeType) return
    setItemModalOpen(false)
    try {
      const body = {
        label: values.label,
        value: values.value,
        sort_order: Number(values.sort_order ?? 0),
        is_enabled: values.is_enabled ?? true,
        is_default: values.is_default ?? false,
        remark: values.remark || null,
      }
      if (editingItem) {
        await apiPut(`/admin/dicts/${activeType.id}/items/${editingItem.id}`, body)
        toast.success('字典项已更新')
      } else {
        await apiPost(`/admin/dicts/${activeType.id}/items`, body)
        toast.success('字典项已创建')
      }
      setEditingItem(null)
      await loadTypes(activeType.id)
    } catch (error) {
      toast.error(getErrorMessage(error, '保存失败'))
    }
  }

  const deleteItem = async (item: DictItemRow) => {
    if (!activeType) return
    try {
      await apiDelete(`/admin/dicts/${activeType.id}/items/${item.id}`)
      toast.success('字典项已删除')
      await loadTypes(activeType.id)
    } catch (error) {
      toast.error(getErrorMessage(error, '删除失败'))
    }
  }

  const updateItemEnabled = async (item: DictItemRow, checked: boolean) => {
    if (!activeType) return
    try {
      await apiPut(`/admin/dicts/${activeType.id}/items/${item.id}`, { is_enabled: checked })
      await loadTypes(activeType.id)
    } catch (error) {
      toast.error(getErrorMessage(error, '状态保存失败'))
    }
  }

  const columns: TableProps<DictItemRow>['columns'] = [
    {
      title: '标签',
      dataIndex: 'label',
      key: 'label',
      render: (label: string, record) => (
        <Space size={6}>
          <span>{label}</span>
          {record.is_default ? <Tag color="blue">默认</Tag> : null}
        </Space>
      ),
    },
    {
      title: '值',
      dataIndex: 'value',
      key: 'value',
      render: (value: string) => <code className="dict-value">{value}</code>,
    },
    { title: '排序', dataIndex: 'sort_order', key: 'sort_order', width: 80 },
    {
      title: '启用',
      dataIndex: 'is_enabled',
      key: 'is_enabled',
      width: 80,
      render: (enabled: boolean, record) => (
        <Switch
          size="small"
          checked={enabled}
          disabled={!canUpdate}
          onChange={(checked) => void updateItemEnabled(record, checked)}
          aria-label={`${record.label} 是否启用`}
        />
      ),
    },
    {
      title: '备注',
      dataIndex: 'remark',
      key: 'remark',
      render: (remark: string | null) => remark || <span className="dict-muted">—</span>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      render: (_, record) =>
        canUpdate ? (
          <Space size={4}>
            <Button
              size="small"
              type="link"
              icon={<EditOutlined />}
              onClick={() => {
                setEditingItem(record)
                setItemModalOpen(true)
              }}
            >
              编辑
            </Button>
            <Popconfirm title={`删除字典项「${record.label}」？`} onConfirm={() => void deleteItem(record)}>
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
        icon={<BookOutlined />}
        title="字典管理"
        subtitle="集中维护业务枚举的标签与值；类型编码创建后不可修改"
        actions={
          <Tooltip title="重新加载">
            <Button
              icon={<ReloadOutlined />}
              onClick={() => void loadTypes(activeTypeId)}
              aria-label="重新加载字典"
            />
          </Tooltip>
        }
      />

      <Row gutter={16} align="top">
        <Col xs={24} lg={7} xl={6}>
          <Card
            className="adm-sidecard dict-type-card"
            variant="outlined"
            title={
              <span>
                <BookOutlined style={{ marginRight: 6 }} />
                字典类型
              </span>
            }
            extra={
              canUpdate ? (
                <Tooltip title="新建字典类型">
                  <Button
                    type="text"
                    size="small"
                    icon={<PlusOutlined />}
                    aria-label="新建字典类型"
                    onClick={() => {
                      setEditingType(null)
                      setTypeModalOpen(true)
                    }}
                  />
                </Tooltip>
              ) : null
            }
          >
            <div className="dict-type-search">
              <Input.Search
                allowClear
                placeholder="搜索名称 / 编码"
                value={typeKeyword}
                onChange={(event) => setTypeKeyword(event.target.value)}
              />
            </div>
            <List
              size="small"
              loading={loadingTypes}
              dataSource={filteredTypes}
              locale={{ emptyText: '暂无字典类型' }}
              renderItem={(type) => (
                <List.Item
                  className={`dict-type-item${type.id === activeTypeId ? ' is-active' : ''}`}
                  onClick={() => selectType(type)}
                  extra={
                    canUpdate ? (
                      <Dropdown
                        trigger={['click']}
                        menu={{
                          items: [
                            { key: 'edit', label: '编辑' },
                            { key: 'delete', label: '删除', danger: true },
                          ],
                          onClick: ({ key, domEvent }) => {
                            domEvent.stopPropagation()
                            if (key === 'edit') {
                              setEditingType(type)
                              setTypeModalOpen(true)
                            }
                            if (key === 'delete') {
                              if (type.item_count > 0) {
                                toast.warning('请先删除该类型下的字典项')
                                return
                              }
                              void deleteType(type)
                            }
                          },
                        }}
                      >
                        <Button
                          type="text"
                          size="small"
                          icon={<MoreOutlined />}
                          aria-label={`${type.name} 更多操作`}
                          onClick={(event) => event.stopPropagation()}
                        />
                      </Dropdown>
                    ) : null
                  }
                >
                  <div className="dict-type-item__body">
                    <div className="dict-type-item__name">
                      {type.name}
                      {!type.is_enabled ? <Tag>停用</Tag> : null}
                    </div>
                    <div className="dict-type-item__code">{type.code}</div>
                    <div className="dict-type-item__count">{type.item_count} 项</div>
                  </div>
                  {canUpdate ? (
                    <Switch
                      size="small"
                      checked={type.is_enabled}
                      onClick={(_, event) => event.stopPropagation()}
                      onChange={(checked) => void updateTypeEnabled(type, checked)}
                      aria-label={`${type.name} 是否启用`}
                    />
                  ) : null}
                </List.Item>
              )}
            />
          </Card>
        </Col>

        <Col xs={24} lg={17} xl={18}>
          {activeType ? (
            <>
              <div className="dict-panel-head">
                <div>
                  <Typography.Title level={5} style={{ margin: 0 }}>
                    {activeType.name}
                  </Typography.Title>
                  <Typography.Text type="secondary" className="dict-code">
                    {activeType.code}
                  </Typography.Text>
                </div>
                <Space wrap>
                  <Input.Search
                    allowClear
                    placeholder="搜索标签 / 值 / 备注"
                    style={{ width: 220 }}
                    value={itemKeyword}
                    onChange={(event) => setItemKeyword(event.target.value)}
                  />
                  {canUpdate ? (
                    <Button
                      type="primary"
                      icon={<PlusOutlined />}
                      onClick={() => {
                        setEditingItem(null)
                        setItemModalOpen(true)
                      }}
                    >
                      新建字典项
                    </Button>
                  ) : null}
                </Space>
              </div>
              <DataTable<DictItemRow>
                columns={columns}
                dataSource={filteredItems}
                rowKey="id"
                loading={loadingItems}
                scrollX={760}
                empty={{
                  hasFilter: itemKeyword.trim().length > 0,
                  filteredText: '没有匹配的字典项',
                  emptyText: '该字典下还没有字典项',
                  onReset: () => setItemKeyword(''),
                  create: canUpdate
                    ? {
                        label: '新建字典项',
                        onClick: () => {
                          setEditingItem(null)
                          setItemModalOpen(true)
                        },
                      }
                    : undefined,
                }}
              />
            </>
          ) : (
            <Card className="adm-sidecard dict-empty-card" variant="outlined">
              <Typography.Text type="secondary">
                {types.length === 0 ? '还没有字典类型，先新建一个' : '在左侧选择一个字典类型'}
              </Typography.Text>
            </Card>
          )}
        </Col>
      </Row>

      <ModalForm
        open={typeModalOpen}
        title={editingType ? `编辑字典类型「${editingType.name}」` : '新建字典类型'}
        okText="保存"
        onCancel={() => {
          setTypeModalOpen(false)
          setEditingType(null)
        }}
        onSubmit={(values) => submitType(values as unknown as TypeFormValues)}
        initialValues={
          editingType
            ? {
                name: editingType.name,
                code: editingType.code,
                description: editingType.description ?? '',
                is_enabled: editingType.is_enabled,
              }
            : { is_enabled: true }
        }
      >
        <Form.Item
          label="字典名称"
          name="name"
          rules={[{ required: true, message: '请输入字典名称' }, { max: 100 }]}
        >
          <Input placeholder="例如：内容状态" maxLength={100} />
        </Form.Item>
        <Form.Item
          label="类型编码"
          name="code"
          extra={editingType ? '类型编码创建后不可修改（业务代码按它引用）' : '小写字母开头，仅含小写字母/数字/下划线'}
          rules={[
            { required: true, message: '请输入类型编码' },
            { pattern: /^[a-z][a-z0-9_]{1,79}$/, message: '格式：小写字母开头，2~80 位' },
          ]}
        >
          <Input placeholder="例如：content_status" maxLength={80} disabled={!!editingType} />
        </Form.Item>
        <Form.Item label="说明" name="description">
          <Input.TextArea rows={2} maxLength={1000} placeholder="这个字典用在哪里" />
        </Form.Item>
        <Form.Item label="启用" name="is_enabled" valuePropName="checked">
          <Switch />
        </Form.Item>
      </ModalForm>

      <ModalForm
        open={itemModalOpen}
        title={editingItem ? `编辑字典项「${editingItem.label}」` : '新建字典项'}
        okText="保存"
        onCancel={() => {
          setItemModalOpen(false)
          setEditingItem(null)
        }}
        onSubmit={(values) => submitItem(values as unknown as ItemFormValues)}
        initialValues={
          editingItem
            ? {
                label: editingItem.label,
                value: editingItem.value,
                sort_order: editingItem.sort_order,
                is_enabled: editingItem.is_enabled,
                is_default: editingItem.is_default,
                remark: editingItem.remark ?? '',
              }
            : { sort_order: 0, is_enabled: true, is_default: false }
        }
      >
        <Form.Item
          label="标签"
          name="label"
          rules={[{ required: true, message: '请输入标签' }, { max: 100 }]}
        >
          <Input placeholder="展示给运营看的文案，例如：已发布" maxLength={100} />
        </Form.Item>
        <Form.Item
          label="值"
          name="value"
          rules={[{ required: true, message: '请输入值' }, { max: 100 }]}
        >
          <Input placeholder="业务存储值，例如：published" maxLength={100} />
        </Form.Item>
        <Form.Item label="排序" name="sort_order">
          <Input type="number" placeholder="数字越小越靠前" />
        </Form.Item>
        <Form.Item label="默认项" name="is_default" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Form.Item label="启用" name="is_enabled" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Form.Item label="备注" name="remark">
          <Input maxLength={255} placeholder="可选" />
        </Form.Item>
      </ModalForm>
    </div>
  )
}
