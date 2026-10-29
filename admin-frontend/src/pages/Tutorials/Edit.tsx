import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Button, Col, Empty, Form, Input, InputNumber, Popconfirm, Radio, Row, Select, Space, Switch, Upload } from 'antd'
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  CheckCircleOutlined,
  CheckOutlined,
  CloudDownloadOutlined,
  DeleteOutlined,
  DownOutlined,
  EditOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  PictureOutlined,
  PlusOutlined,
  ReloadOutlined,
  RightOutlined,
  SearchOutlined,
  SettingOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons'
import type { UploadProps } from 'antd'
import client, { apiGet, apiPost } from '../../api/client'
import QuillEditor from '../../components/QuillEditor'
import type { EditorBridgeApi } from '../../components/ai-writing/editorBridge'
import { useAiWriting } from '../../components/ai-writing/useAiWriting'
import AiSelectionBubble from '../../components/ai-writing/AiSelectionBubble'
import AiWritingDrawer from '../../components/ai-writing/AiWritingDrawer'
import { pickTutorialArticleText } from '../../components/ai-writing/text'
import type { AiFillField, WritingOptions } from '../../components/ai-writing/types'
import { markdownToSafeHtml } from '../../components/editor/markdown'
import NetdiskLinksEditor from '../../components/NetdiskLinksEditor'
import EditPageLayout from '../../components/EditPageLayout'
import Section from '../../components/AdminSection'
import { useCategoryOptions } from '../../hooks/useCategoryOptions'
import { useUnsavedChangesGuard, clearFormTouched } from '../../hooks/useUnsavedChangesGuard'
import { TUTORIAL_CATEGORIES, TUTORIAL_STATUSES, EMPTY_NETDISK_LINK } from '../../constants'
import { getErrorMessage, isFormValidationError } from '../../utils/error'
import { isSafeUrl } from '../../utils/url'
import { uploadImage } from '../../utils/upload'
import type { NetdiskLink, Tutorial, TutorialStatus } from '../../types'

import { toast } from '../../utils/toast'
/** 管理端章节树节点（GET/POST /admin/tutorials/:id/nodes） */
interface AdminNode {
  id: number
  title: string
  content: string
  parent_id: number | null
  children?: AdminNode[]
}

/** 教程基本信息表单值（Form.useForm 泛型，消除 getFieldValue 断言） */
interface TutorialFormValues {
  title: string
  description: string
  introduction: string
  category: string
  status: TutorialStatus
  access_mode: 'free' | 'vip'
  points_price: number
  is_pinned: boolean
  is_recommended: boolean
  cover_image: string
  /** SEO 信息：仅写入页面 meta 标签供搜索引擎抓取，前台页面不展示 */
  seo_title: string
  seo_description: string
  seo_keywords: string
}

/** 封面图可选格式（与后端 /uploads/image 白名单一致） */
const IMAGE_ACCEPT = 'image/jpeg,image/png,image/webp,image/gif'

