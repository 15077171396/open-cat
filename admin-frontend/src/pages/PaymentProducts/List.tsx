import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableProps } from 'antd'
import { DeleteOutlined, EditOutlined, GiftOutlined, ReloadOutlined } from '@ant-design/icons'
import { apiDelete, apiGet, apiPost, apiPut } from '../../api/client'
import ListPageHeader from '../../components/ListPageHeader'
import { PermGuard } from '../../components/PermGuard'
import { TableEmpty } from '../../components/TableEmpty'
import { getErrorMessage, isFormValidationError } from '../../utils/error'
import { toast } from '../../utils/toast'

interface PaymentProduct {
  id: number
  code: string
  name: string
  description: string
  kind: 'points' | 'vip'
  amount_cents: number
  amount: string
  points: number
  vip_days: number
  badge: string
  is_enabled: boolean
  sort_order: number
}

interface ProductFormValues {
  code: string
  name: string
  description?: string
  kind: 'points' | 'vip'
  amount: number
  points: number
  vip_days: number
  badge?: string
  sort_order: number
  is_enabled: boolean
}

export default function PaymentProductsList() {
  const [loading, setLoading] = useState(true)
  const [rows, setRows] = useState<PaymentProduct[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<PaymentProduct | null>(null)
  const [saving, setSaving] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [form] = Form.useForm<ProductFormValues>()
  const kind = Form.useWatch('kind', form)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiGet<{ items: PaymentProduct[] }>('/admin/payment-products')
      setRows(data.items || [])
    } catch (err) {
      toast.error(getErrorMessage(err, '加载套餐失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({
      kind: 'points',
      amount: 10,
      points: 100,
      vip_days: 0,
      sort_order: 0,
      is_enabled: true,
    })
    setModalOpen(true)
  }

  const openEdit = (row: PaymentProduct) => {
    setEditing(row)
    form.setFieldsValue({
      code: row.code,
      name: row.name,
      description: row.description,
      kind: row.kind,
      amount: row.amount_cents / 100,
      points: row.points,
      vip_days: row.vip_days,
      badge: row.badge,
      sort_order: row.sort_order,
      is_enabled: row.is_enabled,
    })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      const payload = {
        ...values,
        code: values.code.trim(),
        name: values.name.trim(),
        description: (values.description || '').trim(),
        badge: (values.badge || '').trim(),
        amount_cents: Math.round(Number(values.amount) * 100),
      }
      if (editing) {
        await apiPut(`/admin/payment-products/${editing.id}`, payload)
        toast.success('套餐已更新')
      } else {
        await apiPost('/admin/payment-products', payload)
        toast.success('套餐已创建')
      }
      setModalOpen(false)
      await load()
    } catch (err) {
      if (isFormValidationError(err)) return
      toast.error(getErrorMessage(err, '保存失败'))
    } finally {
      setSaving(false)
    }
  }

  const toggleEnabled = async (row: PaymentProduct, checked: boolean) => {
    setBusyId(row.id)
    try {
      await apiPut(`/admin/payment-products/${row.id}`, { is_enabled: checked })
      toast.success(checked ? '套餐已上架' : '套餐已下架')
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, '操作失败'))
    } finally {
      setBusyId(null)
    }
  }

  const handleDelete = async (row: PaymentProduct) => {
    setBusyId(row.id)
    try {
      await apiDelete(`/admin/payment-products/${row.id}`)
      toast.success('套餐已删除')
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, '删除失败'))
    } finally {
      setBusyId(null)
    }
  }

  const columns: TableProps<PaymentProduct>['columns'] = [
    {
      title: '套餐',
      key: 'name',
      render: (_v, row) => (
        <Space orientation="vertical" size={0}>
          <Space size={6}>
            <Typography.Text strong>{row.name}</Typography.Text>
            {row.badge && <Tag color="gold">{row.badge}</Tag>}
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {row.code}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'kind',
      width: 90,
      render: (value: string) => (value === 'vip' ? <Tag color="gold">VIP</Tag> : <Tag color="blue">积分</Tag>),
    },
    {
      title: '权益',
      key: 'benefit',
      width: 120,
      render: (_v, row) =>
        row.kind === 'vip' ? `VIP ${row.vip_days} 天` : `${row.points} 积分`,
    },
    {
      title: '售价',
      dataIndex: 'amount',
      width: 110,
      render: (value: string) => <Typography.Text strong>¥{value}</Typography.Text>,
    },
    {
      title: '排序',
      dataIndex: 'sort_order',
      width: 80,
    },
    {
      title: '上架',
      key: 'enabled',
      width: 80,
      render: (_v, row) => (
        <PermGuard perm="payment_product:edit">
          <Switch
            size="small"
            checked={row.is_enabled}
            loading={busyId === row.id}
            onChange={(checked) => toggleEnabled(row, checked)}
          />
        </PermGuard>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 170,
      render: (_v, row) => (
        <Space size={4}>
          <PermGuard perm="payment_product:edit">
            <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(row)}>
              编辑
            </Button>
          </PermGuard>
          <PermGuard perm="payment_product:delete">
            <Popconfirm
              title="删除套餐"
              description={`删除「${row.name}」后不影响历史订单，确定继续？`}
              okText="删除"
              okType="danger"
              cancelText="取消"
              onConfirm={() => handleDelete(row)}
            >
              <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                删除
              </Button>
            </Popconfirm>
          </PermGuard>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <ListPageHeader
        icon={<GiftOutlined />}
        title="充值套餐"
        onCreate={openCreate}
        createLabel="新增套餐"
        extra={
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
            刷新
          </Button>
        }
      />

      <Card variant="outlined" styles={{ body: { padding: 0 } }}>
        <Table<PaymentProduct>
          rowKey="code"
          columns={columns}
          dataSource={rows}
          loading={loading}
          pagination={false}
          scroll={{ x: 820 }}
          locale={{
            emptyText: (
              <TableEmpty
                hasFilter={false}
                filteredText=""
                emptyText="还没有充值套餐"
                create={{ perm: 'payment_product:create', label: '新增套餐', onClick: openCreate }}
              />
            ),
          }}
        />
      </Card>

      <Modal
        open={modalOpen}
        title={editing ? '编辑套餐' : '新增套餐'}
        okText="保存"
        cancelText="取消"
        confirmLoading={saving}
        onCancel={() => setModalOpen(false)}
        onOk={handleSubmit}
        width={620}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Space size={16} style={{ display: 'flex' }} align="start">
            <Form.Item
              label="套餐标识（code）"
              name="code"
              style={{ flex: 1 }}
              rules={[
                { required: true, message: '请填写套餐标识' },
                { pattern: /^[a-z0-9][a-z0-9._-]{0,63}$/, message: '只能包含小写字母、数字、点、下划线和短横线' },
              ]}
              extra={editing ? '创建后不可修改（订单已引用）' : '如 points_100'}
            >
              <Input disabled={!!editing} />
            </Form.Item>
            <Form.Item label="类型" name="kind" style={{ width: 160 }} rules={[{ required: true }]}>
              <Select
                options={[
                  { value: 'points', label: '积分' },
                  { value: 'vip', label: 'VIP' },
                ]}
                onChange={(value) => {
                  if (value === 'vip') form.setFieldValue('vip_days', form.getFieldValue('vip_days') || 30)
                  else form.setFieldValue('points', form.getFieldValue('points') || 100)
                }}
              />
            </Form.Item>
          </Space>

          <Form.Item label="套餐名称" name="name" rules={[{ required: true, message: '请填写名称' }, { max: 100 }]}>
            <Input placeholder="如：100 积分" />
          </Form.Item>
          <Form.Item label="副标题" name="description" rules={[{ max: 300 }]}>
            <Input placeholder="可选，展示在套餐卡片上" />
          </Form.Item>

          <Space size={16} style={{ display: 'flex' }} align="start">
            <Form.Item label="售价（元）" name="amount" style={{ flex: 1 }} rules={[{ required: true, message: '请填写售价' }]}>
              <InputNumber min={0.01} max={1_000_000} precision={2} style={{ width: '100%' }} prefix="¥" />
            </Form.Item>
            {kind === 'vip' ? (
              <Form.Item label="VIP 天数" name="vip_days" style={{ flex: 1 }} rules={[{ required: true, message: '请填写天数' }]}>
                <InputNumber min={1} max={3650} style={{ width: '100%' }} />
              </Form.Item>
            ) : (
              <Form.Item label="到账积分" name="points" style={{ flex: 1 }} rules={[{ required: true, message: '请填写积分' }]}>
                <InputNumber min={1} max={10_000_000} style={{ width: '100%' }} />
              </Form.Item>
            )}
            <Form.Item label="排序" name="sort_order" style={{ width: 110 }}>
              <InputNumber min={0} max={1_000_000} style={{ width: '100%' }} />
            </Form.Item>
          </Space>

          <Space size={16} style={{ display: 'flex' }} align="start">
            <Form.Item label="角标" name="badge" style={{ flex: 1 }} rules={[{ max: 30 }]}>
              <Input placeholder="可选，如「推荐」" />
            </Form.Item>
            <Form.Item label="上架" name="is_enabled" valuePropName="checked" style={{ marginBottom: 0 }}>
              <Switch />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  )
}
