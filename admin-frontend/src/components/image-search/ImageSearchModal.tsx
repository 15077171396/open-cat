/** 图片搜索弹窗：搜索、按图源分区、预览、Alt/署名，转存后回调编辑器插入。 */

import { SearchOutlined } from '@ant-design/icons'
import { Alert, Button, Input, Modal, Segmented, Spin } from 'antd'
import { useCallback, useEffect, useRef, useState } from 'react'
import { getErrorMessage } from '../../utils/error'
import ImageSearchGrid from './ImageSearchGrid'
import ImageSearchPreview from './ImageSearchPreview'
import { importImage, touchMaterial } from './imageSearchApi'
import type {
  ImageInsertPayload, ImageSearchFilters, ImageSearchItem,
} from './types'
import { useImageSearch } from './useImageSearch'

interface Props {
  open: boolean
  initialQuery?: string
  onClose: () => void
  onInsert: (payload: ImageInsertPayload) => void
}

const DEFAULT_FILTERS: ImageSearchFilters = { type: 'all', orientation: 'all' }

const TYPE_OPTIONS = [
  { label: '全部', value: 'all' },
  { label: '照片', value: 'photo' },
  { label: '插画', value: 'illustration' },
  { label: '表情包', value: 'meme' },
]

const ORIENTATION_OPTIONS = [
  { label: '全部', value: 'all' },
  { label: '横图', value: 'landscape' },
  { label: '竖图', value: 'portrait' },
  { label: '方图', value: 'square' },
]

