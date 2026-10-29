export type WritingOperation =
  | 'rewrite' | 'polish' | 'expand' | 'shorten' | 'continue'
  | 'summarize' | 'seo_description' | 'titles' | 'keywords'
  | 'illustrate_prompt' | 'illustrate'

export interface WritingSelection {
  index: number
  length: number
  text: string
}

export interface WritingContext {
  title?: string
  category?: string
  tags?: string[]
  chapter_title?: string
  outline?: string[]
  article_text?: string
  before?: string
  after?: string
}

export interface WritingHistoryTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface WritingImageRequest {
  /** 面板临时选择的图像模型；空表示默认图像模型 */
  model_code?: string
  prompt: string
  alt: string
  style: 'flat' | 'photo' | 'render' | 'line'
  size: '1024*1024' | '1280*720' | '720*1280' | '1024*768'
  count: number
}

export interface WritingRunRequest {
  operation: WritingOperation
  content_type: 'tutorial_node' | 'node' | 'plugin' | 'resource'
  content_id?: number | null
  /** 面板临时选择的文本模型；空表示跟随提示词配置 */
  model_code?: string
  instruction?: string
  target_length?: number | null
  selection?: WritingSelection | null
  context?: WritingContext
  history?: WritingHistoryTurn[]
  image?: WritingImageRequest
}

/** 发起一次写作的界面参数：面板表单与重放（重新生成）共用 */
export interface WritingRunOptions {
  image?: WritingImageRequest
  instruction?: string
  targetLength?: number | null
  modelCode?: string
}

export interface WritingSseEvent {
  event: 'meta' | 'delta' | 'status' | 'result' | 'image' | 'usage' | 'done' | 'error'
  data: Record<string, unknown>
}

export type AiFillField = 'title' | 'seo_description' | 'keywords' | 'summary'

export interface WritingOptions {
  enabled: boolean
  operations: Array<{ operation: WritingOperation; display_name: string; output_type: string }>
  text_models: Array<{ code: string; display_name: string }>
  image_models: Array<{ code: string; display_name: string; image_price?: number; is_default?: boolean }>
  image_model: { code: string; display_name: string; image_price?: number } | null
  styles: Array<{ value: string; label: string }>
  sizes: string[]
  image_daily_limit: number
  image_used_today: number
  hourly_limit: number
}
