import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
} from 'antd'
import type { TableProps } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { apiGet, apiPost, apiPut } from '../../api/client'
import { PermGuard } from '../../components/PermGuard'
import { TableEmpty } from '../../components/TableEmpty'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'
import ModalForm from '../../components/ui/ModalForm'

/** 后端 AI 写作提示词行（GET /admin/ai/writing/prompts） */
interface PromptRow {
  operation: string
  display_name: string
  description: string
  system_prompt: string
  user_template: string
  output_type: string
  model_code: string
  temperature: number
  max_tokens: number
  max_input_chars: number
  is_enabled: boolean
  sort_order: number
}

/**
 * 弹窗只放可编辑字段。
 *
 * operation / output_type / sort_order 刻意不在表单里：它们由后端或种子数据决定，
 * 整行回写会把页面上看不到的值一起提交，属于「看不见的副作用」。
 */
interface PromptFormValues {
  display_name: string
  system_prompt: string
  user_template: string
  model_code?: string
  temperature: number
  max_tokens: number
  max_input_chars: number
  is_enabled: boolean
}

interface AiModelOption {
  code: string
  display_name: string
  /** 旧后端缺省时按 text 处理 */
  modality?: string
}

export default function AiWritingPage() {
  const [rows, setRows] = useState<PromptRow[]>([])
  const [models, setModels] = useState<AiModelOption[]>([])
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [editing, setEditing] = useState<PromptRow | null>(null)
  const [form] = Form.useForm<PromptFormValues>()
  /** 弹窗回填值：ModalForm 打开时写入表单 */
  const [formValues, setFormValues] = useState<Record<string, unknown>>({})

  const load = useCallback(async () => {
    setLoading(true)
    setLoadFailed(false)
    try {
      const [prompts, modelList] = await Promise.all([
        apiGet<{ items: PromptRow[] }>('/admin/ai/writing/prompts'),
        apiGet<{ items: AiModelOption[] }>('/admin/ai-models'),
      ])
      setRows(prompts.items || [])
      // 写作调用的是对话接口：图像模型必须排除，否则保存时后端会以 400 拒绝
      setModels((modelList.items || []).filter((m) => (m.modality || 'text') === 'text'))
    } catch (err) {
      setLoadFailed(true)
      toast.error(getErrorMessage(err, '加载 AI 写作配置失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const openEdit = (row: PromptRow) => {
    setFormValues({
      display_name: row.display_name,
      system_prompt: row.system_prompt,
      user_template: row.user_template,
      // 空字符串会让 Select 显示成一个「空选项」，转成 undefined 才能落到 placeholder
      model_code: row.model_code || undefined,
      temperature: row.temperature,
      max_tokens: row.max_tokens,
      max_input_chars: row.max_input_chars,
      is_enabled: row.is_enabled,
    })
    setEditing(row)
  }

  const closeEdit = () => {
    setEditing(null)
  }

  const save = async (raw: Record<string, unknown>) => {
    if (!editing) return
    // ModalForm 传宽类型；字段定义在 PromptFormValues，收敛回具体类型
    const values = raw as unknown as PromptFormValues
    try {
      // 显式列字段而不用整行 values：新增字段不会在不知不觉间被写回
      await apiPut(`/admin/ai/writing/prompts/${editing.operation}`, {
        display_name: values.display_name.trim(),
        system_prompt: values.system_prompt,
        user_template: values.user_template,
        // 清空模型 = 回退全局默认；显式传空串才会清掉库里的值
        model_code: values.model_code ?? '',
        temperature: values.temperature,
        max_tokens: values.max_tokens,
        max_input_chars: values.max_input_chars,
        is_enabled: values.is_enabled,
      })
      toast.success('提示词已保存')
      closeEdit()
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, '保存失败'))
    }
  }

  const reset = async (row: PromptRow) => {
    try {
      await apiPost(`/admin/ai/writing/prompts/${row.operation}/reset`)
      toast.success(`「${row.display_name}」已恢复默认`)
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, '恢复失败'))
    }
  }

  const columns: TableProps<PromptRow>['columns'] = [
    {
      title: '展示名',
      dataIndex: 'display_name',
      key: 'display_name',
      width: 160,
    },
    {
      title: '标识',
      dataIndex: 'operation',
      key: 'operation',
      width: 160,
    },
    {
      title: '输出',
      dataIndex: 'output_type',
      key: 'output_type',
      width: 100,
      render: (value: string) => <Tag>{value}</Tag>,
    },
    {
      title: '模型',
      dataIndex: 'model_code',
      key: 'model_code',
      width: 180,
      render: (value: string) => value || '全局默认',
    },
    {
      title: '状态',
      dataIndex: 'is_enabled',
      key: 'is_enabled',
      width: 80,
      render: (value: boolean) => (value ? '启用' : '停用'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 180,
      render: (_value, row) => (
        <PermGuard perm="ai_model:edit">
          <Space size={0}>
            <Button type="link" size="small" onClick={() => openEdit(row)}>
              编辑
            </Button>
            {/* 恢复默认会覆盖已改好的提示词，必须先确认 */}
            <Popconfirm
              title="恢复默认提示词"
              description={`将「${row.display_name}」恢复为内置默认值，当前修改会丢失。`}
              okText="恢复默认"
              okType="danger"
              cancelText="取消"
              onConfirm={() => reset(row)}
            >
              <Button type="link" size="small" danger>
                恢复默认
              </Button>
            </Popconfirm>
          </Space>
        </PermGuard>
      ),
    },
  ]

  return (
    <div>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        title="提示词与模型配置保存后立即生效；总开关与插图日限额在「系统设置 → AI 写作」。"
      />
      {loadFailed ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          title="AI 写作配置加载失败，请重试"
          action={
            <Button size="small" icon={<ReloadOutlined />} onClick={() => void load()}>
              重试
            </Button>
          }
        />
      ) : null}
      <Table<PromptRow>
        rowKey="operation"
        dataSource={rows}
        loading={loading}
        pagination={false}
        size="small"
        columns={columns}
        locale={{
          emptyText: (
            <TableEmpty
              hasFilter={false}
              filteredText=""
              emptyText={loadFailed ? '加载失败，请重试' : '暂无提示词配置'}
            />
          ),
        }}
      />
      <ModalForm
        title={`编辑：${editing?.display_name || ''}`}
        open={!!editing}
        onCancel={closeEdit}
        onSubmit={save}
        okText="保存"
        width={860}
        form={form}
        initialValues={formValues}
      >
          <Form.Item
            label="展示名"
            name="display_name"
            rules={[
              { required: true, whitespace: true, message: '请填写展示名' },
              { max: 50, message: '最多 50 字' },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            label="系统提示词"
            name="system_prompt"
            rules={[
              { required: true, whitespace: true, message: '请填写系统提示词' },
              { max: 20000, message: '最多 20000 字' },
            ]}
          >
            <Input.TextArea rows={4} />
          </Form.Item>
          <Form.Item
            label="用户模板"
            name="user_template"
            extra="支持 {{title}} {{selection}} 等变量；未知变量会原样保留。"
            rules={[
              { required: true, whitespace: true, message: '请填写用户模板' },
              { max: 20000, message: '最多 20000 字' },
            ]}
          >
            <Input.TextArea rows={8} />
          </Form.Item>
          <Form.Item
            label="模型"
            name="model_code"
            extra="留空使用全局默认模型；只能选择文本模型。"
          >
            <Select
              allowClear
              placeholder="全局默认"
              options={models.map((m) => ({
                value: m.code,
                label: `${m.display_name}（${m.code}）`,
              }))}
            />
          </Form.Item>
          <Space size={16} wrap>
            <Form.Item
              label="温度"
              name="temperature"
              rules={[{ required: true, message: '请填写温度' }]}
            >
              <InputNumber min={0} max={2} step={0.1} style={{ width: 120 }} />
            </Form.Item>
            <Form.Item
              label="最大输出 tokens"
              name="max_tokens"
              rules={[{ required: true, message: '请填写最大输出 tokens' }]}
            >
              <InputNumber min={100} max={8000} step={100} style={{ width: 160 }} />
            </Form.Item>
            <Form.Item
              label="上下文字符上限"
              name="max_input_chars"
              rules={[{ required: true, message: '请填写上下文字符上限' }]}
            >
              <InputNumber min={500} max={50000} step={500} style={{ width: 160 }} />
            </Form.Item>
            <Form.Item label="启用" name="is_enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
      </ModalForm>
    </div>
  )
}
