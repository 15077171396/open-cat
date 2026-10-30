import { useEffect, useRef, useState } from 'react'
import { Button, Checkbox, Input, Modal, Popover, Space, Tag } from 'antd'
import { CloudDownloadOutlined, QuestionCircleOutlined, TranslationOutlined } from '@ant-design/icons'
import { apiGet, apiPost } from '../api/client'
import { getErrorMessage } from '../utils/error'
import { markdownToHtml } from './editor/markdown'

import { toast } from '../utils/toast'
/** 「从第三方导入正文」的帮助说明（点标题旁的 ⓘ 弹出，避免常驻长文案挤占表单） */
function ThirdPartyHelp() {
  const groupTitle = { fontWeight: 600, color: '#1f1f1f', margin: '10px 0 4px' } as const
  const item = { color: 'var(--app-text-2)', lineHeight: 1.9 } as const
  return (
    <div style={{ width: 430, maxHeight: 400, overflowY: 'auto', fontSize: 12 }}>
      <div style={{ ...groupTitle, marginTop: 0 }}>支持的平台</div>
      <div style={item}>
        <div>· 仓库 / 模型：GitHub、Gitee、GitCode（含指定分支或文件）、ModelScope、HuggingFace（模型与数据集）</div>
        <div>· 国外文章：X (Twitter)、Reddit、Medium、Dev.to、Stack Overflow、Hacker News</div>
        <div>· 国内文章：知乎、掘金、CSDN、博客园、简书、微信公众号、SegmentFault、开源中国、V2EX、少数派、人人都是产品经理</div>
      </div>

      <div style={groupTitle}>拉取后会发生什么</div>
      <div style={item}>
        <div>· 正文填入下方编辑器，可继续编辑</div>
        <div>· 标题 / 简介 / 标签为空时自动填充，标签合并去重</div>
        <div>· 文章类正文末尾自动附「来源」出处行</div>
        <div>· 仓库类优先取中文版（README.zh-CN.md 等）</div>
      </div>

      <div style={groupTitle}>翻译与网络</div>
      <div style={item}>
        <div>· 没有中文版时，可点「翻译为中文」用 AI 翻译当前正文（代码与图片不受影响）</div>
        <div>· 国外平台若服务器无法直连，需管理员在 .env 配置 ARTICLE_PROXY 出站代理</div>
      </div>
    </div>
  )
}

/** 平台类型：repo 仓库/模型类（支持附带文件列表），article 社区文章类 */
type PlatformKind = 'repo' | 'article'

/** 支持的平台（前端轻量识别，仅用于输入提示；权威分发在后端按域名判断） */
const PLATFORMS: { hosts: string[]; label: string; kind: PlatformKind }[] = [
  // 仓库 / 模型类
  { hosts: ['github.com'], label: 'GitHub', kind: 'repo' },
  { hosts: ['gitee.com'], label: 'Gitee', kind: 'repo' },
  { hosts: ['gitcode.com'], label: 'GitCode', kind: 'repo' },
  { hosts: ['modelscope.cn'], label: 'ModelScope', kind: 'repo' },
  { hosts: ['huggingface.co', 'hf-mirror.com'], label: 'HuggingFace', kind: 'repo' },
  // 国外社区文章
  { hosts: ['x.com', 'twitter.com'], label: 'X (Twitter)', kind: 'article' },
  { hosts: ['reddit.com'], label: 'Reddit', kind: 'article' },
  { hosts: ['medium.com'], label: 'Medium', kind: 'article' },
  { hosts: ['dev.to'], label: 'Dev.to', kind: 'article' },
  { hosts: ['stackoverflow.com', 'stackexchange.com'], label: 'Stack Overflow', kind: 'article' },
  { hosts: ['news.ycombinator.com'], label: 'Hacker News', kind: 'article' },
  // 国内社区文章
  { hosts: ['zhihu.com'], label: '知乎', kind: 'article' },
  { hosts: ['juejin.cn'], label: '掘金', kind: 'article' },
  { hosts: ['csdn.net'], label: 'CSDN', kind: 'article' },
  { hosts: ['cnblogs.com'], label: '博客园', kind: 'article' },
  { hosts: ['jianshu.com'], label: '简书', kind: 'article' },
  { hosts: ['mp.weixin.qq.com'], label: '微信公众号', kind: 'article' },
  { hosts: ['segmentfault.com'], label: 'SegmentFault', kind: 'article' },
  { hosts: ['oschina.net'], label: '开源中国', kind: 'article' },
  { hosts: ['v2ex.com'], label: 'V2EX', kind: 'article' },
  { hosts: ['sspai.com'], label: '少数派', kind: 'article' },
  { hosts: ['woshipm.com'], label: '人人都是产品经理', kind: 'article' },
]

