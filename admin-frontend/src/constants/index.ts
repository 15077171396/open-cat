import type { PointTxType } from '../types'

/**
 * 教程分类（**兜底值**，实际选项由 `useCategoryOptions('tutorial', ...)` 从「分类管理」拉取）。
 * 前 4 项对齐 2026-10-18 线上实测分类；后 4 项为历史分类，保留以免存量内容编辑时选项缺失。
 * ⚠️ 首项同时是新建教程的默认分类（`Tutorials/Edit.tsx` 用 `TUTORIAL_CATEGORIES[0]`）。
 */
export const TUTORIAL_CATEGORIES = ['AI智能', 'ComfyUI', '编程开发', '设计创作', 'Coze', 'Stable Diffusion', 'AI 视频', 'Prompt', '效率办公'] as const

/** 教程更新状态 */
export const TUTORIAL_STATUSES = [
  { value: 'updating', label: '更新中' },
  { value: 'completed', label: '已完结' },
] as const

/** 资源类型（与用户前端 RESOURCE_TYPES_FALLBACK 对齐） */
export const RESOURCE_TYPES = [
  { value: 'tool', label: '工具' },
  { value: 'model', label: '模型' },
  { value: 'dataset', label: '数据集' },
  { value: 'source', label: '源码' },
  { value: 'doc', label: '文档' },
  { value: 'other', label: '其他' },
] as const

/** 节点分类 */
export const NODE_CATEGORIES = ['加载', '条件控制', '采样器', 'VAE', 'Latent', '图像', '遮罩', '模型', '其他'] as const

/** 扩展分类 */
export const PLUGIN_CATEGORIES = [
  { value: 'ComfyUI 节点', label: 'ComfyUI 节点', color: 'geekblue' },
  { value: 'Coze 扩展', label: 'Coze 扩展', color: 'cyan' },
  { value: '其他扩展', label: '其他扩展', color: 'green' },
] as const

/** 工具分类（动态分类加载失败时的兜底） */
export const TOOL_CATEGORIES = ['Prompt', '图像', '其他'] as const

/** 工具类型 */
export const TOOL_TYPES = [
  { value: 'ai', label: 'AI 工具' },
  { value: 'frontend', label: '前端工具' },
] as const

/** 工具使用权限（仅对 AI 工具生效：免费/需认证/需积分） */
export const TOOL_ACCESS_MODES = [
  { value: 'free', label: '免费使用', color: 'green' },
  { value: 'auth', label: '需认证', color: 'blue' },
  { value: 'points', label: '需积分', color: 'orange' },
] as const

/** 工具表单字段类型 */
export const TOOL_FIELD_TYPES = [
  { value: 'text', label: '单行文本' },
  { value: 'textarea', label: '多行文本' },
] as const

/** 网盘平台预置选项（value 存库，label 展示；平台支持自由输入，不限于以下列表） */
export const NETDISK_PLATFORM_OPTIONS = [
  { value: 'baidu', label: '百度网盘' },
  { value: 'quark', label: '夸克网盘' },
  { value: 'mobile', label: '移动网盘' },
  { value: 'aliyun', label: '阿里云盘' },
  { value: '123pan', label: '123云盘' },
  { value: 'tianyi', label: '天翼云盘' },
  { value: 'lanzou', label: '蓝奏云' },
  { value: 'xunlei', label: '迅雷云盘' },
  { value: 'onedrive', label: 'OneDrive' },
  { value: 'github', label: 'GitHub' },
  { value: 'official', label: '官方文档' },
  { value: 'other', label: '其他' },
] as const

/** 扩展来源平台列表 */
export const SOURCE_NAMES = ['github', 'quark', 'baidu', 'mobile', 'official', 'other'] as const

/** 空网盘链接模板 */
export const EMPTY_NETDISK_LINK = {
  platform: NETDISK_PLATFORM_OPTIONS[0].value,
  url: '',
  extract_code: '',
}

/** 空扩展来源模板 */
export const EMPTY_PLUGIN_SOURCE = {
  source_name: SOURCE_NAMES[0],
  url: '',
  extract_code: '',
}

/** 默认分页大小 */
export const DEFAULT_PAGE_SIZE = 20

/** 积分流水类型 → 展示文案与标签色（用户详情抽屉与全站积分流水页共用口径，勿在页面内重复定义） */
export const POINT_TX_TYPE_MAP: Record<PointTxType, { color: string; text: string }> = {
  consume: { color: 'orange', text: '工具消耗' },
  admin_set: { color: 'blue', text: '管理员设置' },
  admin_grant: { color: 'green', text: '管理员充值' },
  admin_deduct: { color: 'red', text: '管理员扣减' },
  redeem: { color: 'purple', text: '兑换码充值' },
  invite: { color: 'cyan', text: '邀请奖励' },
}

/**
 * 管理员令牌存储键名。刻意用 sessionStorage 而非 localStorage：与前台同源，
 * 前台渲染的不可信内容任一处 XSS 都能读到长效 refresh token，sessionStorage
 * 至少保证关标签页即失效。局限：不能防 XSS，只是缩小暴露窗口。
 */
export const AUTH_TOKEN_KEY = 'ai_cat_admin_token'
export const AUTH_REFRESH_KEY = 'ai_cat_admin_refresh'
