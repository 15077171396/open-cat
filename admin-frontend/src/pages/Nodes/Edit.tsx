import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Button, Col, Form, Input, Row, Select, Space, Switch, Upload } from 'antd'
import {
  CheckCircleOutlined,
  CheckOutlined,
  DeleteOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  PictureOutlined,
  ReloadOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import type { UploadProps } from 'antd'
import { apiPost, apiPut, apiGet } from '../../api/client'
import QuillEditor from '../../components/QuillEditor'
import type { EditorBridgeApi } from '../../components/ai-writing/editorBridge'
import { useAiWriting } from '../../components/ai-writing/useAiWriting'
import AiSelectionBubble from '../../components/ai-writing/AiSelectionBubble'
import AiWritingDrawer from '../../components/ai-writing/AiWritingDrawer'
import type { AiFillField, WritingOptions } from '../../components/ai-writing/types'
import { markdownToSafeHtml } from '../../components/editor/markdown'
import ThirdPartyImport, { buildMetaPatch } from '../../components/ThirdPartyImport'
import EditPageLayout from '../../components/EditPageLayout'
import Section from '../../components/AdminSection'
import { useCategoryOptions } from '../../hooks/useCategoryOptions'
import { useUnsavedChangesGuard, clearFormTouched } from '../../hooks/useUnsavedChangesGuard'
import { NODE_CATEGORIES } from '../../constants'
import { getErrorMessage, isFormValidationError } from '../../utils/error'
import { uploadImage } from '../../utils/upload'
import { isSafeUrl } from '../../utils/url'
import type { Node } from '../../types'

import { toast } from '../../utils/toast'
/** 封面图可选格式（与后端 /uploads/image 白名单一致） */
const IMAGE_ACCEPT = 'image/jpeg,image/png,image/webp,image/gif'

export default function NodesEdit() {
  const navigate = useNavigate()
  const { id } = useParams()
  const isEdit = !!id
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(isEdit)
  const [saving, setSaving] = useState(false)
  const categoryOptions = useCategoryOptions('node', NODE_CATEGORIES)
  const [coverUploading, setCoverUploading] = useState(false)
  // 预览量（详情接口返回）：仅用于「发布检查」脚注展示
  const [viewCount, setViewCount] = useState<number | null>(null)

  const bridgeRef = useRef<EditorBridgeApi | null>(null)
  const [aiOptions, setAiOptions] = useState<WritingOptions | null>(null)
  const [aiBubble, setAiBubble] = useState<{ visible: boolean; top: number; left: number }>({
    visible: false, top: 0, left: 0,
  })
  const ai = useAiWriting({
    bridgeRef,
    // 百科页上下文与操作无关：整篇正文 + 当前编辑器大纲
    buildBaseRequest: (_operation) => ({
      content_type: 'node',
      content_id: id ? Number(id) : null,
      context: {
        title: form.getFieldValue('name') || '',
        category: form.getFieldValue('category') || '',
        article_text: form.getFieldValue('content') || '',
        outline: bridgeRef.current?.getOutline() || [],
      },
    }),
  })

  const AI_FIELD_MAP: Record<AiFillField, string> = {
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

  // 「发布检查」与封面预览要随输入即时更新（Form.Item 内的值读不到响应式变化）
  const watchedName = Form.useWatch('name', form)
  const watchedCategory = Form.useWatch('category', form)
  const watchedDescription = Form.useWatch('description', form)
  const watchedCover = Form.useWatch('cover_image', form)
  const watchedAuthor = Form.useWatch('author_name', form)

  const checklist = [
    { label: '节点名称', done: !!(watchedName || '').trim() },
    { label: '分类', done: !!watchedCategory },
    { label: '简介', done: !!(watchedDescription || '').trim() },
    { label: '封面图', done: !!watchedCover },
  ]
  const doneCount = checklist.filter((c) => c.done).length

  // 卡片封面上传到 COS：成功后写入 cover_image（复用资源表的同一 helper）
  const handleCoverUpload: UploadProps['customRequest'] = async (options) => {
    const { file, onSuccess, onError } = options
    setCoverUploading(true)
    try {
      const result = await uploadImage(file as File)
      form.setFieldValue('cover_image', result.url)
      onSuccess?.(result, file as File)
      toast.success('封面上传成功')
    } catch (err) {
      toast.error(getErrorMessage(err, '封面上传失败'))
      onError?.(err instanceof Error ? err : new Error('上传失败'))
    } finally {
      setCoverUploading(false)
    }
  }
  // isFieldsTouched() 无参 = 任意字段被编辑过（true 是 all 语义会漏判单字段修改）
  const confirmLeave = useUnsavedChangesGuard(() => form.isFieldsTouched())

  useEffect(() => {
    const init = async () => {
      if (isEdit && id) {
        try {
          const d = await apiGet<Node>(`/admin/nodes/${id}`)
          const content = markdownToSafeHtml(d.content || '')
          form.setFieldsValue({
            name: d.name || '',
            category: d.category || NODE_CATEGORIES[0],
            description: d.description || '',
            content,
            // 后端返回 null（未填写）时归一为空串，否则 antd 受控 Input 会在 null 与 string 间反复切换
            cover_image: d.cover_image || '',
            // 作者由后端自动带出（无手工输入控件）：这里只做原样回存，避免保存时把已有作者快照抹掉
            author_name: d.author_name || '',
            author_avatar_url: d.author_avatar_url || '',
            seo_title: d.seo_title || '',
            seo_description: d.seo_description || '',
            seo_keywords: d.seo_keywords || '',
            // 草稿态：后端返回 false 时开关关闭；历史数据（true）保持原样
            is_published: d.is_published ?? true,
          })
          // antd v6：程序化回填会把字段标记为 touched，需清除避免未保存守卫误报
          clearFormTouched(form, [
            'name', 'category', 'description', 'content',
            'cover_image',
            'author_name', 'author_avatar_url',
            'seo_title', 'seo_description', 'seo_keywords', 'is_published',
          ])
          setViewCount(typeof d.view_count === 'number' ? d.view_count : null)
        } catch {
          toast.error('加载节点失败')
          navigate('/nodes')
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
      // 与资源页同口径：封面必须是 http/https，避免把 javascript:/data: 存进公开展示位
      if (values.cover_image && !isSafeUrl(values.cover_image)) {
        toast.error('封面图 URL 必须以 http:// 或 https:// 开头')
        return
      }
      setSaving(true)
      const payload = {
        name: values.name,
        category: values.category,
        description: values.description,
        content: values.content,
        // 封面 + 发布作者：trim 后空串交给后端按「清除」处理（前台回落「不渲染」）
        cover_image: values.cover_image?.trim() || '',
        // 作者原样回存：后端仅在作者为空时用发布账号补写（作者是「谁发布的」而非「谁最后改的」）
        author_name: values.author_name?.trim() || '',
        author_avatar_url: values.author_avatar_url?.trim() || '',
        // SEO 信息：trim 后空串交给后端按「清除」处理（前台回落默认标题/简介）
        seo_title: values.seo_title?.trim() || '',
        seo_description: values.seo_description?.trim() || '',
        seo_keywords: values.seo_keywords?.trim() || '',
        is_published: values.is_published ?? true,
      }
      if (isEdit && id) {
        await apiPut(`/admin/nodes/${id}`, payload)
      } else {
        await apiPost('/admin/nodes', payload)
      }
      navigate('/nodes')
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
      title={isEdit ? '编辑节点' : '新建节点'}
      backLabel="返回知识百科"
      loading={loading}
      saving={saving}
      onCancel={() => confirmLeave(() => navigate('/nodes'))}
      onSave={handleSave}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          name: '',
          category: NODE_CATEGORIES[0],
          description: '',
          content: '',
          cover_image: '',
          author_name: '',
          author_avatar_url: '',
          seo_title: '',
          seo_description: '',
          seo_keywords: '',
          is_published: true,
        }}
      >
        {/* 封面与作者头像必须**注册**为字段：这两处由上传回调 `setFieldValue` 写 store，
            而 `validateFields()` 只回传已注册字段（未注册的键永远是 undefined），
            读 `values.cover_image` 会得到 undefined → 提交空串 → 后端按「清除」把已有值写成 NULL。
            与 Resources/Edit.tsx 的 cover_image 隐藏域同一写法。 */}
        <Form.Item name="cover_image" hidden><Input /></Form.Item>
        {/* 作者快照同理：字段已无输入控件，但不注册就会在保存时把作者清空 */}
        <Form.Item name="author_name" hidden><Input /></Form.Item>
        <Form.Item name="author_avatar_url" hidden><Input /></Form.Item>

        <Section
          icon={<InfoCircleOutlined />}
          title="基本信息"
          hint="条目名称是前台百科词条的标题，通常用该工具/技术的原始名称；分类决定卡片配色与筛选归属。"
        >
          <Row gutter={16}>
            <Col xs={24} md={16}>
              <Form.Item label="节点名称" name="name" rules={[{ required: true, message: '请填写节点名称' }]}>
                <Input placeholder="如 CheckpointLoaderSimple" maxLength={200} showCount />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="分类" name="category" rules={[{ required: true }]}>
                <Select options={categoryOptions.map((c) => ({ value: c, label: c }))} />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item
            label="简介"
            name="description"
            extra="一句话说明该节点做什么；展示在百科卡片上，也会用于搜索引擎摘要兜底。"
            style={{ marginBottom: 0 }}
          >
            <Input.TextArea rows={2} placeholder="一句话描述该节点作用" />
          </Form.Item>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<PictureOutlined />}
          title="封面图"
          hint="百科卡片左侧首图；未上传时前台回落到分类图标底色。支持 JPG / PNG / WebP / GIF（≤5MB）。"
        >
          <Form.Item noStyle shouldUpdate={(prev, cur) => prev.cover_image !== cur.cover_image}>
            {() => {
              const cover = form.getFieldValue('cover_image')
              return (
                <div className="adm-cover-row">
                  <div className={`adm-cover adm-cover--box${cover ? ' adm-cover--filled' : ''}`}>
                    {cover ? (
                      <img src={cover} alt="封面预览" />
                    ) : (
                      <Upload accept={IMAGE_ACCEPT} showUploadList={false} customRequest={handleCoverUpload}>
                        <div className="adm-cover__placeholder">
                          {coverUploading ? (
                            <ReloadOutlined spin style={{ fontSize: 22 }} />
                          ) : (
                            <>
                              <PictureOutlined style={{ fontSize: 22 }} />
                              <span>点击上传封面图</span>
                              <span style={{ color: '#d9d9d9' }}>推荐尺寸 16:9</span>
                            </>
                          )}
                        </div>
                      </Upload>
                    )}
                  </div>
                  <div className="adm-cover-side">
                    <Space size={8} wrap>
                      <Upload accept={IMAGE_ACCEPT} showUploadList={false} customRequest={handleCoverUpload}>
                        <Button icon={<PictureOutlined />} loading={coverUploading}>
                          {cover ? '更换封面图' : '上传封面图'}
                        </Button>
                      </Upload>
                      {cover && (
                        <Button danger icon={<DeleteOutlined />} onClick={() => form.setFieldValue('cover_image', '')}>
                          删除封面
                        </Button>
                      )}
                    </Space>
                    <div className="adm-cover__note">
                      推荐 16:9（如 1280×720）。留空时前台用分类图标底色兜底，不会出现空白封面。
                    </div>
                  </div>
                </div>
              )
            }}
          </Form.Item>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<FileTextOutlined />}
          title="正文内容"
          hint="支持富文本编辑；也可直接从 GitHub / 知乎 / 掘金等平台拉取正文后继续修改。"
        >
          {/* 从第三方导入正文（可选），与手动编写二选一，导入后仍可继续编辑；
              拉取成功后按「名称 / 简介」自动回填（仅填空值，不覆盖已填内容） */}
          <ThirdPartyImport
            getContent={() => (form.getFieldValue('content') as string) || ''}
            onContentChange={(html) => form.setFieldValue('content', html)}
            onFillMeta={(meta) => {
              const patch = buildMetaPatch(
                {
                  title: (form.getFieldValue('name') as string) || '',
                  summary: (form.getFieldValue('description') as string) || '',
                  tags: [], // 节点无标签字段，不参与回填
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
              return filled
            }}
          />

          <Form.Item name="content" style={{ marginBottom: 0 }}>
            <QuillEditor
              placeholder="请输入节点正文内容..."
              height={560}
              aiEnabled={!!aiOptions?.enabled}
              onAiOpen={() => ai.setOpen(true)}
              onReady={(api) => { bridgeRef.current = api }}
              onSelectionChange={handleSelection}
            />
          </Form.Item>
        </Section>

        <div className="adm-divider" />

        {/* SEO 信息（台账 P1-17）：百科条目是栏目词与长尾词的目标页，
            没有自定义 TDK 就只能吃模板标题，长尾词覆盖能力受限 */}
        <Section
          icon={<SearchOutlined />}
          title="SEO 信息（选填）"
          hint="仅用于搜索引擎收录展示（节点页的标题、摘要与关键词），前台页面不显示；未填写时自动使用「节点名称 - ComfyUI节点详解 - AI学习猫」与节点简介。"
        >
          <Form.Item
            label="SEO 标题"
            name="seo_title"
            extra="搜索引擎结果中显示的页面标题，建议 30 字以内（约 60 字符），突出节点名与用途。"
          >
            <Input
              maxLength={200}
              showCount
              placeholder="如：CheckpointLoaderSimple 节点详解｜模型加载与参数说明"
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
              placeholder="如：CheckpointLoaderSimple 用于加载大模型检查点，本文说明各输入参数含义、常见报错与搭配用法。"
            />
          </Form.Item>

          <Form.Item
            label="SEO 关键词"
            name="seo_keywords"
            extra="多个关键词用英文逗号分隔，8～12 个为宜，覆盖品牌词、产品词与长尾词。"
            style={{ marginBottom: 0 }}
          >
            <Input maxLength={500} placeholder="如：CheckpointLoaderSimple, ComfyUI节点, 模型加载, ComfyUI教程" />
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
            预览 <strong>{viewCount == null ? '—' : viewCount.toLocaleString('zh-CN')}</strong> 次
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