/** 拉取接口返回结构（与后端 third_party_fetch 统一结构对齐） */
interface FetchResourceResult {
  content: string
  title: string
  platform: string
  /** 平台中文名（出处展示，如「掘金」） */
  platform_label?: string
  /** 作者 / 用户名（出处展示） */
  author?: string
  /** 自动生成的一句话简介 */
  summary?: string
  /** 建议标签 */
  tags?: string[]
  /** 原文地址 */
  source_url?: string
  language?: string
  images_transferred?: number
  /** 正文待转存图片总数：与 images_transferred 相减即失败数 */
  images_total?: number
  files_included?: boolean
}

/** 拉取到的元信息（宿主页按自身表单字段决定回填哪些） */
interface FetchMeta {
  /** 文章 / 资源标题 */
  title: string
  /** 自动简介（正文首段） */
  summary: string
  /** 建议标签 */
  tags: string[]
  /** 作者 / 用户名 */
  author: string
  /** 平台中文名 */
  platformLabel: string
  /** 原文地址 */
  sourceUrl: string
}

interface ThirdPartyImportProps {
  /** 获取当前正文（覆盖确认判断 + 翻译输入），由宿主页从表单读取 */
  getContent: () => string
  /** 回填正文（安全 HTML），由宿主页写回表单 */
  onContentChange: (html: string) => void
  /**
   * 回填标题 / 简介 / 标签等元信息（可选）。宿主页在此把 meta 写入自身表单字段，
   * 并返回实际填充的字段名列表（如 ['标题', '简介', '标签']）用于提示。
   */
  onFillMeta?: (meta: FetchMeta) => string[]
}

/**
 * 计算元信息回填补丁：标题/简介仅在目标字段为空时写入（拉取是替换正文语义，
 * 直接覆盖会丢用户手填内容）；标签做合并去重而非替换。
 */
export function buildMetaPatch(
  current: { title: string; summary: string; tags: string[] },
  meta: FetchMeta,
): { title?: string; summary?: string; tags?: string[] } {
  const patch: { title?: string; summary?: string; tags?: string[] } = {}
  if (!current.title.trim() && meta.title.trim()) patch.title = meta.title.trim()
  if (!current.summary.trim() && meta.summary.trim()) patch.summary = meta.summary.trim()
  const merged = Array.from(
    new Set([...current.tags, ...meta.tags].map((t) => t.trim()).filter(Boolean)),
  )
  if (merged.length > current.tags.length) patch.tags = merged
  return patch
}

/** 识别资源地址所属平台（识别不到返回 null，提示由后端报错兜底） */
function detectPlatform(url: string): { label: string; kind: PlatformKind } | null {
  const text = url.trim()
  if (!text) return null
  const withProto = /^https?:\/\//i.test(text) ? text : `https://${text}`
  try {
    const host = new URL(withProto).hostname.toLowerCase().replace(/^www\./, '')
    // 精确匹配或子域后缀匹配（zhuanlan.zhihu.com / pub.medium.com 等）
    const matched = PLATFORMS.find((p) =>
      p.hosts.some((h) => host === h || host.endsWith(`.${h}`)),
    )
    return matched ? { label: matched.label, kind: matched.kind } : null
  } catch {
    return null
  }
}

/** 翻译轮询上限（ms）：后端任务异常挂起时避免前端无限轮询 */
const TRANSLATE_POLL_TIMEOUT_MS = 5 * 60 * 1000

/**
 * 第三方资料导入组件（各内容编辑页共用）：粘贴地址 → 识别平台 → 拉取 README/模型卡/文章
 * → 转安全 HTML 回填正文；非中文正文可 AI 翻译（异步任务 + 2 秒轮询，规避 CDN 100s 超时）。
 * 通过 getContent/onContentChange/onFillMeta 与宿主页解耦。
 */
