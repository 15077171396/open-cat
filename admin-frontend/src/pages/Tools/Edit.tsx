import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Button, Col, Form, Input, InputNumber, Radio, Row, Select, Space, Switch, Typography } from 'antd'
import {
  CheckCircleOutlined,
  DeleteOutlined,
  FormOutlined,
  InfoCircleOutlined,
  PlusOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { apiPost, apiPut, apiGet } from '../../api/client'
import EditPageLayout from '../../components/EditPageLayout'
import Section from '../../components/AdminSection'
import { useCategoryOptions } from '../../hooks/useCategoryOptions'
import { useUnsavedChangesGuard } from '../../hooks/useUnsavedChangesGuard'
import { TOOL_CATEGORIES, TOOL_TYPES, TOOL_FIELD_TYPES, TOOL_ACCESS_MODES } from '../../constants'
import type { Tool, ToolFormField } from '../../types'
import { getErrorMessage, isFormValidationError } from '../../utils/error'

import { toast } from '../../utils/toast'
/** 模型下拉项（来自 GET /admin/ai-models/options，不再前端写死供应商清单） */
interface AiModelOption {
  code: string
  display_name: string
  provider: string
  provider_label: string
  model_name: string
  is_enabled: boolean
  is_default: boolean
}

function toModelSelectOptions(options: AiModelOption[], current?: string) {
  const mapped = options.map((option) => ({
    value: option.code,
    label:
      option.display_name === option.provider_label
        ? `${option.display_name}${option.is_enabled ? '' : ' · 已禁用'}`
        : `${option.display_name}（${option.provider_label}）${option.is_enabled ? '' : ' · 已禁用'}`,
    disabled: !option.is_enabled,
  }))
  // 历史值可能指向已删除/禁用的模型：保留显示，避免下拉「消失」后用户误以为没配置
  if (current && !mapped.some((option) => option.value === current)) {
    mapped.unshift({ value: current, label: `${current}（未在模型管理中）`, disabled: false })
  }
  return mapped
}

/**
 * 提取提示词模板中的 {字段} 占位符（排除 {{...}} 转义），与后端 format(**input_data) 语义对齐：
 * 占位符引用不存在的字段时运行时必然抛错。
 */
const PLACEHOLDER_RE = /(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!})/g

