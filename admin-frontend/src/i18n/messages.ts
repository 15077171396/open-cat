/**
 * 管理端 i18n 词表（第一批：外壳 / 登录 / 通用组件）。
 *
 * 约定：
 * - key 用「模块.用途」，只放**界面文案**，不放业务枚举（业务枚举由接口下发）；
 * - 值里 `{name}` 由 `translate()` 替换；缺英文时回退中文，再回退 key（不会渲染空白）；
 * - 新增语言 = 加一份字典 + 在 `SUPPORTED_LOCALES` 登记，不改调用点。
 */

export const SUPPORTED_LOCALES = ['zh-CN', 'en-US'] as const
export type Locale = (typeof SUPPORTED_LOCALES)[number]

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && (SUPPORTED_LOCALES as readonly string[]).includes(value)
}

type MessageTable = Record<string, string>

const zh: MessageTable = {
  'common.refresh': '刷新',
  'common.retry': '重试',
  'common.cancel': '取消',
  'common.confirm': '确定',
  'common.save': '保存',
  'common.search': '搜索',
  'common.loading': '加载中…',
  'common.total': '共 {total} 条',
  'common.totalPrefix': '共 ',
  'common.items': '条',
  'common.clearFilters': '清空筛选条件',
  'common.reset': '重置',
  'common.resetHint': '清空搜索与筛选条件',
  'common.selectedPrefix': '已选 ',
  'common.selectedSuffix': ' 项',

  'layout.brand': 'AI猫后台',
  'layout.brandShort': '猫',
  'layout.profile': '个人中心',
  'layout.logout': '退出登录',
  'layout.theme': '主题模式',
  'layout.light': '亮色',
  'layout.dark': '暗色',
  'layout.system': '跟随系统',
  'layout.layout': '布局',
  'layout.side': '侧边导航',
  'layout.top': '顶部导航',
  'layout.language': '语言',
  'layout.search': '全局搜索',
  'layout.searchTitle': '全局搜索（Ctrl+K）',
  'layout.openMenu': '打开菜单',
  'layout.collapseSidebar': '收起侧边栏',
  'layout.expandSidebar': '展开侧边栏',
  'layout.menuLoadFailed': '菜单加载失败',
  'layout.retryMenu': '重新加载菜单',
  'layout.adminFallback': '管理员',

  'errorBoundary.fatalTitle': '页面发生异常',
  'errorBoundary.fatalDesc': '页面渲染出现未知错误，请刷新重试；若反复出现，请将控制台日志反馈给开发人员。',
  'errorBoundary.reload': '刷新页面',
  'errorBoundary.title': '此页面渲染失败（其它页面仍可正常使用）',
  'errorBoundary.path': '路径',
  'errorBoundary.retry': '重试这一页',

  'todo.title': '待办事项',
  'todo.refresh': '刷新待办',
  'todo.loadFailed': '待办加载失败，显示的是上次结果',
  'todo.empty': '暂无待办',
  'todo.itemAria': '{label}，{count} 条',
  'todo.badge': '待办',
  'todo.badgeWithCount': '待办 {count} 条',
  'todo.group.auth_request': '实名认证待审',
  'todo.group.report': '用户举报待处理',
  'todo.group.moderation': '机审留痕待复核',
  'todo.group.feedback': '用户反馈待处理',
  'todo.group.scheduled_job': '定时任务执行失败',
  'todo.group.ai_quota': 'AI 额度将尽',

  'command.pages': '页面',
  'command.actions': '快捷操作',
  'command.tutorials': '教程',
  'command.users': '用户',
  'command.footerHint': '↑↓ 选择 · Enter 打开 · Esc 关闭',
  'command.shortcutHint': 'Ctrl / ⌘ + K 随时唤起',
  'command.placeholder': '搜索页面、教程、用户，或输入操作…',
  'command.noResults': '没有匹配的页面、教程或用户',
  'command.noQuery': '输入关键词开始搜索',
  'command.newTutorial': '新建教程',
  'command.themeLight': '切换到亮色主题',
  'command.themeDark': '切换到暗色主题',
  'command.themeSystem': '主题跟随系统',
  'command.layoutTop': '切换为顶部导航',
  'command.layoutSide': '切换为侧边导航',
  'command.languageZh': '切换到简体中文',
  'command.languageEn': '切换到 English',
  'command.hintAppearance': '外观',
  'command.hintLayout': '布局',
  'command.hintTutorial': '教程',
  'command.hintUser': '用户',
  'command.hintShortcut': '快捷入口',

  'login.tag': '管理员入口',
  'login.title': 'AI学习猫 管理后台',
  'login.subtitle': '仅限管理员账号登录',
  'login.account': '账号',
  'login.accountPlaceholder': '手机号 / 邮箱',
  'login.accountRequired': '请输入手机号或邮箱',
  'login.password': '密码',
  'login.passwordPlaceholder': '请输入密码',
  'login.passwordRequired': '请输入密码',
  'login.totp': '二次验证码',
  'login.totpPlaceholder': '6 位验证码 / 恢复码',
  'login.totpRequired': '请输入验证器 App 中的验证码',
  'login.totpHint': '也可输入一次性的恢复码（形如 ABCDE-FGHJK）',
  'login.submit': '登录',
  'login.submitting': '登录中...',
  'login.missing': '请填写账号和密码',
  'login.failed': '登录失败',
  'login.totpMissing': '请输入验证器 App 中的 6 位验证码',
}

