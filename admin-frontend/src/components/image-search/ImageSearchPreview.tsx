/** 选中图片的预览与插入参数（Alt、署名、转存状态）。 */

import { Button, Checkbox, Input } from 'antd'
import { useEffect, useState } from 'react'
import type { ImageSearchItem } from './types'

interface Props {
  item: ImageSearchItem
  /** 图源显示名，由后端 options 下发；前端不再维护一份图源名称表。 */
  sourceName: string
  alt: string
  attribution: boolean
  importing: boolean
  importError: string
  cosConfigured: boolean
  onAltChange: (value: string) => void
  onAttributionChange: (value: boolean) => void
  onInsert: () => void
}

export default function ImageSearchPreview({
  item, sourceName, alt, attribution, importing, importError, cosConfigured,
  onAltChange, onAttributionChange, onInsert,
}: Props) {
  const primary = item.source === 'local' ? item.preview_url : item.preview_proxy_url
  const fallback = item.source === 'local' ? item.thumb_url : item.thumb_proxy_url
  const [src, setSrc] = useState(primary)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setSrc(primary)
    setFailed(false)
  }, [item, primary])

  const required = item.requires_attribution
  const sourceLabel = sourceName || item.source

  return (
    <aside className="image-search-preview" aria-label="图片预览">
      <div className="image-search-preview__media">
        {failed ? (
          <span className="image-search-preview__placeholder">预览不可用，可直接插入</span>
        ) : (
          <img
            src={src}
            alt={alt || item.alt || item.title || '预览'}
            onError={() => {
              if (fallback && src !== fallback) setSrc(fallback)
              else setFailed(true)
            }}
          />
        )}
      </div>

      <div className="image-search-preview__meta">
        <div className="image-search-preview__line">
          <span className="image-search-preview__source">{sourceLabel}</span>
          <span>{item.width} × {item.height}</span>
        </div>
        {item.author ? (
          <div className="image-search-preview__line">
            <span>作者</span>
            {item.author_url ? (
              <a href={item.author_url} target="_blank" rel="noopener noreferrer nofollow">
                {item.author}
              </a>
            ) : <span>{item.author}</span>}
          </div>
        ) : null}
        <div className="image-search-preview__line">
          <span>授权</span>
          {item.license_url ? (
            <a href={item.license_url} target="_blank" rel="noopener noreferrer nofollow">
              {item.license || sourceLabel}
            </a>
          ) : <span>{item.license || sourceLabel}</span>}
        </div>
        {item.page_url ? (
          <a
            className="image-search-preview__origin"
            href={item.page_url}
            target="_blank"
            rel="noopener noreferrer nofollow"
          >
            查看原图页面
          </a>
        ) : null}
      </div>

      <label className="image-search-preview__field">
        <span>图片说明（Alt）</span>
        <Input.TextArea
          value={alt}
          onChange={(event) => onAltChange(event.target.value)}
          maxLength={300}
          rows={2}
          placeholder="描述图片内容，便于无障碍阅读"
          aria-label="图片说明"
        />
      </label>

      <Checkbox
        checked={required || attribution}
        disabled={required}
        onChange={(event) => onAttributionChange(event.target.checked)}
      >
        {required ? '插入署名（该图源要求）' : '插入署名'}
      </Checkbox>
      <div className="image-search-preview__credit">
        图片：{item.author || '未知作者'} / {sourceLabel}
      </div>

      {importError ? <div className="image-search-preview__error">{importError}</div> : null}
      {!cosConfigured ? (
        <div className="image-search-preview__error">COS 未配置，暂时无法保存图片</div>
      ) : null}

      <Button
        type="primary"
        block
        loading={importing}
        disabled={!alt.trim() || !cosConfigured}
        onClick={onInsert}
      >
        {importing ? '转存中…' : '插入正文'}
      </Button>
    </aside>
  )
}