export default function ToolsEdit() {
  const navigate = useNavigate()
  const { id } = useParams()
  const isEdit = !!id
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(isEdit)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const categoryOptions = useCategoryOptions('tool', TOOL_CATEGORIES)
  const toolType = Form.useWatch('tool_type', form)
  const accessMode = Form.useWatch('access_mode', form)
  // 发布检查要看「提示词」「表单字段」是否已填，但**不能 watch 这两个大字段**
  // （提示词模板每次键入都会重渲染整页）。改用布尔 state，只在「空↔非空」翻转时重渲染。
  const [promptFilled, setPromptFilled] = useState(false)
  const [hasFields, setHasFields] = useState(false)
  // 模型下拉来自模型管理（不再是前端写死的三家）
  const [aiModelOptions, setAiModelOptions] = useState<AiModelOption[]>([])
  const [defaultModelCode, setDefaultModelCode] = useState('deepseek')
  // 名称/分类/简介都短，可以 watch 以获得实时勾选状态（与百科/资源编辑页同一做法）
  const watchedName = Form.useWatch('name', form)
  const watchedCategory = Form.useWatch('category', form)
  const watchedDescription = Form.useWatch('description', form)
  const watchedAiModel = Form.useWatch('ai_model', form)

  // isFieldsTouched() 在本页存在误报（表单字段可视化编辑的 Form.List 场景），
  // 改用 onValuesChange + 列表增删显式置脏的确定性方案。
  const confirmLeave = useUnsavedChangesGuard(() => dirty)

  useEffect(() => {
    let cancelled = false
    apiGet<{ items: AiModelOption[] }>('/admin/ai-models/options')
      .then((data) => {
        if (cancelled) return
        const items = data.items || []
        setAiModelOptions(items)
        const fallback =
          items.find((item) => item.is_default && item.is_enabled)?.code ||
          items.find((item) => item.is_enabled)?.code ||
          'deepseek'
        setDefaultModelCode(fallback)
        if (!isEdit && !form.getFieldValue('ai_model')) {
          form.setFieldValue('ai_model', fallback)
        }
      })
      .catch(() => {
        // 拉取失败不阻断编辑：保留历史值，下拉里会把它作为唯一选项显示
      })
    return () => {
      cancelled = true
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const init = async () => {
      if (isEdit && id) {
        try {
          const d = await apiGet<Tool>(`/admin/tools/${id}`)
          form.setFieldsValue({
            name: d.name || '',
            category: d.category || TOOL_CATEGORIES[0],
            description: d.description || '',
            tool_type: d.tool_type || 'ai',
            is_published: d.is_published ?? true,
            access_mode: d.access_mode || 'auth',
            points_cost: d.points_cost ?? 1,
            ai_model: d.ai_config?.model || '',
            ai_prompt: d.ai_config?.prompt_template || '',
            fields: d.form_schema?.fields ?? [],
          })
          // setFieldsValue 不触发 onValuesChange，回填后要手动同步发布检查的两个布尔位
          setPromptFilled(!!(d.ai_config?.prompt_template || '').trim())
          setHasFields((d.form_schema?.fields ?? []).length > 0)
        } catch {
          toast.error('加载工具失败')
          navigate('/tools')
        } finally {
          setLoading(false)
        }
      }
    }
    init()
  }, [id]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      const isAi = values.tool_type === 'ai'
      const fields: ToolFormField[] = (values.fields || []).filter((f: ToolFormField) => f?.name?.trim())
      if (isAi && values.ai_prompt) {
        const fieldNames = new Set(fields.map((f: ToolFormField) => f.name.trim()))
        const placeholders: string[] = values.ai_prompt.match(PLACEHOLDER_RE) || []
        const missing = [...new Set(placeholders)]
          .map((p) => p.slice(1, -1))
          .filter((n) => !fieldNames.has(n))
        if (missing.length > 0) {
          toast.error(`提示词模板中的占位符 {${missing.join('}、{')}} 未在表单字段中定义，请先添加对应字段`)
          return
        }
      }
      setSaving(true)
      const accessModeValue = values.access_mode || 'auth'
      const payload = {
        name: values.name,
        category: values.category,
        description: values.description || '',
        tool_type: values.tool_type,
        is_published: values.is_published ?? true,
        access_mode: accessModeValue,
        // 积分价格仅在积分模式下提交，其他模式后端会清零
        points_cost: accessModeValue === 'points' ? (values.points_cost ?? 1) : 0,
        ai_config: isAi
          ? {
              model: values.ai_model || defaultModelCode || 'deepseek',
              prompt_template: values.ai_prompt || '',
            }
          : null,
        form_schema: isAi && fields.length > 0 ? { fields } : null,
      }
      if (isEdit && id) {
        await apiPut(`/admin/tools/${id}`, payload)
      } else {
        await apiPost('/admin/tools', payload)
      }
      navigate('/tools')
    } catch (err: unknown) {
      if (isFormValidationError(err)) return
      toast.error(getErrorMessage(err, '保存失败'))
    } finally {
      setSaving(false)
    }
  }

  const isAi = toolType === 'ai'
  const checklist = [
    { label: '工具名称', done: !!(watchedName || '').trim() },
    { label: '分类', done: !!watchedCategory },
    { label: '简介', done: !!(watchedDescription || '').trim() },
    ...(isAi
      ? [
          { label: '提示词模板', done: promptFilled },
          { label: '表单字段', done: hasFields },
        ]
      : []),
  ]
  const doneCount = checklist.filter((c) => c.done).length

  return (
    <EditPageLayout
      title={isEdit ? '编辑工具' : '新建工具'}
      backLabel="返回在线工具"
      loading={loading}
      saving={saving}
      onCancel={() => confirmLeave(() => navigate('/tools'))}
      onSave={handleSave}
    >
      <Form
        form={form}
        layout="vertical"
        onValuesChange={(changed) => {
          setDirty(true)
          // 只在「空 ↔ 非空」翻转时才 setState，避免每次键入重渲染整页
          if ('ai_prompt' in changed) setPromptFilled(!!(changed.ai_prompt || '').trim())
          if ('fields' in changed) {
            const list = (changed.fields || []) as ToolFormField[]
            setHasFields(list.some((f) => !!f?.name?.trim()))
          }
        }}
        initialValues={{
          name: '',
          category: TOOL_CATEGORIES[0],
          description: '',
          tool_type: 'ai',
          is_published: true,
          access_mode: 'auth',
          points_cost: 1,
          ai_model: '',
          ai_prompt: '',
          fields: [],
        }}
      >
        <Section
          icon={<InfoCircleOutlined />}
          title="基本信息"
          hint="名称与简介会直接展示在前台工具卡片上；分类决定卡片归属的筛选分组，类型决定前台用哪种工作台渲染。"
        >
          <Row gutter={16}>
            <Col xs={24} md={10}>
              <Form.Item
                label="工具名称"
                name="name"
                rules={[{ required: true, message: '请填写工具名称' }, { max: 100, message: '最多 100 字' }]}
              >
                <Input placeholder="如：AI 提示词生成器" />
              </Form.Item>
            </Col>
            <Col xs={12} md={7}>
              <Form.Item label="分类" name="category" rules={[{ required: true }]}>
                <Select options={categoryOptions.map((c) => ({ value: c, label: c }))} />
              </Form.Item>
            </Col>
            <Col xs={12} md={7}>
              <Form.Item
                label="类型"
                name="tool_type"
                rules={[{ required: true }]}
                extra="前端工具需专用工作台组件"
              >
                <Select options={TOOL_TYPES.map((t) => ({ value: t.value, label: t.label }))} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="简介" name="description" rules={[{ max: 500, message: '最多 500 字' }]}>
            <Input.TextArea rows={2} placeholder="一句话描述该工具的作用" showCount maxLength={500} />
          </Form.Item>
          <Form.Item
            label="上架状态"
            name="is_published"
            valuePropName="checked"
            extra="下架后前台不可见、不可使用，但数据与使用次数都保留"
            style={{ marginBottom: 0 }}
          >
            <Switch checkedChildren="上架" unCheckedChildren="下架" />
          </Form.Item>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<SafetyCertificateOutlined />}
          title="使用权限"
          hint="决定前台进入门槛与是否扣积分；积分模式下调用失败会自动退还，VIP 与管理员不扣积分。"
        >
          <Form.Item
            name="access_mode"
            style={{ marginBottom: accessMode === 'points' ? 16 : 0 }}
            extra={
              accessMode === 'points'
                ? 'AI 工具与前端工具（如图片压缩）均按次扣积分，AI 调用失败自动退还；VIP/管理员不扣积分'
                : '免费=登录即可用；需认证=消耗 3 小时免费时长（耗尽后需星球认证，仅 AI 工具计时，前端工具仅要求登录）'
            }
          >
            <Radio.Group
              options={TOOL_ACCESS_MODES.map((m) => ({ value: m.value, label: m.label }))}
              optionType="button"
              buttonStyle="solid"
            />
          </Form.Item>
          {accessMode === 'points' && (
            <Form.Item
              label="积分消耗（每次运行）"
              name="points_cost"
              rules={[{ required: true, message: '请填写积分消耗' }]}
              style={{ maxWidth: 280, marginBottom: 0 }}
            >
              <InputNumber min={0} max={100000} precision={0} style={{ width: '100%' }} suffix="积分/次" />
            </Form.Item>
          )}
        </Section>

        {toolType === 'frontend' && (
          <>
            <div className="adm-divider" />
            <Typography.Paragraph type="warning" style={{ marginBottom: 0 }}>
              前端工具在前台需要对应的专用工作台组件才会正常展示（目前仅「图片压缩」有专用工作台），
              其余前端工具将显示通用表单页。若只是想让用户填写表单并调用 AI，请把类型改为「AI 工具」。
            </Typography.Paragraph>
          </>
        )}

        {isAi && (
          <>
            <div className="adm-divider" />

            <Section
              icon={<RobotOutlined />}
              title="AI 配置"
              hint="提示词模板里用 {字段名} 引用下方表单字段；引用了未定义的字段会在运行时直接报错，保存前会拦下。"
            >
              <Row gutter={16}>
                <Col xs={24} md={6}>
                  <Form.Item
                    label="模型"
                    name="ai_model"
                    extra="来自「系统 → 模型管理」；禁用/删除的模型会回退默认模型"
                  >
                    <Select
                      options={toModelSelectOptions(aiModelOptions, watchedAiModel)}
                      placeholder="选择模型"
                    />
                  </Form.Item>
                </Col>
                <Col xs={24} md={18}>
                  <Form.Item
                    label="提示词模板"
                    name="ai_prompt"
                    rules={[{ required: true, message: '请填写提示词模板' }]}
                    extra="用 {字段名} 引用下方表单字段，如 {topic}、{style}"
                    style={{ marginBottom: 0 }}
                  >
                    <Input.TextArea
                      rows={5}
                      placeholder={'例如：你是一个专业的提示词工程师。请根据以下信息生成提示词：\n主题: {topic}\n风格: {style}'}
                    />
                  </Form.Item>
                </Col>
              </Row>
            </Section>

            <div className="adm-divider" />

            <Section
              icon={<FormOutlined />}
              title="表单字段"
              hint="用户运行工具时需要填写的输入项；提示词模板可通过 {字段名} 引用它们。字段名只能是英文/数字/下划线。"
            >
              <Form.List name="fields">
                {(lists, { add, remove }) => (
                  <>
                    {lists.length === 0 && (
                      <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
                        还没有字段。没有输入项的工具在运行时会让用户无从下手。
                      </Typography.Text>
                    )}
                    {lists.map(({ key, name }) => (
                      <Row key={key} gutter={12} align="middle" style={{ marginBottom: 8 }}>
                        <Col xs={24} md={5}>
                          <Form.Item
                            name={[name, 'name']}
                            style={{ marginBottom: 0 }}
                            rules={[
                              { required: true, message: '字段名' },
                              { pattern: /^[a-zA-Z_][a-zA-Z0-9_]*$/, message: '仅限字母/数字/下划线' },
                            ]}
                          >
                            <Input placeholder="字段名 (英文)" />
                          </Form.Item>
                        </Col>
                        <Col xs={24} md={5}>
                          <Form.Item
                            name={[name, 'label']}
                            style={{ marginBottom: 0 }}
                            rules={[{ required: true, message: '标签' }]}
                          >
                            <Input placeholder="显示标签" />
                          </Form.Item>
                        </Col>
                        <Col xs={24} md={4}>
                          <Form.Item name={[name, 'type']} style={{ marginBottom: 0 }} initialValue="text">
                            <Select options={TOOL_FIELD_TYPES.map((t) => ({ value: t.value, label: t.label }))} />
                          </Form.Item>
                        </Col>
                        <Col xs={24} md={8}>
                          <Form.Item name={[name, 'placeholder']} style={{ marginBottom: 0 }}>
                            <Input placeholder="提示文字 (可选)" />
                          </Form.Item>
                        </Col>
                        <Col xs={24} md={2} style={{ textAlign: 'right' }}>
                          <Button
                            type="text"
                            danger
                            icon={<DeleteOutlined />}
                            onClick={() => {
                              setDirty(true)
                              remove(name)
                            }}
                          />
                        </Col>
                      </Row>
                    ))}
                    <Button
                      type="dashed"
                      icon={<PlusOutlined />}
                      onClick={() => {
                        setDirty(true)
                        add({ type: 'text' })
                        setHasFields(true)
                      }}
                      style={{ width: '100%' }}
                    >
                      添加字段
                    </Button>
                  </>
                )}
              </Form.List>
            </Section>
          </>
        )}

        <div className="adm-divider" />

        <Section
          icon={<CheckCircleOutlined />}
          title="发布检查"
          hint="保存前确认以下要素是否齐全；缺项不影响保存，但会让前台工具卡片或运行体验不完整。"
        >
          <Space size={16} wrap>
            {checklist.map((c) => (
              <Space key={c.label} size={6}>
                <CheckCircleOutlined style={{ color: c.done ? '#52c41a' : '#d9d9d9' }} />
                <Typography.Text type={c.done ? undefined : 'secondary'}>{c.label}</Typography.Text>
              </Space>
            ))}
          </Space>
          <div style={{ marginTop: 12 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              已完成 {doneCount} / {checklist.length} 项
              {isAi ? ' · AI 工具的提示词占位符必须都能在表单字段里找到' : ' · 前端工具需有对应的专用工作台组件'}
            </Typography.Text>
          </div>
        </Section>
      </Form>
    </EditPageLayout>
  )
}
