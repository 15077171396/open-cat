import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Button, Col, Form, Input, Row, Select, Space, Switch, Upload } from 'antd'
import {
  CheckCircleOutlined,
  CheckOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  PictureOutlined,
  ReloadOutlined,
  SearchOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import type { UploadProps } from 'antd'
import { apiGet, apiPost, apiPut } from '../../api/client'
import QuillEditor from '../../components/QuillEditor'
import type { EditorBridgeApi } from '../../components/ai-writing/editorBridge'
import { useAiWriting } from '../../components/ai-writing/useAiWriting'
import AiSelectionBubble from '../../components/ai-writing/AiSelectionBubble'
import AiWritingDrawer from '../../components/ai-writing/AiWritingDrawer'
import type { AiFillField, WritingOptions } from '../../components/ai-writing/types'
import { markdownToSafeHtml } from '../../components/editor/markdown'
import NetdiskLinksEditor from '../../components/NetdiskLinksEditor'
import ThirdPartyImport, { buildMetaPatch } from '../../components/ThirdPartyImport'
import EditPageLayout from '../../components/EditPageLayout'
import Section from '../../components/AdminSection'
import { useCategoryOptions } from '../../hooks/useCategoryOptions'
import { useUnsavedChangesGuard, clearFormTouched } from '../../hooks/useUnsavedChangesGuard'
import { RESOURCE_TYPES, EMPTY_NETDISK_LINK } from '../../constants'
import { getErrorMessage, isFormValidationError } from '../../utils/error'
import { isSafeUrl } from '../../utils/url'
import { uploadResourceFile, uploadImage } from '../../utils/upload'
import type { NetdiskLink, Resource } from '../../types'

import { toast } from '../../utils/toast'
/** 资源类型默认下拉值（静态常量，动态分类加载失败时回退） */
const RESOURCE_TYPE_FALLBACK = RESOURCE_TYPES.map((r) => r.value)

/** 封面图可选格式（与后端 /uploads/image 的白名单一致） */
const IMAGE_ACCEPT = 'image/jpeg,image/png,image/webp,image/gif'

/** 资源表单值（Form.useForm 泛型，消除 getFieldValue 断言） */
interface ResourceFormValues {
  name: string
  description: string
  content: string
  resource_type: string
  file_size: string
  is_featured: boolean
  is_pinned: boolean
  cover_image: string
  /** 发布作者快照：由后端按「第三方导入作者 / 发布账号」自动带出，表单不提供输入控件 */
  author_name: string
  /** 作者头像快照：同上 */
  author_avatar_url: string
  tags: string[]
  /** SEO 信息：仅写入页面 meta 标签供搜索引擎抓取，前台页面不展示 */
  seo_title: string
  seo_description: string
  seo_keywords: string
  is_published: boolean
}

export default function ResourcesEdit() {
  const navigate = useNavigate()
  const { id } = useParams()
  const isEdit = !!id
  const [form] = Form.useForm<ResourceFormValues>()
  const [loading, setLoading] = useState(isEdit)
  const [saving, setSaving] = useState(false)
  const [netdiskLinks, setNetdiskLinks] = useState<NetdiskLink[]>([{ ...EMPTY_NETDISK_LINK }])
  const categoryOptions = useCategoryOptions('resource', RESOURCE_TYPE_FALLBACK)
  const [fileUploading, setFileUploading] = useState(false)
  const [coverUploading, setCoverUploading] = useState(false)

  // 侧栏「发布检查」与封面预览要随输入即时更新（Form.Item 内的值读不到响应式变化）
  const watchedName = Form.useWatch('name', form)
  const watchedDescription = Form.useWatch('description', form)
  const watchedTags = Form.useWatch('tags', form)
  const watchedCover = Form.useWatch('cover_image', form)
  const watchedAuthor = Form.useWatch('author_name', form)
  // 网盘链接列表的初始快照（isFieldsTouched 覆盖不到表单外的 state，用 JSON 比较补充）
  const initialLinksJsonRef = useRef(JSON.stringify([{ ...EMPTY_NETDISK_LINK }]))
  // 未保存守卫：isFieldsTouched() 无参 = 任意字段被编辑过（true 是 all 语义会漏判单字段修改）
  const confirmLeave = useUnsavedChangesGuard(
    () => form.isFieldsTouched() || JSON.stringify(netdiskLinks) !== initialLinksJsonRef.current,
  )

  const bridgeRef = useRef<EditorBridgeApi | null>(null)
  const [aiOptions, setAiOptions] = useState<WritingOptions | null>(null)
  const [aiBubble, setAiBubble] = useState({ visible: false, top: 0, left: 0 })
  const ai = useAiWriting({
    bridgeRef,
    // 资源页上下文与操作无关：整篇正文 + 当前编辑器大纲
    buildBaseRequest: (_operation) => ({
      content_type: 'resource',
      content_id: id ? Number(id) : null,
      context: {
        title: form.getFieldValue('name') || '',
        // 资源页无 category 字段：分类即 resource_type（后端 content_types 亦按此映射）
        category: form.getFieldValue('resource_type') || '',
        tags: form.getFieldValue('tags') || [],
        article_text: form.getFieldValue('content') || '',
        outline: bridgeRef.current?.getOutline() || [],
      },
    }),
  })

  // 表单是泛型 Form.useForm<ResourceFormValues>，键名必须收窄为表单字段名（string 会被 TS 拒绝）
  const AI_FIELD_MAP: Record<AiFillField, keyof ResourceFormValues> = {
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

  const linkCount = netdiskLinks.filter((l) => l.url.trim()).length
  const checklist = [
    { label: '资源名称', done: !!(watchedName || '').trim() },
    { label: '一句话简介', done: !!(watchedDescription || '').trim() },
    { label: '资源标签', done: (watchedTags || []).length > 0 },
    { label: '封面图', done: !!watchedCover },
    { label: '下载链接', done: linkCount > 0 },
  ]
  const doneCount = checklist.filter((c) => c.done).length

  // 资源文件上传到 COS：成功后以 official(官方直链) 形式追加到网盘链接列表
  const handleFileUpload: UploadProps['customRequest'] = async (options) => {
    const { file, onSuccess, onError } = options
    const f = file as File
    setFileUploading(true)
    try {
      const result = await uploadResourceFile(f)
      setNetdiskLinks((prev) => [
        ...prev,
        { platform: 'official', url: result.url, extract_code: '' },
      ])
      onSuccess?.(result, f)
      toast.success(`文件上传成功：${result.filename || f.name}`)
    } catch (err) {
      toast.error(getErrorMessage(err, '文件上传失败'))
      onError?.(err instanceof Error ? err : new Error('上传失败'))
    } finally {
      setFileUploading(false)
    }
  }

  // 封面图上传到 COS：成功后写入表单隐藏字段 cover_image
  const handleCoverUpload: UploadProps['customRequest'] = async (options) => {
    const { file, onSuccess, onError } = options
    const f = file as File
    setCoverUploading(true)
    try {
      const result = await uploadImage(f)
      form.setFieldValue('cover_image', result.url)
      onSuccess?.(result, f)
      toast.success('封面上传成功')
    } catch (err) {
      toast.error(getErrorMessage(err, '封面图上传失败'))
      onError?.(err instanceof Error ? err : new Error('上传失败'))
    } finally {
      setCoverUploading(false)
    }
  }

  useEffect(() => {
    const init = async () => {
      if (isEdit && id) {
        try {
          const d = await apiGet<Resource>(`/admin/resources/${id}`)
          const content = markdownToSafeHtml(d.content || '')
          form.setFieldsValue({
            name: d.name || '',
            description: d.description || '',
            content,
            resource_type: d.resource_type || 'tool',
            file_size: d.file_size || '',
            is_featured: d.is_featured ?? false,
            is_pinned: d.is_pinned ?? false,
            cover_image: d.cover_image || '',
            // 作者由后端自动带出：这里只做原样回存，避免保存时把已有作者快照抹掉
            author_name: d.author_name || '',
            author_avatar_url: d.author_avatar_url || '',
            tags: d.tags || [],
            seo_title: d.seo_title || '',
            seo_description: d.seo_description || '',
            seo_keywords: d.seo_keywords || '',
            // 草稿态：后端返回 false 时开关关闭；历史数据（true）保持原样
            is_published: d.is_published ?? true,
          })
          // antd v6：程序化回填会把字段标记为 touched，需清除避免未保存守卫误报
          clearFormTouched(form, ['name', 'description', 'content', 'resource_type', 'file_size', 'is_featured', 'is_pinned', 'is_published', 'cover_image', 'author_name', 'author_avatar_url', 'tags', 'seo_title', 'seo_description', 'seo_keywords'])
          setNetdiskLinks(
            d.netdisk_links?.length
              ? d.netdisk_links.map((l) => ({ platform: l.platform, url: l.url, extract_code: l.extract_code || '' }))
              : [{ ...EMPTY_NETDISK_LINK }]
          )
          initialLinksJsonRef.current = JSON.stringify(
            d.netdisk_links?.length
              ? d.netdisk_links.map((l) => ({ platform: l.platform, url: l.url, extract_code: l.extract_code || '' }))
              : [{ ...EMPTY_NETDISK_LINK }],
          )
        } catch (err) {
          toast.error(getErrorMessage(err, '加载资源失败'))
          navigate('/resources')
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
      if (values.cover_image && !isSafeUrl(values.cover_image)) {
        toast.error('封面图 URL 必须以 http:// 或 https:// 开头')
        return
      }
      const invalidLinks = netdiskLinks.filter((l) => l.url.trim() && !isSafeUrl(l.url))
      if (invalidLinks.length > 0) {
        toast.error('网盘链接必须以 http:// 或 https:// 开头')
        return
      }
      setSaving(true)
      const payload = {
        name: values.name,
        description: values.description,
        content: values.content,
        resource_type: values.resource_type,
        // 大小可选：空值传 null，避免触发后端 min_length 校验
        file_size: values.file_size || null,
        is_featured: values.is_featured ?? false,
        is_pinned: values.is_pinned ?? false,
        cover_image: values.cover_image || null,
        // 作者：原样回存，后端仅在作者为空时用发布账号补写（作者是「谁发布的」而非「谁最后改的」）
        author_name: values.author_name?.trim() || '',
        author_avatar_url: values.author_avatar_url?.trim() || '',
        tags: values.tags || [],
        // SEO 信息：空值传 null，前台回落默认标题/简介
        seo_title: values.seo_title?.trim() || null,
        seo_description: values.seo_description?.trim() || null,
        seo_keywords: values.seo_keywords?.trim() || null,
        is_published: values.is_published ?? true,
        netdisk_links: netdiskLinks
          .filter((l) => l.url.trim())
          .map((l) => ({ platform: l.platform, url: l.url, extract_code: l.extract_code })),
      }
      if (isEdit && id) {
        await apiPut(`/admin/resources/${id}`, payload)
      } else {
        await apiPost('/admin/resources', payload)
      }
      navigate('/resources')
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
      title={isEdit ? '编辑资源' : '新建资源'}
      backLabel="返回资源管理"
      loading={loading}
      saving={saving}
      onCancel={() => confirmLeave(() => navigate('/resources'))}
      onSave={handleSave}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          name: '',
          description: '',
          content: '',
          resource_type: 'tool',
          file_size: '',
          is_featured: false,
          is_pinned: false,
          cover_image: '',
          author_name: '',
          author_avatar_url: '',
          tags: [],
          seo_title: '',
          seo_description: '',
          seo_keywords: '',
          is_published: true,
        }}
      >
        {/* 非输入字段：不注册就取不到值，提交时会把已有内容（封面/作者快照）清掉 */}
        <Form.Item name="cover_image" hidden><Input /></Form.Item>
        <Form.Item name="author_name" hidden><Input /></Form.Item>
        <Form.Item name="author_avatar_url" hidden><Input /></Form.Item>

        <Section
          icon={<InfoCircleOutlined />}
          title="基本信息"
          hint="名称、分类与简介会出现在前台资源列表卡片上；标签用于筛选与相关推荐。"
        >
          <Form.Item label="资源名称" name="name" rules={[{ required: true, message: '请填写资源名称' }]}>
            <Input placeholder="请输入资源名称" maxLength={200} showCount />
          </Form.Item>

          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item label="类型" name="resource_type" rules={[{ required: true }]}>
                {/* 分类选项与「分类管理」一一对应，显示原始分类名，值存原始 name */}
                <Select options={categoryOptions.map((c) => ({ value: c, label: c }))} />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item label="大小" name="file_size" extra="仅展示用，如 12.5GB；可留空">
                <Input placeholder="如 12.5GB" maxLength={20} />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item label="简介" name="description" rules={[{ required: true, message: '请填写资源简介' }]}>
            <Input.TextArea rows={3} placeholder="一句话简介，说明这个资源是什么、能解决什么问题" />
          </Form.Item>

          <Form.Item
            label="资源标签"
            name="tags"
            rules={[{ required: true, message: '请至少添加一个标签' }]}
            extra="回车添加，可自由输入；展示在资源卡片与详情页。"
            style={{ marginBottom: 0 }}
          >
            <Select mode="tags" placeholder="输入标签后回车，如：Stable Diffusion / 免费商用" open={false} suffixIcon={null} />
          </Form.Item>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<PictureOutlined />}
          title="封面图"
          hint="卡片顶部封面与详情页 Hero 背景；未上传时前台显示类型图标底色。支持 JPG / PNG / WebP / GIF（≤5MB）。"
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
                      推荐 16:9（如 1280×720）。留空时前台用类型图标底色兜底，不会出现空白封面。
                    </div>
                  </div>
                </div>
              )
            }}
          </Form.Item>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<SettingOutlined />}
          title="发布设置"
          hint="推荐与置顶只影响该资源在前台列表中的位置与角标，不改变内容本身。"
        >
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item
                label="精选推荐"
                name="is_featured"
                valuePropName="checked"
                extra="前台优先展示并带「推荐」角标"
                style={{ marginBottom: 0 }}
              >
                <Switch checkedChildren="推荐" unCheckedChildren="普通" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item
                label="置顶"
                name="is_pinned"
                valuePropName="checked"
                extra="列表最优先展示并带「置顶」角标"
                style={{ marginBottom: 0 }}
              >
                <Switch checkedChildren="置顶" unCheckedChildren="正常" />
              </Form.Item>
            </Col>
          </Row>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<FileTextOutlined />}
          title="正文内容"
          hint="支持富文本编辑；也可直接从 GitHub / 知乎 / 掘金等平台拉取正文后继续修改。"
        >
          {/* 从第三方导入正文（可选），与手动编写二选一，导入后仍可继续编辑；
              拉取成功后按「标题 / 简介 / 标签」自动回填（仅填空值，标签合并去重） */}
          <ThirdPartyImport
            getContent={() => form.getFieldValue('content') || ''}
            onContentChange={(html) => form.setFieldValue('content', html)}
            onFillMeta={(meta) => {
              const patch = buildMetaPatch(
                {
                  title: form.getFieldValue('name') || '',
                  summary: form.getFieldValue('description') || '',
                  tags: form.getFieldValue('tags') || [],
                },
                meta,
              )
              const filled: string[] = []
              if (patch.title !== undefined) {
                form.setFieldValue('name', patch.title)
                filled.push('标题')
              }
              if (patch.summary !== undefined) {
                form.setFieldValue('description', patch.summary)
                filled.push('简介')
              }
              if (patch.tags !== undefined) {
                form.setFieldValue('tags', patch.tags)
                filled.push('标签')
              }
              return filled
            }}
          />

          <Form.Item name="content" style={{ marginBottom: 0 }}>
            <QuillEditor
              placeholder="请输入资源详细介绍..."
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
          icon={<CloudUploadOutlined />}
          title="资源文件与下载链接"
          hint="小于 20MB 的文件可直接上传到 COS 生成直链；更大的文件请填写网盘链接。"
        >
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Upload showUploadList={false} customRequest={handleFileUpload}>
                <div className="adm-drop">
                  {fileUploading ? (
                    <ReloadOutlined spin style={{ fontSize: 26, color: '#1677ff' }} />
                  ) : (
                    <>
                      <CloudUploadOutlined className="adm-drop__icon" />
                      <span className="adm-drop__title">点击上传资源文件</span>
                      <span className="adm-drop__hint">≤ 20MB，自动生成直链</span>
                    </>
                  )}
                </div>
              </Upload>
              <div className="adm-warn">
                ⚠ 超过 20MB 的文件请使用第三方网盘（夸克 / 百度网盘等），在右侧链接中手动填写。
              </div>
            </Col>
            <Col xs={24} md={16}>
              <NetdiskLinksEditor links={netdiskLinks} onChange={setNetdiskLinks} />
            </Col>
          </Row>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<SearchOutlined />}
          title="SEO 信息（选填）"
          hint="仅用于搜索引擎收录展示（搜索结果标题、摘要与关键词），前台页面不显示；未填写时自动使用资源名称与简介。"
        >
          <Form.Item
            label="SEO 标题"
            name="seo_title"
            extra="搜索引擎结果中显示的页面标题，建议 30 字以内（约 60 字符），突出资源名称与核心卖点。"
          >
            <Input maxLength={200} showCount placeholder="如：网易开源镜像站｜Linux 开源软件镜像与开发资源下载" />
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
              placeholder="如：网易开源镜像站提供 Linux 发行版、开源软件及开发环境相关镜像资源，适合 Linux 服务器、开发环境搭建使用。"
            />
          </Form.Item>

          <Form.Item
            label="SEO 关键词"
            name="seo_keywords"
            extra="多个关键词用英文逗号分隔，8～12 个为宜，覆盖品牌词、产品词与长尾词。"
            style={{ marginBottom: 0 }}
          >
            <Input
              maxLength={500}
              placeholder="如：网易开源镜像站, Linux镜像, 软件源, 开源软件镜像, 开发环境"
            />
          </Form.Item>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<CheckCircleOutlined />}
          title="发布检查"
          hint="保存前确认以下要素是否齐全；缺项不影响保存，但会让前台卡片与详情页显示不完整。"
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
