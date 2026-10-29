import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Col, Form, Input, Row, Select, Switch } from 'antd'
import {
  CheckCircleOutlined,
  CheckOutlined,
  CloudDownloadOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import { apiPost, apiPut, apiGet } from '../../api/client'
import QuillEditor from '../../components/QuillEditor'
import type { EditorBridgeApi } from '../../components/ai-writing/editorBridge'
import { useAiWriting } from '../../components/ai-writing/useAiWriting'
import AiSelectionBubble from '../../components/ai-writing/AiSelectionBubble'
import AiWritingDrawer from '../../components/ai-writing/AiWritingDrawer'
import type { AiFillField, WritingOptions } from '../../components/ai-writing/types'
import { markdownToSafeHtml } from '../../components/editor/markdown'
import EditPageLayout from '../../components/EditPageLayout'
import Section from '../../components/AdminSection'
import PluginSourcesEditor from '../../components/PluginSourcesEditor'
import ThirdPartyImport, { buildMetaPatch } from '../../components/ThirdPartyImport'
import { useCategoryOptions } from '../../hooks/useCategoryOptions'
import { useUnsavedChangesGuard, clearFormTouched } from '../../hooks/useUnsavedChangesGuard'
import { PLUGIN_CATEGORIES, EMPTY_PLUGIN_SOURCE } from '../../constants'
import type { Plugin, PluginSource } from '../../types'
import { getErrorMessage, isFormValidationError } from '../../utils/error'
import { isSafeUrl } from '../../utils/url'

import { toast } from '../../utils/toast'
/** 扩展分类默认下拉值（静态常量，动态分类加载失败时回退） */
const PLUGIN_CATEGORY_FALLBACK = PLUGIN_CATEGORIES.map((p) => p.value)

/** 扩展表单值（Form.useForm 泛型，消除 getFieldValue 断言） */
interface PluginFormValues {
  name: string
  category: string
  /** 发布作者：展示在扩展卡片与详情页；空串 = 不展示作者行 */
  author_name: string
  /** 作者头像 URL：空串 = 用作者名首字兜底 */
  author_avatar_url: string
  description: string
  content: string
  /** SEO 信息（台账 P1-17）：仅写入页面 meta 标签供搜索引擎抓取，前台页面不展示 */
  seo_title: string
  seo_description: string
  seo_keywords: string
  is_published: boolean
}

export default function PluginsEdit() {
  const navigate = useNavigate()
  const { id } = useParams()
  const isEdit = !!id
  const [form] = Form.useForm<PluginFormValues>()
  const [loading, setLoading] = useState(isEdit)
  const [saving, setSaving] = useState(false)
  const [sources, setSources] = useState<PluginSource[]>([{ ...EMPTY_PLUGIN_SOURCE }])
  const categoryOptions = useCategoryOptions('plugin', PLUGIN_CATEGORY_FALLBACK)
  // 浏览量（详情接口返回）：仅用于「发布检查」脚注展示
  const [viewCount, setViewCount] = useState<number | null>(null)
  // 来源列表的初始快照（isFieldsTouched 覆盖不到表单外的 state，用 JSON 比较补充）
  const initialSourcesJsonRef = useRef(JSON.stringify([{ ...EMPTY_PLUGIN_SOURCE }]))
  // 未保存守卫：isFieldsTouched() 无参 = 任意字段被编辑过（true 是 all 语义会漏判单字段修改）
  const confirmLeave = useUnsavedChangesGuard(
    () => form.isFieldsTouched() || JSON.stringify(sources) !== initialSourcesJsonRef.current,
  )

  const bridgeRef = useRef<EditorBridgeApi | null>(null)
  const [aiOptions, setAiOptions] = useState<WritingOptions | null>(null)
  const [aiBubble, setAiBubble] = useState({ visible: false, top: 0, left: 0 })
  const ai = useAiWriting({
    bridgeRef,
    // 扩展页上下文与操作无关：整篇正文 + 当前编辑器大纲
    buildBaseRequest: (_operation) => ({
      content_type: 'plugin',
      content_id: id ? Number(id) : null,
      context: {
        title: form.getFieldValue('name') || '',
        category: form.getFieldValue('category') || '',
        article_text: form.getFieldValue('content') || '',
        outline: bridgeRef.current?.getOutline() || [],
      },
    }),
  })

  // 表单是泛型 Form.useForm<PluginFormValues>，键名必须收窄为表单字段名（string 会被 TS 拒绝）
  const AI_FIELD_MAP: Record<AiFillField, keyof PluginFormValues> = {
    title: 'name',
    summary: 'description',
    seo_description: 'seo_description',
    keywords: 'seo_keywords',
  }
  const fillAiField = (field: AiFillField, value: string) => {
    form.setFieldValue(AI_FIELD_MAP[field], value)
    toast.success('已填入表单，保存后生效')
  }

  useEffect(() => {
    apiGet<WritingOptions>('/admin/ai/writing/options')
      // 配置结构不完整时按不可用处理：抽屉直接读 operations.map，半截响应会让页面崩掉
      .then((options) => setAiOptions(options && Array.isArray(options.operations) ? options : null))
      .catch(() => setAiOptions(null))
  }, [])

  const handleSelection = (hasSelection: boolean) => {
    if (!hasSelection) {
      setAiBubble((prev) => ({ ...prev, visible: false }))
      return
    }
    const selection = window.getSelection()
    if (!selection || selection.isCollapsed || !selection.rangeCount) {
      // 编辑器回调说「有选区」但浏览器侧已折叠/失效时，同样不能留下旧浮条
      setAiBubble((prev) => ({ ...prev, visible: false }))
      return
    }
    const rect = selection.getRangeAt(0).getBoundingClientRect()
    setAiBubble({ visible: true, top: rect.bottom + 8, left: rect.left })
  }

  // 「发布检查」要随输入即时更新（Form.Item 内的值读不到响应式变化）
  const watchedName = Form.useWatch('name', form)
  const watchedCategory = Form.useWatch('category', form)
  const watchedDescription = Form.useWatch('description', form)
  const watchedAuthor = Form.useWatch('author_name', form)

  const sourceCount = sources.filter((s) => s.url.trim()).length
  const checklist = [
    { label: '扩展名称', done: !!(watchedName || '').trim() },
    { label: '分类', done: !!watchedCategory },
    { label: '简介', done: !!(watchedDescription || '').trim() },
    { label: '获取来源', done: sourceCount > 0 },
  ]
  const doneCount = checklist.filter((c) => c.done).length

  useEffect(() => {
    const init = async () => {
      if (isEdit && id) {
        try {
          const d = await apiGet<Plugin>(`/admin/plugins/${id}`)
          const content = markdownToSafeHtml(d.content || '')
          form.setFieldsValue({
            name: d.name || '',
            category: d.category || PLUGIN_CATEGORIES[0].value,
            // 后端返回 null（未填写）时归一为空串，否则 antd 受控 Input 会在 null 与 string 间反复切换
            author_name: d.author_name || '',
            author_avatar_url: d.author_avatar_url || '',
            description: d.description || '',
            content,
            seo_title: d.seo_title || '',
            seo_description: d.seo_description || '',
            seo_keywords: d.seo_keywords || '',
            // 草稿态：后端返回 false 时开关关闭；历史数据（true）保持原样
            is_published: d.is_published ?? true,
          })
          // antd v6：程序化回填会把字段标记为 touched，需清除避免未保存守卫误报
          clearFormTouched(form, [
            'name', 'category',
            'author_name', 'author_avatar_url',
            'description', 'content',
            'seo_title', 'seo_description', 'seo_keywords', 'is_published',
          ])
          setViewCount(typeof d.view_count === 'number' ? d.view_count : null)
          const loadedSources = d.sources && d.sources.length
            ? d.sources.map((s) => ({
                source_name: s.source_name,
                url: s.url,
                extract_code: s.extract_code || '',
              }))
            : [{ ...EMPTY_PLUGIN_SOURCE }]
          setSources(loadedSources)
          initialSourcesJsonRef.current = JSON.stringify(loadedSources)
        } catch {
          toast.error('加载扩展失败')
          navigate('/plugins')
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
      const unsafeSources = sources.filter((s) => s.url.trim() && !isSafeUrl(s.url))
      if (unsafeSources.length > 0) {
        toast.error('来源链接不安全，仅支持 http/https 协议')
        return
      }
      setSaving(true)
      const payload = {
        name: values.name,
        category: values.category,
        // 作者：原样回存，后端仅在作者为空时用发布账号补写（作者是「谁发布的」而非「谁最后改的」）
        author_name: values.author_name?.trim() || '',
        author_avatar_url: values.author_avatar_url?.trim() || '',
        description: values.description,
        content: values.content,
        // SEO 信息：trim 后空串交给后端按「清除」处理（前台回落默认标题/简介）
        seo_title: values.seo_title?.trim() || '',
        seo_description: values.seo_description?.trim() || '',
        seo_keywords: values.seo_keywords?.trim() || '',
        is_published: values.is_published ?? true,
        sources: sources
          .filter((s) => s.url.trim())
          .map((s) => ({
            source_name: s.source_name,
            url: s.url,
            extract_code: s.extract_code || undefined,
          })),
      }
      if (isEdit && id) {
        await apiPut(`/admin/plugins/${id}`, payload)
      } else {
        await apiPost('/admin/plugins', payload)
      }
      navigate('/plugins')
    } catch (err: unknown) {
      if (isFormValidationError(err)) return
      toast.error(getErrorMessage(err, '保存失败'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
    <EditPageLayout
      title={isEdit ? '编辑扩展' : '新建扩展'}
      backLabel="返回扩展管理"
      loading={loading}
      saving={saving}
      onCancel={() => confirmLeave(() => navigate('/plugins'))}
      onSave={handleSave}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          name: '',
          category: PLUGIN_CATEGORIES[0].value,
          author_name: '',
          author_avatar_url: '',
          description: '',
          content: '',
          seo_title: '',
          seo_description: '',
          seo_keywords: '',
          is_published: true,
        }}
      >
        {/* 作者快照必须**注册**为字段：validateFields() 只回传已注册字段，
            读不到会提交空串把已有作者/头像清掉。表单已无输入控件，仅做原样回存。 */}
        <Form.Item name="author_name" hidden><Input /></Form.Item>
        <Form.Item name="author_avatar_url" hidden><Input /></Form.Item>

        <Section
          icon={<InfoCircleOutlined />}
          title="基本信息"
          hint="扩展名称是前台卡片的标题；分类决定卡片配色与筛选归属（取值见「分类管理」）。"
        >
          <Row gutter={16}>
            <Col xs={24} md={16}>
              <Form.Item label="扩展名称" name="name" rules={[{ required: true, message: '请填写扩展名称' }]}>
                <Input placeholder="请输入扩展名称" maxLength={200} showCount />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="分类" name="category" rules={[{ required: true, message: '请选择分类' }]}>
                <Select options={categoryOptions.map((c) => ({ value: c, label: c }))} />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item
            label="简介"
            name="description"
            rules={[{ required: true, message: '请填写简介' }]}
            extra="一句话说明这个扩展做什么；展示在扩展卡片上，也会用于搜索引擎摘要兜底。"
            style={{ marginBottom: 0 }}
          >
            <Input.TextArea rows={3} placeholder="一句话简介" />
          </Form.Item>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<FileTextOutlined />}
          title="正文内容"
          hint="支持富文本编辑；也可直接从 GitHub / 知乎 / 掘金等平台拉取正文后继续修改（拉取到的作者会写入「发布作者」）。"
        >
          {/* 从第三方导入正文（可选），导入后按名称/简介/作者自动回填（仅填空值不覆盖） */}
          <ThirdPartyImport
            getContent={() => form.getFieldValue('content') || ''}
            onContentChange={(html) => form.setFieldValue('content', html)}
            onFillMeta={(meta) => {
              const patch = buildMetaPatch(
                {
                  title: form.getFieldValue('name') || '',
                  summary: form.getFieldValue('description') || '',
                  tags: [], // 扩展无标签字段，不参与回填
                },
                meta,
              )
              const filled: string[] = []
              if (patch.title !== undefined) {
                form.setFieldValue('name', patch.title)
                filled.push('名称')
              }
              if (patch.summary !== undefined) {
                form.setFieldValue('description', patch.summary)
                filled.push('简介')
              }
              // 作者仅在留空时回填（与标题/简介同一「不覆盖已填内容」语义）
              if (!(form.getFieldValue('author_name') || '').trim() && meta.author.trim()) {
                form.setFieldValue('author_name', meta.author.trim())
                filled.push('作者')
              }
              return filled
            }}
          />

          <Form.Item name="content" style={{ marginBottom: 0 }}>
            <QuillEditor
              placeholder="请输入扩展正文内容..."
              height={560}
              aiEnabled={!!aiOptions?.enabled}
              onAiOpen={() => ai.setOpen(true)}
              onReady={(api) => { bridgeRef.current = api }}
              onSelectionChange={handleSelection}
            />
          </Form.Item>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<CloudDownloadOutlined />}
          title="获取来源"
          hint="扩展的下载 / 仓库地址（如 GitHub、夸克网盘、官方文档）；留空则前台不显示「获取方式」。"
        >
          <Form.Item style={{ marginBottom: 0 }}>
            <PluginSourcesEditor sources={sources} onChange={setSources} />
          </Form.Item>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<SearchOutlined />}
          title="SEO 信息（选填）"
          hint="仅用于搜索引擎收录展示（扩展页的标题、摘要与关键词），前台页面不显示；未填写时自动使用「扩展名称 - 扩展详解 - AI学习猫」与扩展简介。"
        >
          <Form.Item
            label="SEO 标题"
            name="seo_title"
            extra="搜索引擎结果中显示的页面标题，建议 30 字以内（约 60 字符），突出扩展名与用途。"
          >
            <Input
              maxLength={200}
              showCount
              placeholder="如：VS Code 插件详解｜安装、配置与常见问题"
            />
          </Form.Item>

          <Form.Item
            label="SEO 描述"
            name="seo_description"
            extra="搜索引擎结果中的摘要文案，建议 150 字以内，自然融入关键词。"
          >
            <Input.TextArea
              rows={3}
              maxLength={500}
              showCount
              placeholder="如：该扩展提供哪些能力、解决什么问题，本文说明安装步骤与常见报错处理。"
            />
          </Form.Item>

          <Form.Item
            label="SEO 关键词"
            name="seo_keywords"
            extra="多个关键词用英文逗号分隔，8～12 个为宜，覆盖品牌词、产品词与长尾词。"
            style={{ marginBottom: 0 }}
          >
            <Input maxLength={500} placeholder="如：扩展名, 扩展下载, 扩展安装, 配置教程" />
          </Form.Item>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<CheckCircleOutlined />}
          title="发布检查"
          hint="保存前确认以下要素是否齐全；缺项不影响保存，但会让前台卡片显示不完整。"
        >
          <Form.Item
            label="发布状态"
            name="is_published"
            valuePropName="checked"
            extra="关闭后为草稿：前台列表 / 详情 / 首页 / sitemap 均不可见，后台仍可正常编辑"
            style={{ marginBottom: 16 }}
          >
            <Switch checkedChildren="已发布" unCheckedChildren="草稿" />
          </Form.Item>
          <div className="adm-checklist adm-checklist--row">
            {checklist.map((item) => (
              <div
                key={item.label}
                className={`adm-checklist__item${item.done ? ' adm-checklist__item--done' : ''}`}
              >
                <span className="adm-checklist__dot">{item.done ? <CheckOutlined /> : ''}</span>
                <span>{item.label}</span>
              </div>
            ))}
          </div>
          <div className="adm-checklist__foot">
            已完成 <strong>{doneCount}/{checklist.length}</strong>
            <span className="adm-sep"> · </span>
            获取来源 <strong>{sourceCount}</strong> 条
            <span className="adm-sep"> · </span>
            浏览 <strong>{viewCount == null ? '—' : viewCount.toLocaleString('zh-CN')}</strong> 次
            <span className="adm-sep"> · </span>
            发布作者 <strong>{watchedAuthor || '保存时自动带出'}</strong>
          </div>
        </Section>
      </Form>
    </EditPageLayout>

    <AiSelectionBubble
      visible={aiBubble.visible && !!aiOptions?.enabled}
      top={aiBubble.top}
      left={aiBubble.left}
      onAction={(operation) => { void ai.run(operation); setAiBubble((p) => ({ ...p, visible: false })) }}
      onMore={() => { ai.setOpen(true); setAiBubble((p) => ({ ...p, visible: false })) }}
    />
    {/* 未启用/配置未就绪时不挂载面板：此时也没有任何入口能打开它 */}
    {aiOptions?.enabled ? (
      <AiWritingDrawer
        open={ai.open}
        onClose={() => { ai.cancel(); ai.setOpen(false) }}
        options={aiOptions}
        turns={ai.turns}
        busy={ai.busy}
        onRun={(operation, runOptions) => { void ai.run(operation, runOptions) }}
        onCancel={ai.cancel}
        // 图片按张插入由抽屉的 onInsertImage 提供；「插入光标」仅采纳文本结果
        onApply={(turnId, mode) => ai.applyTurn(turnId, mode)}
        onRegenerate={ai.regenerate}
        onFillResult={fillAiField}
        onInsertImage={(url, alt) => bridgeRef.current?.insertImage(url, alt)}
      />
    ) : null}
  </>
  )
}
