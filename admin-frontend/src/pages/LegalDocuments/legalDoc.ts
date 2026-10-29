/** 法律文档页的纯逻辑：正文字数统计与「自定义 / 默认」判定（抽出来是为了能单测）。 */

// 与后端 legal_documents_service._plain_length 同一口径：去标签、去 HTML 实体、去空白。
// 两端各算一次是为了让「已保存」与「草稿中」的字数可比较 —— 后端只知道已保存的那份。
const TAG_RE = /<[^>]+>/g
const ENTITY_RE = /&[a-zA-Z#0-9]+;/g
const WS_RE = /\s+/g

export function plainTextLength(html: string | null | undefined): number {
  const text = (html || '').replace(TAG_RE, '').replace(ENTITY_RE, '').replace(WS_RE, '')
  return text.length
}

export interface LegalDocMeta {
  key: string
  type: string
  title: string
  /** 后端值：库里存过自定义文本 */
  is_custom: boolean
  length: number
  updated_at: string | null
  public_hint: string
  public_api: string
}

export interface LegalDocState {
  /** 草稿是否非空（= 保存后就是自定义文本） */
  isCustom: boolean
  /** 草稿的正文长度 */
  length: number
  /** 该文档有未保存改动 */
  pending: boolean
}

/**
 * 文档状态：以**草稿**为准，而不是后端返回的已保存状态。
 *
 * 否则清空正文准备恢复默认时，左栏仍写着「自定义 · 1200 字」，
 * 用户会以为没生效（这个页面最容易犯的错就是把「已保存」当成「当前」）。
 */
export function docState(
  doc: LegalDocMeta,
  draftValue: string,
  savedValue: string,
  defaultValue: string,
): LegalDocState {
  const effective = draftValue || defaultValue
  return {
    isCustom: !!draftValue.trim(),
    length: plainTextLength(effective),
    pending: (draftValue || '') !== (savedValue || ''),
  }
}