export default function ImageSearchModal({
  open, initialQuery = '', onClose, onInsert,
}: Props) {
  const hook = useImageSearch(open, initialQuery)
  const [queryInput, setQueryInput] = useState(initialQuery)
  const [filters, setFilters] = useState<ImageSearchFilters>(DEFAULT_FILTERS)
  const [selected, setSelected] = useState<ImageSearchItem | null>(null)
  const [alt, setAlt] = useState('')
  const [attribution, setAttribution] = useState(true)
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState('')
  const searchRef = useRef(hook.search)
  const queryInputRef = useRef(queryInput)

  useEffect(() => {
    searchRef.current = hook.search
  }, [hook.search])

  useEffect(() => {
    if (!open) return
    setQueryInput(initialQuery)
    // 同步写 ref：下面那个搜索 effect 与本 effect 同周期执行，读 state 会慢一帧
    queryInputRef.current = initialQuery
    setFilters(DEFAULT_FILTERS)
    setSelected(null)
    setImportError('')
  }, [open, initialQuery])

  // 只在「打开弹窗」和「切换类型/方向」时自动搜一次。
  // ⚠️ 输入框刻意不参与：边打字边请求会瞬间打满图源配额（Pexels 180/h、抓取类 60/h），
  // 改成回车或点「搜索」才发起。
  useEffect(() => {
    if (!open || !hook.options?.enabled) return
    void searchRef.current(queryInputRef.current.trim(), filters)
  }, [open, filters, hook.options?.enabled])

  const selectItem = useCallback((item: ImageSearchItem) => {
    setSelected(item)
    setAlt(item.alt || item.title || '')
    // 本站素材和表情包/网络图源默认不署名；Pexels/Pixabay 仍默认带作者署名。
    setAttribution(item.source === 'pexels' || item.source === 'pixabay' || item.requires_attribution)
    setImportError('')
  }, [])

  const submitSearch = useCallback(() => {
    void hook.search(queryInput.trim(), filters)
  }, [hook, queryInput, filters])

  const handleInsert = useCallback(async () => {
    if (!selected || !alt.trim() || importing) return
    setImporting(true)
    setImportError('')
    try {
      const data = await importImage({
        source: selected.source,
        sourceId: selected.source_id,
        alt: alt.trim(),
        query: hook.query,
      })
      void touchMaterial(data.asset_id).catch(() => {})
      onInsert({
        url: data.url,
        alt: data.alt || alt.trim(),
        credit: attribution ? data.credit : null,
      })
      onClose()
    } catch (err) {
      setImportError(getErrorMessage(err, '图片转存失败，请重试'))
    } finally {
      setImporting(false)
    }
  }, [selected, alt, importing, hook.query, attribution, onInsert, onClose])

  const sourceTabs = [
    { label: '全部', value: 'all' },
    ...hook.sources.map((source) => ({ label: source.name, value: source.id })),
  ]
  const selectedKey = selected ? `${selected.source}:${selected.source_id}` : ''
  const showInitialSpin = hook.optionsLoading && !hook.options

  return (
    <Modal
      open={open}
      onCancel={() => { if (!importing) onClose() }}
      footer={null}
      centered
      width="min(1080px, calc(100vw - 32px))"
      zIndex={10000}
      mask={{ closable: !importing }}
      keyboard={!importing}
      title={(
        <div className="image-search-head">
          <strong>搜索图片</strong>
          <span>选中后转存到本站，再插入正文</span>
        </div>
      )}
      className="image-search-modal"
      styles={{ body: { padding: 0 } }}
    >
      {showInitialSpin ? (
        <div className="image-search-loading"><Spin /></div>
      ) : null}

      {hook.optionsError ? (
        <Alert
          type="warning"
          showIcon
          title={hook.optionsError}
          action={<Button size="small" onClick={hook.reloadOptions}>重试</Button>}
        />
      ) : null}

      {hook.options && !hook.options.enabled ? (
        <Alert
          type="info"
          showIcon
          title="图片搜索未开启"
          description="请在「系统设置 → 上传与素材 → 图片搜索」中开启并配置图源。"
        />
      ) : null}

      {hook.options?.enabled ? (
        <>
          <div className="image-search-toolbar">
            <div className="image-search-toolbar__search">
              <Input
                value={queryInput}
                onChange={(event) => setQueryInput(event.target.value)}
                onPressEnter={submitSearch}
                prefix={<SearchOutlined />}
                allowClear
                placeholder="搜索图片，如 Python 编程 / 团队协作（回车搜索）"
                aria-label="搜索图片关键词"
              />
              <Button type="primary" onClick={submitSearch}>搜索</Button>
            </div>
            <div className="image-search-toolbar__row">
              <Segmented
                value={hook.activeSource}
                onChange={(value) => hook.setActiveSource(String(value))}
                options={sourceTabs}
                aria-label="图片图源"
              />
              <div className="image-search-toolbar__filters">
                <Segmented
                  value={filters.type}
                  onChange={(value) => setFilters((prev) => ({
                    ...prev, type: value as ImageSearchFilters['type'],
                  }))}
                  options={TYPE_OPTIONS}
                  aria-label="图片类型"
                />
                <Segmented
                  value={filters.orientation}
                  onChange={(value) => setFilters((prev) => ({
                    ...prev, orientation: value as ImageSearchFilters['orientation'],
                  }))}
                  options={ORIENTATION_OPTIONS}
                  aria-label="图片方向"
                />
              </div>
            </div>
          </div>

          {hook.sources.length === 0 ? (
            <Alert
              type="warning"
              showIcon
              title="尚未配置图源"
              description="请到「系统设置 → 上传与素材 → 图片搜索」填写 Pexels 或 Pixabay 的 API Key。"
            />
          ) : (
            <div className={`image-search-body${selected ? ' has-preview' : ''}`}>
              <div className="image-search-results">
                <ImageSearchGrid
                  sources={hook.sources}
                  results={hook.results}
                  activeSource={hook.activeSource}
                  selectedKey={selectedKey}
                  query={hook.query}
                  onSelect={selectItem}
                  onRetry={(source) => { void hook.retry(source, filters) }}
                  onLoadMore={(source) => { void hook.loadMore(source, filters) }}
                />
              </div>
              {selected ? (
                <ImageSearchPreview
                  item={selected}
                  sourceName={
                    hook.sources.find((item) => item.id === selected.source)?.name
                    || selected.source
                  }
                  alt={alt}
                  attribution={attribution}
                  importing={importing}
                  importError={importError}
                  cosConfigured={hook.options.cos_configured}
                  onAltChange={setAlt}
                  onAttributionChange={setAttribution}
                  onInsert={() => { void handleInsert() }}
                />
              ) : null}
            </div>
          )}

          <div className="image-search-foot">
            Pexels/Pixabay 默认附带作者与许可证署名；网络图源和本站素材可手动勾选。加载失败或长时间无响应的图片会自动隐藏。
          </div>
        </>
      ) : null}
    </Modal>
  )
}