const en: MessageTable = {
  'common.refresh': 'Refresh',
  'common.retry': 'Retry',
  'common.cancel': 'Cancel',
  'common.confirm': 'OK',
  'common.save': 'Save',
  'common.search': 'Search',
  'common.loading': 'Loading…',
  'common.total': 'Total {total}',
  'common.totalPrefix': 'Total ',
  'common.items': 'items',
  'common.clearFilters': 'Clear filters',
  'common.reset': 'Reset',
  'common.resetHint': 'Clear search and filters',
  'common.selectedPrefix': '',
  'common.selectedSuffix': ' selected',

  'layout.brand': 'AI Cat Admin',
  'layout.brandShort': 'Cat',
  'layout.profile': 'Profile',
  'layout.logout': 'Sign out',
  'layout.theme': 'Theme',
  'layout.light': 'Light',
  'layout.dark': 'Dark',
  'layout.system': 'Follow system',
  'layout.layout': 'Layout',
  'layout.side': 'Sidebar',
  'layout.top': 'Top navigation',
  'layout.language': 'Language',
  'layout.search': 'Global search',
  'layout.searchTitle': 'Global search (Ctrl+K)',
  'layout.openMenu': 'Open menu',
  'layout.collapseSidebar': 'Collapse sidebar',
  'layout.expandSidebar': 'Expand sidebar',
  'layout.menuLoadFailed': 'Failed to load menu',
  'layout.retryMenu': 'Reload menu',
  'layout.adminFallback': 'Administrator',

  'errorBoundary.fatalTitle': 'Something went wrong',
  'errorBoundary.fatalDesc':
    'An unexpected error occurred while rendering. Reload to try again; if it keeps happening, share the console log with the developers.',
  'errorBoundary.reload': 'Reload page',
  'errorBoundary.title': 'This page failed to render (other pages still work)',
  'errorBoundary.path': 'Path',
  'errorBoundary.retry': 'Retry this page',

  'todo.title': 'To-do',
  'todo.refresh': 'Refresh to-do',
  'todo.loadFailed': 'Failed to load to-do; showing the last result',
  'todo.empty': 'Nothing to do',
  'todo.itemAria': '{label}, {count} items',
  'todo.badge': 'To-do',
  'todo.badgeWithCount': 'To-do {count} items',
  'todo.group.auth_request': 'Verification pending',
  'todo.group.report': 'Reports pending',
  'todo.group.moderation': 'Moderation review pending',
  'todo.group.feedback': 'Feedback pending',
  'todo.group.scheduled_job': 'Scheduled jobs failed',
  'todo.group.ai_quota': 'AI quota running low',

  'command.pages': 'Pages',
  'command.actions': 'Quick actions',
  'command.tutorials': 'Tutorials',
  'command.users': 'Users',
  'command.footerHint': '↑↓ Select · Enter Open · Esc Close',
  'command.shortcutHint': 'Press Ctrl / ⌘ + K anytime',
  'command.placeholder': 'Search pages, tutorials, users or actions…',
  'command.noResults': 'No matching pages, tutorials or users',
  'command.noQuery': 'Type a keyword to search',
  'command.newTutorial': 'New tutorial',
  'command.themeLight': 'Switch to light theme',
  'command.themeDark': 'Switch to dark theme',
  'command.themeSystem': 'Follow system theme',
  'command.layoutTop': 'Use top navigation',
  'command.layoutSide': 'Use sidebar navigation',
  'command.languageZh': 'Switch to 简体中文',
  'command.languageEn': 'Switch to English',
  'command.hintAppearance': 'Appearance',
  'command.hintLayout': 'Layout',
  'command.hintTutorial': 'Tutorial',
  'command.hintUser': 'User',
  'command.hintShortcut': 'Shortcut',

  'login.tag': 'Admin access',
  'login.title': 'AI Cat Admin',
  'login.subtitle': 'Administrator accounts only',
  'login.account': 'Account',
  'login.accountPlaceholder': 'Phone / email',
  'login.accountRequired': 'Enter your phone number or email',
  'login.password': 'Password',
  'login.passwordPlaceholder': 'Enter your password',
  'login.passwordRequired': 'Enter your password',
  'login.totp': 'Two-factor code',
  'login.totpPlaceholder': '6-digit code / recovery code',
  'login.totpRequired': 'Enter the code from your authenticator app',
  'login.totpHint': 'You can also use a one-time recovery code (e.g. ABCDE-FGHJK)',
  'login.submit': 'Sign in',
  'login.submitting': 'Signing in…',
  'login.missing': 'Enter your account and password',
  'login.failed': 'Sign-in failed',
  'login.totpMissing': 'Enter the 6-digit code from your authenticator app',
}

export const MESSAGES: Record<Locale, MessageTable> = { 'zh-CN': zh, 'en-US': en }

/** 某个 key 在当前语言里是否有译文（业务枚举回退用：有译文才覆盖接口下发的中文文案） */
export function hasMessage(locale: Locale, key: string): boolean {
  return MESSAGES[locale]?.[key] !== undefined
}

export function translate(locale: Locale, key: string, vars?: Record<string, string | number>): string {
  const table = MESSAGES[locale] ?? MESSAGES['zh-CN']
  const template = table[key] ?? MESSAGES['zh-CN'][key] ?? key
  if (!vars) return template
  return template.replace(/\{(\w+)\}/g, (_, name: string) =>
    vars[name] === undefined ? `{${name}}` : String(vars[name]),
  )
}