export default function ThirdPartyImport({
  getContent,
  onContentChange,
  onFillMeta,
}: ThirdPartyImportProps) {
  const [resourceUrl, setResourceUrl] = useState('')
  const [fetching, setFetching] = useState(false)
  const [translating, setTranslating] = useState(false)
  const [includeFiles, setIncludeFiles] = useState(false)
  // 最近一次拉取的出处信息（平台 / 作者 / 原文地址），用于结果提示条展示
  const [lastSource, setLastSource] = useState<FetchMeta | null>(null)
  const platform = detectPlatform(resourceUrl)
  // 文章类平台没有文件列表概念，禁用并忽略该选项（仓库/模型类才生效）
  const filesSupported = platform?.kind !== 'article'
  // 组件卸载标志：翻译轮询为长任务，卸载后立即停止后续请求与 setState
  const cancelledRef = useRef(false)
  useEffect(() => () => { cancelledRef.current = true }, [])

  /** 拉取第三方资料，转为安全 HTML 后填入正文编辑器 */
  const handleFetch = async () => {
    const url = resourceUrl.trim()
    if (!url) {
      toast.warning('请先填写资源地址')
      return
    }
    const doFetch = async () => {
      setFetching(true)
      try {
        const data = await apiGet<FetchResourceResult>('/admin/fetch-resource', {
          params: { url, include_files: filesSupported && includeFiles },
        })
        onContentChange(markdownToHtml(data.content))
        const meta: FetchMeta = {
          title: data.title || '',
          summary: data.summary || '',
          tags: data.tags || [],
          author: data.author || '',
          platformLabel: data.platform_label || data.platform,
          sourceUrl: data.source_url || url,
        }
        setLastSource(meta)
        const filled = onFillMeta?.(meta) ?? []
        const imgNote = data.images_transferred
          ? `，${data.images_transferred} 张图片已转存到 COS`
          : ''
        const fillNote = filled.length ? `，已自动填充${filled.join(' / ')}` : ''
        toast.success(
          `已从 ${meta.platformLabel} 拉取《${meta.title}》${imgNote}${fillNote}，可继续编辑`,
        )
        // 图片部分转存失败必须显式提示：失败项保留的是对方站点的**签名外链**，
        // 通常 1~3 天后失效，届时正文会突然出现裂图，事后很难追因
        const imagesFailed = Math.max(
          0,
          (data.images_total ?? data.images_transferred ?? 0) - (data.images_transferred ?? 0),
        )
        if (imagesFailed > 0) {
          toast.warning(
            `有 ${imagesFailed} 张图片未能转存到 COS，正文中保留的是原站临时链接（数天后会失效），` +
              '建议发布前在编辑器里确认这些配图，必要时重新导入或手动补图',
            8,
          )
        }
        if (data.language !== 'zh') {
          toast.info('该资源暂无中文版内容，可点击「翻译为中文」一键翻译', 5)
        }
      } catch (err) {
        toast.error(getErrorMessage(err, '拉取资料失败'))
      } finally {
        setFetching(false)
      }
    }
    // 已有正文时先确认，避免误覆盖手动编写的内容
    const current = getContent()
    if (current.trim()) {
      Modal.confirm({
        title: '替换正文内容',
        content: '当前正文内容将被第三方资料替换（标题 / 简介 / 标签仅在为空时自动填充），是否继续？',
        okText: '替换',
        cancelText: '取消',
        onOk: doFetch,
      })
    } else {
      await doFetch()
    }
  }

  /** AI 翻译当前正文为简体中文（第三方资料导入后一键中文化，长文约需 1-2 分钟） */
  const handleTranslate = async () => {
    const current = getContent().trim()
    if (!current) {
      toast.warning('正文内容为空，请先拉取资料或编写正文')
      return
    }
    Modal.confirm({
      title: '翻译为中文',
      content: '将使用 AI 把当前正文翻译为简体中文并替换现有内容（代码与图片保留原样），是否继续？',
      okText: '开始翻译',
      cancelText: '取消',
      onOk: async () => {
        setTranslating(true)
        const progressKey = 'translate-progress'
        try {
          // 长文翻译改为异步任务 + 轮询：同步等待会被 CDN（如 Cloudflare 100s）掐断返回 524
          const start = await apiPost<{ task_id: string }>('/admin/translate-content', {
            content: current,
            format: 'html',
          })
          toast.loading({ content: '翻译任务已提交，正在翻译…', key: progressKey, duration: 0 })
          let result = ''
          const deadline = Date.now() + TRANSLATE_POLL_TIMEOUT_MS
          for (;;) {
            await new Promise((r) => setTimeout(r, 2000))
            if (cancelledRef.current) return // 组件已卸载：停止轮询，不再 setState
            if (Date.now() > deadline) {
              throw new Error('翻译超时，请稍后重试或联系管理员查看任务状态')
            }
            const st = await apiGet<{
              status: string
              done: number
              total: number
              content: string | null
              error: string | null
            }>(`/admin/translate-content/${start.task_id}`)
            if (cancelledRef.current) return
            if (st.status === 'completed') {
              result = st.content || ''
              break
            }
            if (st.status === 'failed') {
              throw new Error(st.error || '翻译失败，请稍后重试')
            }
            if (st.total > 0) {
              toast.loading({
                content: `正在翻译… ${st.done}/${st.total} 块`,
                key: progressKey,
                duration: 0,
              })
            }
          }
          onContentChange(result)
          toast.success({ content: '翻译完成，请检查后保存', key: progressKey })
        } catch (err) {
          toast.error({ content: getErrorMessage(err, '翻译失败，请稍后重试'), key: progressKey, duration: 4 })
        } finally {
          // 仅卸载中断路径需销毁残留 loading；成功/失败路径已用同 key 提示替换，再 destroy 会误杀
          if (cancelledRef.current) toast.destroy(progressKey)
          if (!cancelledRef.current) setTranslating(false)
        }
      },
    })
  }

  return (
    <div style={{ marginBottom: 24 }}>
      <div
        style={{
          fontSize: 14,
          fontWeight: 600,
          color: '#1f1f1f',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          marginBottom: 8,
        }}
      >
        <CloudDownloadOutlined />
        从第三方导入正文（可选）
        <Popover
          title="第三方导入说明"
          trigger="click"
          placement="rightTop"
          content={<ThirdPartyHelp />}
        >
          <QuestionCircleOutlined
            style={{ color: 'var(--app-text-2)', cursor: 'pointer', fontSize: 13 }}
            aria-label="查看第三方导入说明"
          />
        </Popover>
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <Space.Compact style={{ flex: 1, minWidth: 0 }}>
          <Input
            placeholder="粘贴资源地址，如 https://github.com/owner/repo、https://x.com/user/status/123、https://juejin.cn/post/123"
            value={resourceUrl}
            onChange={(e) => setResourceUrl(e.target.value)}
            onPressEnter={handleFetch}
            allowClear
          />
          <Tag
            style={{
              display: 'flex',
              alignItems: 'center',
              marginInlineEnd: 0,
              paddingInline: 12,
              whiteSpace: 'nowrap',
              background: 'var(--app-subtle)',
              borderInlineStartWidth: 0,
            }}
          >
            {platform?.label ?? '自动识别'}
          </Tag>
        </Space.Compact>
        <Checkbox
          checked={filesSupported && includeFiles}
          disabled={!filesSupported}
          onChange={(e) => setIncludeFiles(e.target.checked)}
          style={{ flexShrink: 0, whiteSpace: 'nowrap' }}
        >
          附带文件列表
        </Checkbox>
        <Button
          type="primary"
          loading={fetching}
          onClick={handleFetch}
          style={{ flexShrink: 0 }}
        >
          拉取资料
        </Button>
        <Button
          icon={<TranslationOutlined />}
          loading={translating}
          onClick={handleTranslate}
          style={{ flexShrink: 0 }}
        >
          翻译为中文
        </Button>
      </div>
      {/* 拉取结果出处提示条：平台 · 作者 + 原文链接（正文末尾已附「来源」行） */}
      {lastSource && (
        <div
          style={{
            marginTop: 8,
            padding: '6px 10px',
            background: 'var(--app-success-bg)',
            border: '1px solid #b7eb8f',
            borderRadius: "var(--app-radius)",
            fontSize: 12,
            color: '#389e0d',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            flexWrap: 'wrap',
          }}
        >
          <span>
            出处：{lastSource.platformLabel}
            {lastSource.author ? ` · ${lastSource.author}` : ''}
          </span>
          {lastSource.sourceUrl && (
            <a href={lastSource.sourceUrl} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>
              查看原文
            </a>
          )}
          <span style={{ color: 'var(--app-text-2)' }}>
            （已随正文写入「来源」行，可在编辑器中调整）
          </span>
        </div>
      )}
    </div>
  )
}
