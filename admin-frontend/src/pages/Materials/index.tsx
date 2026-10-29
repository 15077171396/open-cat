import {
  AppstoreOutlined, AudioOutlined, ExportOutlined, EyeOutlined, FileMarkdownOutlined,
  FileOutlined, FileTextOutlined, FileZipOutlined, LinkOutlined, PictureOutlined,
  ReloadOutlined, SearchOutlined, VideoCameraOutlined,
} from '@ant-design/icons'
import { Button, Input, Modal, Pagination, Select, Spin, Tooltip } from 'antd'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { copyText } from '../../utils/clipboard'
import { formatDateTime } from '../../utils/datetime'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'
import {
  fetchMaterials, fetchMaterialStats, touchMaterial,
  type MaterialItem, type MaterialKind, type MaterialStats,
} from './api'
import PageHeader from '../../components/ui/PageHeader'

const PAGE_SIZE = 24

/** 类型筛选做成 chips：比下拉更直观，且顺带展示各类型的数量。 */
const KIND_CHIPS: Array<{ value: string; label: string; icon: ReactNode }> = [
  { value: '', label: '全部素材', icon: <AppstoreOutlined /> },
  { value: 'image', label: '图片', icon: <PictureOutlined /> },
  { value: 'video', label: '视频', icon: <VideoCameraOutlined /> },
  { value: 'audio', label: '音频', icon: <AudioOutlined /> },
  { value: 'document', label: '文档', icon: <FileTextOutlined /> },
  { value: 'archive', label: '压缩包', icon: <FileZipOutlined /> },
  { value: 'other', label: '其它', icon: <FileOutlined /> },
]

const KIND_LABELS: Record<string, string> = Object.fromEntries(
  KIND_CHIPS.filter((item) => item.value).map((item) => [item.value, item.label]),
)

// 素材库的来源筛选是「历史数据」筛选器，必须能覆盖已停用的来源（如 bing），
// 所以这里保留一份静态清单，不从后端 options 派生。
const SOURCE_OPTIONS = [
  { value: '', label: '全部来源' },
  { value: 'upload', label: '本站上传' },
  { value: 'ai', label: 'AI 生成' },
  { value: 'pexels', label: 'Pexels' },
  { value: 'pixabay', label: 'Pixabay' },
  { value: 'giphy', label: 'GIPHY' },
  { value: 'tenor', label: 'Tenor 兼容' },
  { value: 'so360', label: '360 图片' },
  { value: 'baidu', label: '百度图片' },
  { value: 'sogou', label: '搜狗图片' },
  { value: 'duitang', label: '堆糖' },
  { value: 'doutupk', label: '斗图啦' },
  { value: 'adoutu', label: '爱斗图' },
  { value: 'memecub', label: 'memecub' },
  { value: 'emoji', label: 'EmojiPackage' },
]

const SOURCE_LABELS: Record<string, string> = {
  ...Object.fromEntries(
    SOURCE_OPTIONS.filter((item) => item.value).map((item) => [item.value, item.label]),
  ),
  // 2026-10-29 起不再抓取 Bing 图片；历史素材仍按原名字展示，避免显示成裸 source 值。
  bing: 'Bing 图片（已停用）',
}

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function extOf(item: MaterialItem): string {
  const name = item.filename || item.cos_url.split('?')[0]
  const ext = name.includes('.') ? name.split('.').pop() || '' : ''
  return ext.toUpperCase().slice(0, 6)
}

function kindIcon(kind: MaterialKind) {
  if (kind === 'image') return <PictureOutlined />
  if (kind === 'video') return <VideoCameraOutlined />
  if (kind === 'audio') return <AudioOutlined />
  if (kind === 'archive') return <FileZipOutlined />
  if (kind === 'document') return <FileTextOutlined />
  return <FileOutlined />
}