export default function TutorialsEdit() {
  const navigate = useNavigate()
  const { id } = useParams()
  const isEdit = !!id
  const [form] = Form.useForm<TutorialFormValues>()
  const [loading, setLoading] = useState(isEdit)
  const [saving, setSaving] = useState(false)
  const [tutorialLinks, setTutorialLinks] = useState<NetdiskLink[]>([{ ...EMPTY_NETDISK_LINK }])
  const categoryOptions = useCategoryOptions('tutorial', TUTORIAL_CATEGORIES)
  const [coverUploading, setCoverUploading] = useState(false)
  // 学习次数（详情接口返回）：仅用于「发布检查」区展示
  const [viewCount, setViewCount] = useState<number | null>(null)
  // 配套资源链接的初始快照（isFieldsTouched 覆盖不到表单外的 state，用 JSON 比较补充）
  const initialLinksJsonRef = useRef(JSON.stringify([{ ...EMPTY_NETDISK_LINK }]))

  // 章节管理状态
  const [nodeTree, setNodeTree] = useState<AdminNode[]>([])
  const [nodesLoading, setNodesLoading] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [nodeTitle, setNodeTitle] = useState('')
  const [nodeContent, setNodeContent] = useState('')
  const [nodeSaving, setNodeSaving] = useState(false)
  const [nodeOperating, setNodeOperating] = useState(false)
  const [expandedKeys, setExpandedKeys] = useState<Set<number>>(new Set())

  /** 在树中查找节点与其父节点列表 */
  const findNode = useCallback((nodes: AdminNode[], nodeId: number): { node: AdminNode; parent: AdminNode | null } | null => {
    const walk = (list: AdminNode[], parent: AdminNode | null): { node: AdminNode; parent: AdminNode | null } | null => {
      for (const n of list) {
        if (n.id === nodeId) return { node: n, parent }
        if (n.children?.length) {
          const found = walk(n.children, n)
          if (found) return found
        }
      }
      return null
    }
    return walk(nodes, null)
  }, [])

  const selected = useMemo(
    () => (selectedId != null ? findNode(nodeTree, selectedId)?.node ?? null : null),
    [nodeTree, selectedId, findNode],
  )

  /** 当前章节是否有未保存修改（标题用 trim 比较，避免自动保存后因首尾空格再次判定为脏） */
  const isDirty = useMemo(() => {
    if (!selected) return false
    return selected.title.trim() !== nodeTitle.trim() || selected.content !== nodeContent
  }, [selected, nodeTitle, nodeContent])

  // 章节编辑三态的调用时刻快照：直接进 useCallback 依赖会让回调随键入变化使整树 memo 失效，
  // memo 住旧闭包又会在保存时写回旧正文。用 ref 读最新值并保持回调引用稳定；
  // ⚠️ 必须在 useEffect 里同步（React 18 并发渲染下渲染期写 ref 会留下过期值）。
  const editorSnapshot = useRef({ selectedId, nodeTitle, nodeContent, isDirty })
  useEffect(() => {
    editorSnapshot.current = { selectedId, nodeTitle, nodeContent, isDirty }
  }, [selectedId, nodeTitle, nodeContent, isDirty])

  // —— AI 写作：只接入章节正文编辑器，课程介绍编辑器不接入 ——
  const bridgeRef = useRef<EditorBridgeApi | null>(null)
  const [aiOptions, setAiOptions] = useState<WritingOptions | null>(null)
  const [aiBubble, setAiBubble] = useState({ visible: false, top: 0, left: 0 })

  /** 教程全文：当前章节用未保存的 nodeContent 覆盖，其余章节用已加载内容 */
  const collectArticleText = useCallback(() => {
    const parts: string[] = []
    const walk = (nodes: AdminNode[]) => {
      for (const node of nodes) {
        parts.push(`# ${node.title}\n${node.id === selectedId ? nodeContent : node.content}`)
        if (node.children?.length) walk(node.children)
      }
    }
    walk(nodeTree)
    return parts.join('\n\n')
  }, [nodeTree, nodeContent, selectedId])

  /** 大纲需要全部层级章节标题，不能只看顶层 */
  const collectOutline = useCallback(() => {
    const titles: string[] = []
    const walk = (nodes: AdminNode[]) => {
      for (const node of nodes) {
        titles.push(node.title)
        if (node.children?.length) walk(node.children)
      }
    }
    walk(nodeTree)
    return titles
  }, [nodeTree])

  const ai = useAiWriting({
    bridgeRef,
    buildBaseRequest: (operation) => ({
      content_type: 'tutorial_node',
      content_id: selectedId,
      context: {
        title: form.getFieldValue('title') || '',
        category: form.getFieldValue('category') || '',
        chapter_title: nodeTitle,
        // 全文类操作发整本教程（客户端截断到 45k，避开后端 50k 422）；
        // 改写 / 润色 / 续写等只发当前章节。
        article_text: pickTutorialArticleText(operation, collectArticleText(), nodeContent),
        outline: collectOutline(),
      },
    }),
  })

  // 表单是泛型 Form.useForm<TutorialFormValues>，键名必须收窄为表单字段名（string 会被 TS 拒绝）
  const AI_FIELD_MAP: Record<AiFillField, keyof TutorialFormValues> = {
    title: 'title',
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

  // 章节切换不是页面卸载：hook 的卸载清理不会触发。除了隐藏浮条、取消在途请求，
  // 还必须丢掉旧桥接并清空已有回合——否则 A 章生成的「替换原文」会被应用到 B 章。
  // 用 layout effect 而不是 passive effect：新章节的 QuillEditor 在 passive effect 里
  // 通过 onReady 发布新桥接，而子组件的 passive effect 先于父组件执行；若在这里用
  // useEffect，刚发布的新桥接会被本 effect 立刻置空，AI 入口随即失效。
  useLayoutEffect(() => {
    setAiBubble((prev) => ({ ...prev, visible: false }))
    bridgeRef.current = null
    ai.reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId])

  // 未保存守卫：基本信息表单 + 当前章节正文 + 配套资源链接（isFieldsTouched() 无参 = 任意字段被编辑过）
  const confirmLeave = useUnsavedChangesGuard(
    () => form.isFieldsTouched() || isDirty || JSON.stringify(tutorialLinks) !== initialLinksJsonRef.current,
  )

  // 「发布检查」与侧栏预览要随输入即时更新（Form.Item 内的值读不到响应式变化）
  const watchedTitle = Form.useWatch('title', form)
  const watchedDescription = Form.useWatch('description', form)
  const watchedCategory = Form.useWatch('category', form)
  const watchedCover = Form.useWatch('cover_image', form)

  const nodeCount = useMemo(() => {
    const count = (list: AdminNode[]): number =>
      list.reduce((sum, n) => sum + 1 + count(n.children || []), 0)
    return count(nodeTree)
  }, [nodeTree])

  const linkCount = tutorialLinks.filter((l) => l.url.trim()).length
  const checklist = [
    { label: '教程标题', done: !!(watchedTitle || '').trim() },
    { label: '课程简介', done: !!(watchedDescription || '').trim() },
    { label: '分类', done: !!watchedCategory },
    { label: '封面图', done: !!watchedCover },
    { label: '章节内容', done: nodeCount > 0 },
  ]
  const doneCount = checklist.filter((c) => c.done).length

  const loadNodes = useCallback(async () => {
    if (!id) return
    setNodesLoading(true)
    try {
      const data = await apiGet<{ nodes: AdminNode[] }>(`/admin/tutorials/${id}/nodes`)
      setNodeTree(data.nodes)
      // 默认展开全部分支
      const keys = new Set<number>()
      const walk = (list: AdminNode[]) => {
        list.forEach((n) => {
          if (n.children?.length) {
            keys.add(n.id)
            walk(n.children)
          }
        })
      }
      walk(data.nodes)
      setExpandedKeys(keys)
      return data.nodes
    } catch (err) {
      toast.error(getErrorMessage(err, '加载章节失败'))
    } finally {
      setNodesLoading(false)
    }
  }, [id])

  useEffect(() => {
    const init = async () => {
      if (isEdit && id) {
        try {
          const d = await apiGet<Tutorial>(`/admin/tutorials/${id}`)
          const introduction = markdownToSafeHtml(d.introduction || '')
          // 新建成功跳转编辑页时组件复用，先重置清掉 isFieldsTouched 残留，避免未保存守卫误报
          form.resetFields()
          form.setFieldsValue({
            title: d.title || '',
            description: d.description || '',
            introduction,
            category: d.category || TUTORIAL_CATEGORIES[0],
            status: d.status || 'updating',
            access_mode: d.access_mode || 'free',
            points_price: d.points_price ?? 0,
            is_pinned: d.is_pinned ?? false,
            is_recommended: d.is_recommended ?? false,
            cover_image: d.cover_image || '',
            seo_title: d.seo_title || '',
            seo_description: d.seo_description || '',
            seo_keywords: d.seo_keywords || '',
          })
          // antd v6：程序化回填会把字段标记为 touched，需清除避免未保存守卫误报
          clearFormTouched(form, ['title', 'description', 'introduction', 'category', 'status', 'access_mode', 'points_price', 'is_pinned', 'is_recommended', 'cover_image', 'seo_title', 'seo_description', 'seo_keywords'])
          setViewCount(typeof d.view_count === 'number' ? d.view_count : null)
          setTutorialLinks(
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
          toast.error(getErrorMessage(err, '加载教程失败'))
          navigate('/tutorials')
        } finally {
          setLoading(false)
        }
        loadNodes()
      }
    }
    init()
  }, [id]) // eslint-disable-line react-hooks/exhaustive-deps

  // 章节操作

  /** 保存指定章节（手动/自动/切章前共用）：标题为空时用原标题兜底（后端拒绝空串），
   *  silent=true 自动保存成功时不弹 toast */
  const saveNode = useCallback(async (
    nodeId: number, title: string, content: string, opts?: { silent?: boolean },
  ): Promise<boolean> => {
    if (!id) return false
    const current = findNode(nodeTree, nodeId)?.node
    const finalTitle = title.trim() || current?.title || '未命名章节'
    try {
      await client.put(`/admin/tutorials/${id}/nodes/${nodeId}`, {
        title: finalTitle,
        content,
      })
      // 本地树同步（避免整树重拉）
      setNodeTree((prev) => {
        const update = (list: AdminNode[]): AdminNode[] =>
          list.map((n) =>
            n.id === nodeId
              ? { ...n, title: finalTitle, content }
              : { ...n, children: n.children ? update(n.children) : undefined },
          )
        return update(prev)
      })
      if (!opts?.silent) toast.success('章节已保存')
      return true
    } catch (err) {
      toast.error(getErrorMessage(err, '保存章节失败'))
      return false
    }
  }, [id, nodeTree, findNode])

  /** 切章前把当前未保存内容刷回后端；失败返回 false，调用方必须留在当前章节（否则正文被覆盖丢失） */
  const flushDirtyNode = useCallback(async (): Promise<boolean> => {
    const s = editorSnapshot.current
    if (!s.isDirty || s.selectedId == null) return true
    return saveNode(s.selectedId, s.nodeTitle, s.nodeContent, { silent: true })
  }, [saveNode])

  /** 选中章节并载入编辑器（有未保存修改时先自动保存，成功后再切换） */
  const selectNode = useCallback(async (node: AdminNode) => {
    if (node.id === editorSnapshot.current.selectedId) return
    if (!(await flushDirtyNode())) return
    setSelectedId(node.id)
    setNodeTitle(node.title)
    setNodeContent(node.content)
  }, [flushDirtyNode])

  /** 自动保存：停止编辑 3 秒后自动保存当前章节 */
  useEffect(() => {
    if (!selectedId || !isDirty) return
    const timer = setTimeout(() => {
      void saveNode(selectedId, nodeTitle, nodeContent, { silent: true })
    }, 3000)
    return () => clearTimeout(timer)
  }, [selectedId, isDirty, nodeTitle, nodeContent, saveNode])

  /** 手动保存当前章节（标题 + 正文） */
  const handleSaveNode = async () => {
    if (!id || selectedId == null) return
    if (!nodeTitle.trim()) {
      toast.warning('请填写章节标题')
      return
    }
    setNodeSaving(true)
    try {
      await saveNode(selectedId, nodeTitle, nodeContent)
    } finally {
      setNodeSaving(false)
    }
  }

  const handleAddNode = useCallback(async (parentId: number | null) => {
    if (!id) return
    // 创建后选中会切到新章节，先刷回当前章节的未保存正文避免被空内容覆盖
    if (!(await flushDirtyNode())) return
    setNodeOperating(true)
    try {
      const res = await apiPost<{ id: number }>(`/admin/tutorials/${id}/nodes`, {
        title: parentId == null ? '新章节' : '新小节',
        content: '',
        parent_id: parentId,
      })
      const fresh = await loadNodes()
      if (parentId != null) {
        setExpandedKeys((prev) => new Set(prev).add(parentId))
      }
      // 选中新章节（从重拉的树中取，避免引用旧树）
      const created = fresh ? findNode(fresh, res.id)?.node : null
      if (created) {
        setSelectedId(created.id)
        setNodeTitle(created.title)
        setNodeContent(created.content)
      }
      toast.success('章节已创建，请编辑标题与内容')
    } catch (err) {
      toast.error(getErrorMessage(err, '新增章节失败'))
    } finally {
      setNodeOperating(false)
    }
  }, [id, flushDirtyNode, loadNodes, findNode])

  /** 删除章节（含子孙） */
  const handleDeleteNode = useCallback(async (node: AdminNode) => {
    if (!id) return
    setNodeOperating(true)
    try {
      await client.delete(`/admin/tutorials/${id}/nodes/${node.id}`)
      // 被删章节含子孙时提示数量
      const countDescendants = (n: AdminNode): number => (n.children?.length || 0) + (n.children || []).reduce((s, c) => s + countDescendants(c), 0)
      const descendants = countDescendants(node)
      if (editorSnapshot.current.selectedId === node.id) {
        setSelectedId(null)
        setNodeTitle('')
        setNodeContent('')
      }
      await loadNodes()
      toast.success(descendants > 0 ? `已删除该章节及其 ${descendants} 个子章节` : '章节已删除')
    } catch (err) {
      toast.error(getErrorMessage(err, '删除章节失败'))
    } finally {
      setNodeOperating(false)
    }
  }, [id, loadNodes])

  /** 同级上移 / 下移 */
  const handleMoveNode = useCallback(async (node: AdminNode, direction: 'up' | 'down') => {
    if (!id) return
    setNodeOperating(true)
    try {
      await client.post(`/admin/tutorials/${id}/nodes/${node.id}/move`, { direction })
      await loadNodes()
    } catch (err) {
      toast.error(getErrorMessage(err, '移动章节失败'))
    } finally {
      setNodeOperating(false)
    }
  }, [id, loadNodes])

  const toggleExpanded = useCallback((nodeId: number) => {
    setExpandedKeys((prev) => {
      const next = new Set(prev)
      if (next.has(nodeId)) next.delete(nodeId)
      else next.add(nodeId)
      return next
    })
  }, [])

  // 基本信息保存

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

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      if (values.cover_image && !isSafeUrl(values.cover_image)) {
        toast.error('封面图 URL 必须以 http:// 或 https:// 开头')
        return
      }
      const invalidLinks = tutorialLinks.filter((l) => l.url.trim() && !isSafeUrl(l.url))
      if (invalidLinks.length > 0) {
        toast.error('网盘链接必须以 http:// 或 https:// 开头')
        return
      }
      setSaving(true)
      const payload = {
        ...values,
        // 免费模式积分价归零（与后端归一口径一致，避免表单残留脏值）
        points_price: values.access_mode === 'free' ? 0 : values.points_price ?? 0,
        // SEO 信息：trim 后清空，空串由后端按「清除」处理（前台回落默认标题/简介）
        seo_title: values.seo_title?.trim() || '',
        seo_description: values.seo_description?.trim() || '',
        seo_keywords: values.seo_keywords?.trim() || '',
        netdisk_links: tutorialLinks
          .filter((l) => l.url.trim())
          .map((l) => ({ platform: l.platform, url: l.url, extract_code: l.extract_code })),
      }
      if (isEdit && id) {
        await client.put(`/admin/tutorials/${id}`, payload)
        navigate('/tutorials')
      } else {
        // 新建：创建后跳到编辑页继续管理章节（正文按章存储，章节在下方「章节管理」维护）
        const res = await apiPost<{ id: number }>('/admin/tutorials', payload)
        toast.success('教程已创建，接下来请添加章节内容')
        navigate(`/tutorials/${res.id}/edit`)
      }
    } catch (err) {
      if (isFormValidationError(err)) return
      toast.error(getErrorMessage(err, '保存失败'))
    } finally {
      setSaving(false)
    }
  }

  // 章节树渲染

  const renderNodeTree = (nodes: AdminNode[], depth = 0) => {
    return nodes.map((node) => {
      const children = node.children || []
      const hasChildren = children.length > 0
      const isExpanded = expandedKeys.has(node.id)
      const isSelected = node.id === selectedId
      return (
        <div key={node.id}>
          <div
            className={`adm-node-row${isSelected ? ' adm-node-row--active' : ''}`}
            style={{ paddingLeft: 8 + depth * 16 }}
            onClick={() => selectNode(node)}
          >
            {hasChildren ? (
              <span
                className="adm-node-row__caret"
                onClick={(e) => { e.stopPropagation(); toggleExpanded(node.id) }}
              >
                {isExpanded ? <DownOutlined style={{ fontSize: 10 }} /> : <RightOutlined style={{ fontSize: 10 }} />}
              </span>
            ) : (
              <span className="adm-node-row__caret" />
            )}
            <div className="adm-node-row__title" title={node.title}>
              {node.title}
            </div>
            <span className="adm-node-row__actions" onClick={(e) => e.stopPropagation()}>
              <PlusOutlined
                className="adm-node-row__btn"
                title="添加子章节"
                style={{ color: '#6366f1' }}
                onClick={() => handleAddNode(node.id)}
              />
              <ArrowUpOutlined
                className="adm-node-row__btn"
                title="上移"
                style={{ color: '#94a3b8' }}
                onClick={() => handleMoveNode(node, 'up')}
              />
              <ArrowDownOutlined
                className="adm-node-row__btn"
                title="下移"
                style={{ color: '#94a3b8' }}
                onClick={() => handleMoveNode(node, 'down')}
              />
              <Popconfirm
                title="删除章节"
                description={hasChildren ? `将同时删除 ${children.length} 个直接子章节（含其子孙），确定删除？` : '确定删除该章节？'}
                onConfirm={() => handleDeleteNode(node)}
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
              >
                <DeleteOutlined className="adm-node-row__btn" title="删除章节" style={{ color: '#ef4444' }} />
              </Popconfirm>
            </span>
          </div>
          {isExpanded && hasChildren && renderNodeTree(children, depth + 1)}
        </div>
      )
    })
  }

  // 章节树按引用缓存：正文每键入一次都重渲染整个编辑页，而树里每行都带 Popconfirm/Tooltip
  // 重组件；依赖里不能出现 nodeTitle/nodeContent/isDirty（正是 editorSnapshot 稳住回调的原因），
  // Edit.node-switch.test.tsx 是这条 memo 的安全网
  const nodeTreeView = useMemo(
    () => renderNodeTree(nodeTree),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [nodeTree, expandedKeys, selectedId, selectNode, toggleExpanded, handleAddNode, handleMoveNode, handleDeleteNode],
  )

  return (
    <>
    <EditPageLayout
      title={isEdit ? '编辑教程' : '新建教程'}
      backLabel="返回教程管理"
      loading={loading}
      saving={saving}
      onCancel={() => confirmLeave(() => navigate('/tutorials'))}
      onSave={handleSave}
      saveText={isEdit ? '保存基本信息' : '创建教程'}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          title: '',
          description: '',
          introduction: '',
          category: TUTORIAL_CATEGORIES[0],
          status: 'updating',
          access_mode: 'free',
          points_price: 0,
          is_pinned: false,
          is_recommended: false,
          cover_image: '',
          seo_title: '',
          seo_description: '',
          seo_keywords: '',
        }}
      >
        <Form.Item name="cover_image" hidden><Input /></Form.Item>

        <Section
          icon={<InfoCircleOutlined />}
          title="基本信息"
          hint="标题、分类与简介会出现在前台教程列表卡片上；更新状态影响卡片上的「更新中 / 已完结」标记。"
        >
          <Form.Item label="教程标题" name="title" rules={[{ required: true, message: '请填写教程标题' }]}>
            <Input placeholder="请输入教程标题" maxLength={200} showCount />
          </Form.Item>

          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item label="分类" name="category" rules={[{ required: true, message: '请选择分类' }]}>
                <Select options={categoryOptions.map((c) => ({ value: c, label: c }))} placeholder="请选择分类" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item label="更新状态" name="status" extra="教程连载中选「更新中」，写完选「已完结」。">
                <Select options={[...TUTORIAL_STATUSES]} placeholder="请选择状态" />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item
            label="课程简介"
            name="description"
            rules={[{ required: true, message: '请填写课程简介' }]}
            extra="一句话简介，展示在课程卡片与介绍页顶部摘要。"
            style={{ marginBottom: 0 }}
          >
            <Input.TextArea rows={2} placeholder="一句话简介，如：从入门到精通，系统掌握 XXX 核心用法" maxLength={500} showCount />
          </Form.Item>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<PictureOutlined />}
          title="封面图"
          hint="教程列表卡片与介绍页顶部使用；未上传时前台回落到分类图标底色。支持 JPG / PNG / WebP / GIF（≤5MB）。"
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
          icon={<SettingOutlined />}
          title="发布与权限"
          hint="置顶与推荐只影响前台排序与角标；阅读权限决定谁能读——免费教程所有登录用户可读，会员教程仅认证会员可读。"
        >
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item
                label="置顶"
                name="is_pinned"
                valuePropName="checked"
                extra="置顶教程在前台列表最前展示"
              >
                <Switch checkedChildren="置顶" unCheckedChildren="正常" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item
                label="推荐"
                name="is_recommended"
                valuePropName="checked"
                extra="推荐教程卡片显示「推荐」角标并排序提前"
              >
                <Switch checkedChildren="推荐" unCheckedChildren="普通" />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item
                label="阅读权限"
                name="access_mode"
                extra="免费：登录后即可阅读；会员：知识星球认证会员可读，非会员可按积分价购买后永久阅读。"
              >
                <Radio.Group>
                  <Radio.Button value="free">免费</Radio.Button>
                  <Radio.Button value="vip">会员专享</Radio.Button>
                </Radio.Group>
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              {/* 仅会员模式可设置积分价；切换为免费时保存会自动归零 */}
              <Form.Item noStyle shouldUpdate={(prev, cur) => prev.access_mode !== cur.access_mode}>
                {() => {
                  const isVip = form.getFieldValue('access_mode') === 'vip'
                  return (
                    <Form.Item
                      label="积分购买价"
                      name="points_price"
                      extra={isVip ? '非会员可用该积分购买本教程，购买后永久可读；0 表示不支持积分购买。' : '仅「会员专享」教程支持设置积分购买价。'}
                    >
                      <InputNumber
                        min={0}
                        max={1000000}
                        precision={0}
                        suffix="积分"
                        style={{ width: '100%' }}
                        disabled={!isVip}
                        placeholder="如 100"
                      />
                    </Form.Item>
                  )
                }}
              </Form.Item>
            </Col>
          </Row>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<FileTextOutlined />}
          title="课程介绍"
          hint="展示在教程介绍页的「课程介绍」区域，支持图文混排、标题与列表；留空时前台展示课程简介。"
        >
          <Form.Item name="introduction" style={{ marginBottom: 0 }}>
            <QuillEditor placeholder="请输入课程介绍内容..." height={360} showToc={false} />
          </Form.Item>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<UnorderedListOutlined />}
          title="章节管理"
          hint={
            isEdit
              ? `正文按章存储，前台阅读页左侧的章节树即此结构；章内小标题会自动生成右侧大纲。当前共 ${nodeCount} 个章节。`
              : '保存基本信息创建教程后，即可在此添加章节内容。'
          }
        >
          {!isEdit ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="保存基本信息创建教程后，即可在此添加章节内容"
            />
          ) : (
            <div className="adm-nodes">
              {/* 左：章节树 */}
              <div className="adm-nodes__tree">
                <Button
                  block
                  type="dashed"
                  icon={<PlusOutlined />}
                  loading={nodeOperating}
                  onClick={() => handleAddNode(null)}
                  style={{ marginBottom: 12 }}
                >
                  新增章节
                </Button>
                {/* 高度与右侧编辑区对齐（标题行 + 编辑器 520px），章节多时区域内滚动 */}
                <div className="adm-nodes__list">
                  {nodesLoading && nodeTree.length === 0 ? (
                    <div style={{ textAlign: 'center', color: '#999', padding: '24px 0' }}>加载章节中...</div>
                  ) : nodeTree.length === 0 ? (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无章节，点击上方按钮添加" />
                  ) : (
                    nodeTreeView
                  )}
                </div>
              </div>

              {/* 右：单章编辑器 */}
              <div className="adm-nodes__editor">
                {selected ? (
                  <>
                    {/* 与下方编辑器行对齐：标题输入占编辑器宽度，保存按钮占目录宽度（200px / 间距 16px，同 QuillEditor 布局） */}
                    <div style={{ display: 'flex', gap: 16, marginBottom: 12 }}>
                      <Input
                        value={nodeTitle}
                        onChange={(e) => setNodeTitle(e.target.value)}
                        placeholder="章节标题"
                        maxLength={200}
                        size="large"
                        style={{ flex: 1, minWidth: 0 }}
                      />
                      <Button
                        type="primary"
                        icon={<EditOutlined />}
                        loading={nodeSaving}
                        onClick={handleSaveNode}
                        disabled={!isDirty}
                        style={{ width: 200, flexShrink: 0 }}
                      >
                        {isDirty ? '保存本章（有修改）' : '已保存'}
                      </Button>
                    </div>
                    {/* key 切章重挂载：避免 Quill 受控切换大内容时的光标/内容残留问题，
                        同时让 AI 桥接随编辑器重建，不残留上一章的选区 */}
                    <QuillEditor
                      key={selected.id}
                      value={nodeContent}
                      onChange={(v) => setNodeContent(v)}
                      placeholder="请输入本章节内容...（章内使用标题可生成右侧大纲）"
                      height={520}
                      aiEnabled={!!aiOptions?.enabled}
                      onAiOpen={() => ai.setOpen(true)}
                      onReady={(api) => { bridgeRef.current = api }}
                      onSelectionChange={handleSelection}
                    />
                  </>
                ) : (
                  <div className="adm-nodes__placeholder">
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择左侧章节进行编辑，或新增一个章节" />
                  </div>
                )}
              </div>
            </div>
          )}
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<CloudDownloadOutlined />}
          title="配套资源"
          hint="教程附带的网盘链接或官方直链；留空则前台不显示「配套资源」区域。"
        >
          <Form.Item style={{ marginBottom: 0 }}>
            <NetdiskLinksEditor links={tutorialLinks} onChange={setTutorialLinks} />
          </Form.Item>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<SearchOutlined />}
          title="SEO 信息（选填）"
          hint="仅用于搜索引擎收录展示（教程介绍页与章节阅读页的标题、摘要与关键词），前台页面不显示；未填写时自动使用教程标题与简介。"
        >
          <Form.Item
            label="SEO 标题"
            name="seo_title"
            extra="搜索引擎结果中显示的页面标题，建议 30 字以内（约 60 字符）；章节页格式为「章节名 - SEO标题」。"
          >
            <Input maxLength={200} showCount placeholder="如：Python 从入门到实战｜零基础系统课程" />
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
              placeholder="如：本教程系统讲解核心概念、实操步骤与项目案例，适合零基础入门与进阶。"
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
              placeholder="如：Python教程, 编程入门, 前端开发, 项目实战"
            />
          </Form.Item>
        </Section>

        <div className="adm-divider" />

        <Section
          icon={<CheckCircleOutlined />}
          title="发布检查"
          hint="保存前确认以下要素是否齐全；缺项不影响保存，但会让前台卡片与阅读页显示不完整。"
        >
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
            章节 <strong>{nodeCount}</strong> 个
            <span className="adm-sep"> · </span>
            学习 <strong>{viewCount == null ? '—' : viewCount.toLocaleString('zh-CN')}</strong> 次
            <span className="adm-sep"> · </span>
            配套资源 <strong>{linkCount}</strong> 条
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