export default function MaterialsPage() {
  const [items, setItems] = useState<MaterialItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [kind, setKind] = useState('')
  const [source, setSource] = useState('')
  const [stats, setStats] = useState<MaterialStats | null>(null)
  const [preview, setPreview] = useState<MaterialItem | null>(null)
  const [previewBroken, setPreviewBroken] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchMaterials({
        keyword, kind, source, page, pageSize: PAGE_SIZE,
      })
      setItems(data.items)
      setTotal(data.total)
    } catch (err) {
      toast.error(getErrorMessage(err, '素材库加载失败'))
      setItems([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [keyword, kind, source, page])

  useEffect(() => {
    void load()
  }, [load])

  const loadStats = useCallback(async () => {
    try {
      setStats(await fetchMaterialStats())
    } catch {
      setStats(null)
    }
  }, [])

  useEffect(() => {
    void loadStats()
  }, [loadStats])

  const applyKeyword = () => {
    setKeyword(keywordInput.trim())
    setPage(1)
  }

  const refresh = () => {
    void load()
    void loadStats()
  }

  const clearFilters = () => {
    setKeywordInput('')
    setKeyword('')
    setKind('')
    setSource('')
    setPage(1)
  }

  const openPreview = (item: MaterialItem) => {
    setPreviewBroken(false)
    setPreview(item)
  }

  const markUsed = (item: MaterialItem) => {
    void touchMaterial(item.id)
      .then(() => {
        setItems((prev) => prev.map((row) => (
          row.id === item.id
            ? { ...row, usage_count: row.usage_count + 1, last_used_at: new Date().toISOString() }
            : row
        )))
      })
      .catch(() => {})
  }

  const copy = async (item: MaterialItem, text: string, success: string) => {
    if (!(await copyText(text))) {
      toast.error('复制失败，请手动复制')
      return
    }
    toast.success(success)
    markUsed(item)
  }

  const copyUrl = (item: MaterialItem) => void copy(item, item.cos_url, '素材地址已复制')
  const copyMarkdown = (item: MaterialItem) => {
    const alt = item.alt || item.title || item.filename || '图片'
    void copy(item, `![${alt}](${item.cos_url})`, 'Markdown 已复制')
  }

  const chips = useMemo(() => {
    const kinds = stats?.kinds || {}
    return KIND_CHIPS.map((chip) => ({
      ...chip,
      count: chip.value ? (kinds[chip.value] ?? 0) : (stats?.total ?? total),
    }))
  }, [stats, total])

  const hasFilter = Boolean(keyword || kind || source)

  return (
    <div className="adm-page mat-page">
      <PageHeader
        icon={<PictureOutlined />}
        title="素材库"
        subtitle="上传、图库导入与 AI 生成的素材都在这里；复制链接即可在编辑器复用。"
        actions={
          <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>
            刷新
          </Button>
        }
      />

      <div className="mat-chips" role="group" aria-label="素材类型筛选">
        {chips.map((chip) => (
          <button
            type="button"
            key={chip.value || 'all'}
            className={`mat-chip${kind === chip.value ? ' is-active' : ''}`}
            onClick={() => { setKind(chip.value); setPage(1) }}
            aria-pressed={kind === chip.value}
          >
            <span className="mat-chip__icon">{chip.icon}</span>
            <span className="mat-chip__label">{chip.label}</span>
            <span className="mat-chip__count">{chip.count}</span>
          </button>
        ))}
      </div>

      <div className="mat-toolbar">
        <Input
          allowClear
          value={keywordInput}
          onChange={(event) => setKeywordInput(event.target.value)}
          onPressEnter={applyKeyword}
          prefix={<SearchOutlined />}
          placeholder="搜索标题、文件名、Alt 或标签"
          aria-label="搜索素材"
          className="mat-toolbar__search"
        />
        <Select
          value={source}
          onChange={(value) => { setSource(value); setPage(1) }}
          options={SOURCE_OPTIONS}
          aria-label="素材来源"
          className="mat-toolbar__source"
        />
        <Button type="primary" onClick={applyKeyword}>搜索</Button>
        <span className="mat-toolbar__total">
          {hasFilter ? `筛选出 ${total} 个` : `共 ${total} 个素材`}
        </span>
      </div>

      <Spin spinning={loading}>
        {items.length === 0 && !loading ? (
          <div className="mat-empty">
            <span className="mat-empty__icon">
              {hasFilter ? <SearchOutlined /> : <PictureOutlined />}
            </span>
            <div className="mat-empty__title">
              {hasFilter ? '没有匹配的素材' : '素材库还是空的'}
            </div>
            <div className="mat-empty__sub">
              {hasFilter
                ? '换个关键词或筛选条件试试'
                : '在编辑器里上传图片、从图库导入，或让 AI 生成配图，都会自动收进这里'}
            </div>
            {hasFilter ? <Button onClick={clearFilters}>清除筛选</Button> : null}
          </div>
        ) : (
          <div className="mat-grid">
            {items.map((item) => {
              const label = item.title || item.filename || `素材 ${item.id}`
              const dimensions = item.width && item.height ? `${item.width}×${item.height}` : ''
              return (
                <article className="mat-card" key={item.id}>
                  <div className="mat-card__media">
                    <button
                      type="button"
                      className="mat-card__preview"
                      onClick={() => openPreview(item)}
                      aria-label={`预览 ${label}`}
                    >
                      {item.kind === 'image' ? (
                        <img
                          src={item.thumb_url || item.cos_url}
                          alt={item.alt || item.title || ''}
                          loading="lazy"
                        />
                      ) : (
                        <span className="mat-card__file">
                          <span className="mat-card__file-icon">{kindIcon(item.kind)}</span>
                          <span className="mat-card__ext">{extOf(item) || 'FILE'}</span>
                        </span>
                      )}
                    </button>

                    <span className="mat-card__badge">
                      {item.kind === 'image' ? (extOf(item) || 'IMG') : (KIND_LABELS[item.kind] || item.kind)}
                    </span>

                    <div className="mat-card__overlay">
                      <Tooltip title="复制地址">
                        <Button
                          size="small"
                          aria-label="复制地址"
                          icon={<LinkOutlined />}
                          onClick={() => copyUrl(item)}
                        />
                      </Tooltip>
                      {item.kind === 'image' ? (
                        <Tooltip title="复制 Markdown">
                          <Button
                            size="small"
                            aria-label="复制 Markdown"
                            icon={<FileMarkdownOutlined />}
                            onClick={() => copyMarkdown(item)}
                          />
                        </Tooltip>
                      ) : null}
                      <Tooltip title="打开原文件">
                        <Button
                          size="small"
                          aria-label="打开原文件"
                          icon={<ExportOutlined />}
                          href={item.cos_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={() => markUsed(item)}
                        />
                      </Tooltip>
                      <Tooltip title="查看详情">
                        <Button
                          size="small"
                          aria-label={`查看详情 ${label}`}
                          icon={<EyeOutlined />}
                          onClick={() => openPreview(item)}
                        />
                      </Tooltip>
                    </div>
                  </div>

                  <div className="mat-card__body">
                    <div className="mat-card__title" title={label}>{label}</div>
                    <div className="mat-card__meta">
                      <span className="mat-card__source">{SOURCE_LABELS[item.source] || item.source}</span>
                      <span className="mat-card__dot" aria-hidden="true">·</span>
                      <span>{formatBytes(item.size_bytes)}</span>
                      {dimensions ? (
                        <>
                          <span className="mat-card__dot" aria-hidden="true">·</span>
                          <span>{dimensions}</span>
                        </>
                      ) : null}
                    </div>
                    <div className="mat-card__foot">
                      <span>使用 {item.usage_count} 次</span>
                      {item.last_used_at ? (
                        <span className="mat-card__time">{formatDateTime(item.last_used_at)}</span>
                      ) : null}
                    </div>
                  </div>
                </article>
              )
            })}
          </div>
        )}
      </Spin>

      {total > PAGE_SIZE ? (
        <div className="mat-pagination">
          <Pagination
            current={page}
            pageSize={PAGE_SIZE}
            total={total}
            showSizeChanger={false}
            onChange={setPage}
          />
        </div>
      ) : null}

      <Modal
        open={!!preview}
        onCancel={() => setPreview(null)}
        footer={null}
        width={860}
        title={preview?.title || preview?.filename || '素材预览'}
        className="mat-preview-modal"
      >
        {preview ? (
          <div className="mat-preview">
            <div className="mat-preview__stage">
              {preview.kind === 'image' && !previewBroken ? (
                <img
                  src={preview.cos_url}
                  alt={preview.alt || preview.title || ''}
                  onError={() => setPreviewBroken(true)}
                />
              ) : preview.kind === 'image' ? (
                <div className="mat-preview__file">
                  <span className="mat-preview__file-icon"><PictureOutlined /></span>
                  <span>原图无法加载，可能已被删除或存储不可访问</span>
                  <Button size="small" href={preview.cos_url} target="_blank" rel="noopener noreferrer">
                    尝试直接打开
                  </Button>
                </div>
              ) : (
                <div className="mat-preview__file">
                  <span className="mat-preview__file-icon">{kindIcon(preview.kind)}</span>
                  <span>{preview.filename || '文件素材'}</span>
                </div>
              )}
            </div>

            <aside className="mat-preview__side">
              <dl className="mat-preview__facts">
                <div>
                  <dt>来源</dt>
                  <dd>{SOURCE_LABELS[preview.source] || preview.source}</dd>
                </div>
                <div>
                  <dt>类型</dt>
                  <dd>{KIND_LABELS[preview.kind] || preview.kind}</dd>
                </div>
                <div>
                  <dt>大小</dt>
                  <dd>{formatBytes(preview.size_bytes)}</dd>
                </div>
                {preview.width && preview.height ? (
                  <div>
                    <dt>尺寸</dt>
                    <dd>{preview.width}×{preview.height}</dd>
                  </div>
                ) : null}
                <div>
                  <dt>使用</dt>
                  <dd>{preview.usage_count} 次</dd>
                </div>
                <div>
                  <dt>创建</dt>
                  <dd>{formatDateTime(preview.created_at)}</dd>
                </div>
                {preview.license ? (
                  <div>
                    <dt>许可</dt>
                    <dd>{preview.license}</dd>
                  </div>
                ) : null}
              </dl>

              <div className="mat-preview__actions">
                <Button type="primary" onClick={() => copyUrl(preview)}>复制地址</Button>
                {preview.kind === 'image' ? (
                  <Button onClick={() => copyMarkdown(preview)}>复制 Markdown</Button>
                ) : null}
                <Button
                  icon={<ExportOutlined />}
                  href={preview.cos_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  打开原文件
                </Button>
              </div>
            </aside>
          </div>
        ) : null}
      </Modal>
    </div>
  )
}
