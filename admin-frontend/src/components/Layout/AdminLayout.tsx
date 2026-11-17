import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Drawer,
  Grid,
  Layout,
  Menu,
  Avatar,
  Dropdown,
  Typography,
  Space,
  Skeleton,
  Button,
  Tooltip,
  Input,
  Modal,
  Popover,
  Tour,
} from 'antd'
import type { MenuProps } from 'antd'
import type { CSSProperties } from 'react'
import {
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  LogoutOutlined,
  UserOutlined,
  ReloadOutlined,
  SearchOutlined,
  TranslationOutlined,
  FullscreenOutlined,
  FullscreenExitOutlined,
  LockOutlined,
  SettingOutlined,
  AppstoreOutlined,
  FontSizeOutlined,
  MoonOutlined,
  SunOutlined,
  RobotOutlined,
} from '@ant-design/icons'
import { useNavigate, useLocation, Outlet } from 'react-router-dom'
import { useAuthStore } from '../../store/authStore'
import { usePrefsStore, resolveMode, resolveMenuStyle } from '../../store/prefsStore'
import type { ComponentSize } from '../../store/prefsStore'
import { apiGet } from '../../api/client'
import { saveLockPassword, verifyLockPassword } from '../../api/preferences'
import { usePreferencesSync } from '../../hooks/usePreferencesSync'
import { toast } from '../../utils/toast'
import RouteErrorBoundary from '../RouteErrorBoundary'
import TodoBell from './TodoBell'
import SettingsDrawer from './SettingsDrawer'
import CommandPalette from './CommandPalette'
import PageTabs from './PageTabs'
import { usePageTitleStore } from '../../store/pageTitleStore'
import { menuIcon } from '../../constants/menuIcons'
import { LOCALE_LABELS, useI18n } from '../../i18n'
import type { Locale } from '../../i18n'
import {
  GROUP_KEY_PREFIX,
  collectMenuLabels,
  externalLeafKeys,
  filterMenuTree,
  groupMenuKey,
  nextOpenKeys,
  resolveOpenGroupKey,
  resolveSelectedKey,
} from '../../utils/menu'
import {
  HOME_TAB,
  addTab,
  closeAllTabs,
  closeLeftTabs,
  closeOtherTabs,
  closeRightTabs,
  nextTabAfterClose,
  readPinnedTabs,
  readTabs,
  removeTab,
  writePinnedTabs,
  writeTabs,
} from '../../utils/pageTabs'
import type { MenuLeaf, MenuTree } from '../../types'

const { Sider, Header, Content } = Layout

/**
 * 侧边菜单**不在前端写死**：结构由 `GET /admin/menus` 下发
 * （配置源 `backend/app/services/admin_menu.py`），本组件只负责
 * 「按权限过滤 → 组装 antd Menu → 跟随路由高亮/展开」。
 *
 * 这么拆的好处：调整分组、加页面入口、改顺序/图标，全部只改后端那份配置，
 * 不用发版前端；且菜单口径与 RBAC 权限目录同源，不会出现「菜单有入口但点进去 403」。
 *
 * 布局（side / top）与主题模式来自 `store/prefsStore`（localStorage 持久化）：
 * side 为原来的左侧固定 Sider + 顶栏面包屑；top 则把同一份 Menu 以 horizontal
 * 模式塞进顶栏，内容区全宽。两种布局共用菜单数据与导航逻辑，避免两套实现走偏。
 */

/** 菜单缓存：接口抖动时侧栏不至于整块空白（导航是后台基础设施，不能依赖单次请求成败） */
const MENU_CACHE_KEY = 'ai_cat_admin_menu'
/** 缓存结构版本：`MenuTree` 形状变更时递增，避免读到旧结构 */
const MENU_CACHE_VERSION = 2
/** 首次加载失败后的自动重试延迟（再失败才交给用户手动重试） */
const MENU_RETRY_DELAY_MS = 1500

const EMPTY_TREE: MenuTree = { top: [], groups: [], footer: [] }

// 窗口聚焦时重取会话内权限的最小间隔
const FOCUS_REFRESH_INTERVAL_MS = 30_000

function readMenuCache(locale: Locale): MenuTree | null {
  try {
    const raw = sessionStorage.getItem(MENU_CACHE_KEY)
    if (!raw) return null
    // 缓存按语言隔离：切换语言后先用旧语言的菜单顶一会儿，比侧栏整块变空更糟
    const parsed = JSON.parse(raw) as { v?: number; locale?: string; tree?: MenuTree }
    if (
      parsed?.v !== MENU_CACHE_VERSION ||
      parsed.locale !== locale ||
      !parsed.tree ||
      !Array.isArray(parsed.tree.top) ||
      !Array.isArray(parsed.tree.groups)
    ) {
      return null
    }
    return parsed.tree
  } catch {
    return null
  }
}

function writeMenuCache(tree: MenuTree, locale: Locale): void {
  try {
    sessionStorage.setItem(MENU_CACHE_KEY, JSON.stringify({ v: MENU_CACHE_VERSION, locale, tree }))
  } catch {
    // 隐私模式/配额满：缓存只是可选优化，写不进去不影响主流程
  }
}

// 提取内联样式为模块级常量，避免每次渲染创建新引用
// 尺寸与圆角遵循设计规范（docs/07-开源文档/06-UI设计规范.md）：顶栏 56 / 内边距 16 / 卡片圆角 4
// 颜色一律用 CSS 变量（tokens.css），暗色主题下由 [data-theme='dark'] 覆盖，不写死十六进制
const siderStyle: CSSProperties = {
  background: 'var(--app-card)',
  borderRight: 'none',
  boxShadow: '0 2px 5px rgba(0, 0, 0, 0.08)',
  position: 'fixed',
  left: 0,
  top: 0,
  height: '100vh',
  zIndex: 10,
  display: 'flex',
  flexDirection: 'column',
}

/** 菜单区可滚动：分组展开后总高度会超过视口（小屏尤其明显） */
const menuScrollStyle: CSSProperties = {
  flex: 1,
  // flex 子项默认 min-height:auto，内容超高时会把容器撑破而不是出现内部滚动条
  minHeight: 0,
  overflowY: 'auto',
  overflowX: 'hidden',
  // 滚到菜单末端时不让滚动链接给外层页面（否则会带着内容区一起滚）
  overscrollBehaviorY: 'contain',
  paddingBottom: 8,
}

const menuFallbackStyle: CSSProperties = {
  padding: '16px 12px',
  textAlign: 'center',
}

const headerStyle: CSSProperties = {
  background: 'var(--app-card)',
  padding: '0 16px',
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  borderBottomWidth: 1,
  borderBottomStyle: 'solid',
  borderBottomColor: 'var(--app-border-light)',
  flex: 'none',
  position: 'sticky',
  top: 0,
  zIndex: 1,
}

/** 顶栏布局：logo 与操作区固定，中间的横向菜单吃掉剩余宽度 */
const topHeaderStyle: CSSProperties = { ...headerStyle, gap: 8 }
const topMenuWrapStyle: CSSProperties = { flex: 1, minWidth: 0, overflow: 'hidden' }

const headerLeftStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
}

const collapseTriggerStyle: CSSProperties = {
  cursor: 'pointer',
  fontSize: 18,
  padding: 8,
}

/** 菜单叶子 → antd Menu item（外链交给 <a> 自己开新窗口） */
function toLeafItem(leaf: MenuLeaf) {
  return {
    key: leaf.key,
    icon: menuIcon(leaf.icon),
    label: leaf.url ? (
      <a href={leaf.url} target="_blank" rel="noreferrer">
        {leaf.label}
      </a>
    ) : (
      leaf.label
    ),
  }
}

export default function AdminLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  // 逐字段 selector 订阅：useAuthStore() 整店订阅会让 fetchUser/token 轮换等都重渲染
  // 布局挂着 Outlet，编辑页有重 Quill 实例，alt-tab 一次整片重渲染
  const account = useAuthStore((s) => s.user?.account)
  const avatarUrl = useAuthStore((s) => s.user?.avatar_url)
  const logout = useAuthStore((s) => s.logout)
  const isSuper = useAuthStore((s) => s.isSuper)
  const permissions = useAuthStore((s) => s.permissions)
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem('adm-menu-collapsed') === '1'
    } catch {
      return false
    }
  })
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [pickedGroupKey, setPickedGroupKey] = useState<string | null>(null)
  /** 锁屏：设置密码弹窗 + 全屏遮罩解锁 */
  const [lockSetupOpen, setLockSetupOpen] = useState(false)
  const [lockDraft, setLockDraft] = useState('')
  const [unlockInput, setUnlockInput] = useState('')
  const [lockBusy, setLockBusy] = useState(false)
  /** 解锁令牌只放内存：刷新/重新加载后必然丢失，从而要求重新解锁 */
  const unlockTokenRef = useRef<string | null>(null)
  const [locked, setLocked] = useState(() => {
    try {
      return typeof window !== 'undefined' && window.sessionStorage.getItem('adm-locked') === '1'
    } catch {
      return false
    }
  })
  const [guideOpen, setGuideOpen] = useState(false)
  const [quickOpen, setQuickOpen] = useState(false)

  // 展示偏好：独立 store + localStorage 持久化，退出登录不丢
  const prefs = usePrefsStore()
  const {
    mode,
    layout,
    menuStyle,
    boxStyle,
    pageWidth,
    menuWidth,
    tabStyle,
    pageTransition,
    showTabs,
    accordion,
    showMenuButton,
    showFastEnter,
    showRefreshButton,
    showCrumbs,
    showLanguage,
    showMenuSearch,
    showFullscreen,
    showSizeSelect,
    showNotification,
    showNprogress,
    watermark,
    showAppLogo,
    showGuide,
    aiAssistant,
    componentSize,
    guideDone,
  } = prefs
  const setPrefs = prefs.setPrefs
  const setMode = prefs.setMode
  // 启动后台拉取服务端偏好（服务端优先、失败回落 localStorage），并同步锁屏口令设置态
  const {
    ready: prefsReady,
    hasLockPassword,
    markLockPasswordSet,
  } = usePreferencesSync()
  const { locale, setLocale, t } = useI18n()
  const screens = Grid.useBreakpoint()
  // md=768：手机/小平板改用抽屉菜单；首帧 screens 为空时按桌面处理，避免桌面闪一下移动布局
  const isMobile = screens.md === false
  const isTop = layout === 'top' && !isMobile
  const isMixed = layout === 'mixed' && !isMobile
  const isDouble = layout === 'double' && !isMobile
  const navLayout = isMobile ? 'side' : layout
  const resolvedTheme = resolveMode(mode)
  const isMenuDark = resolveMenuStyle(menuStyle) === 'dark'
  // 双列需要「分组栏 88 + 菜单栏」；宽度来自设置抽屉的「菜单宽度」
  const siderWidth = isDouble ? menuWidth + 88 : menuWidth

  // 侧栏折叠状态持久化（演示站 setting.menuOpen 同样落本地存储）
  useEffect(() => {
    try {
      localStorage.setItem('adm-menu-collapsed', collapsed ? '1' : '0')
    } catch {
      // 隐私模式/配额满：折叠状态只是可选优化
    }
  }, [collapsed])

  // 全屏状态跟随浏览器事件（Esc 退出时按钮图标要同步回来）
  useEffect(() => {
    const onChange = () => setIsFullscreen(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  // 有缓存就先渲染（避免刷新时侧栏闪一下骨架），随后静默校准
  const [menuTree, setMenuTree] = useState<MenuTree | null>(() => readMenuCache(locale))
  const [menuLoading, setMenuLoading] = useState(false)
  const [menuError, setMenuError] = useState(false)
  const [reloadToken, setReloadToken] = useState(0)
  const [openKeys, setOpenKeys] = useState<string[]>([])

  // 菜单管理保存后通知这里重拉菜单：侧栏名称/排序/可见性立即生效
  useEffect(() => {
    const handler = () => setReloadToken((token) => token + 1)
    window.addEventListener('admin-menu-updated', handler)
    return () => window.removeEventListener('admin-menu-updated', handler)
  }, [])

  // 会话期内窗口重新聚焦时重取 /auth/me 与 RBAC 档案：服务端撤销的权限能即时生效，
  // 瞬时故障被清空的权限也能恢复；focus 在 alt-tab 下极频繁，30 秒节流
  useEffect(() => {
    let lastAt = 0
    const onFocus = () => {
      const now = Date.now()
      if (now - lastAt < FOCUS_REFRESH_INTERVAL_MS) return
      lastAt = now
      void useAuthStore.getState().fetchUser()
    }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [])

  // 拉取菜单结构（失败自动重试一次，仍失败则交给用户手动重试）。
  // 语言变化要重取：菜单文案由后端按 Accept-Language 下发，前端兜不了。
  useEffect(() => {
    let alive = true
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    // 切到该语言时有缓存就先用缓存，别让侧栏停在上一门语言上
    setMenuTree((current) => readMenuCache(locale) ?? current)
    const load = async (attempt: number) => {
      setMenuLoading(true)
      try {
        const tree = await apiGet<MenuTree>('/admin/menus')
        if (!alive) return
        setMenuTree(tree)
        writeMenuCache(tree, locale)
        setMenuError(false)
        setMenuLoading(false)
      } catch {
        if (!alive) return
        if (attempt === 0) {
          retryTimer = setTimeout(() => {
            if (alive) void load(1)
          }, MENU_RETRY_DELAY_MS)
          return
        }
        setMenuError(true)
        setMenuLoading(false)
      }
    }
    void load(0)
    return () => {
      alive = false
      if (retryTimer) clearTimeout(retryTimer)
    }
  }, [reloadToken, locale])

  // 按 RBAC 权限裁剪（super_admin 全量可见）；权限变化即时反映，无需重拉菜单
  const visibleTree = useMemo(
    () => (menuTree ? filterMenuTree(menuTree, permissions, isSuper) : EMPTY_TREE),
    [menuTree, permissions, isSuper]
  )

  // 当前选中菜单：匹配路径前缀（更具体的路径优先命中）
  const selectedKey = useMemo(
    () => resolveSelectedKey(visibleTree, location.pathname),
    [visibleTree, location.pathname]
  )

  // 当前选中项所属的老分组（折叠态用它给分组图标上主色）
  const activeGroupKey = useMemo(
    () => resolveOpenGroupKey(visibleTree, selectedKey),
    [visibleTree, selectedKey]
  )

  // 选中项所在分组自动展开，且**只展开这一个**（手风琴）。依赖 pathname 而不是
  // 只依赖 activeGroupKey：同组内跳转时 activeGroupKey 不变，若用户手动展开了别的分组，
  // 就不该继续留着 —— 每次导航都回到「当前页所在分组」。用户手动点开其它分组时
  // 路由没变，这个 effect 不会把他的手势按回去。
  useEffect(() => {
    if (!activeGroupKey) return
    setOpenKeys([activeGroupKey])
  }, [activeGroupKey, location.pathname])

  const externalKeys = useMemo(() => externalLeafKeys(visibleTree), [visibleTree])

  const menuItems = useMemo<MenuProps['items']>(() => {
    const groups = visibleTree.groups.map((group) => ({
      key: groupMenuKey(group.key),
      icon: menuIcon(group.icon),
      label: group.label,
      children: group.items.map(toLeafItem),
      // 折叠态只剩图标，靠主色标出「当前页面所在分组」，否则用户不知道自己在哪一层
      className:
        collapsed && !isTop && !isMixed && !isDouble && !isMobile && groupMenuKey(group.key) === activeGroupKey
          ? 'adm-menu-group-active'
          : undefined,
    }))
    // 顶级独立项（首页「工作台」）渲染在所有分组之上，与分组同级
    const top = visibleTree.top.map(toLeafItem)
    const footer = visibleTree.footer.map(toLeafItem)
    return (footer.length > 0
      ? [...top, ...groups, { type: 'divider' as const }, ...footer]
      : [...top, ...groups]) as MenuProps['items']
  }, [visibleTree, collapsed, activeGroupKey, isTop, isMixed, isDouble, isMobile])

  // 混合 / 双列布局只展示「当前选中分组」的二级菜单，一级分组在顶栏或左列分组栏切换
  useEffect(() => {
    setPickedGroupKey(null)
  }, [location.pathname])

  const focusGroupKey = pickedGroupKey ?? activeGroupKey
  const focusGroup = useMemo(
    () =>
      visibleTree.groups.find((group) => groupMenuKey(group.key) === focusGroupKey) ??
      visibleTree.groups[0] ??
      null,
    [visibleTree, focusGroupKey],
  )

  const focusedMenuItems = useMemo<MenuProps['items']>(() => {
    // 顶级独立项不属于任何分组，混合 / 双列布局没有分组可以承载它 —— 固定在二级菜单最上方
    const top = visibleTree.top.map(toLeafItem)
    const items = focusGroup ? focusGroup.items.map(toLeafItem) : []
    const footer = visibleTree.footer.map(toLeafItem)
    return (footer.length > 0
      ? [...top, ...items, { type: 'divider' as const }, ...footer]
      : [...top, ...items]) as MenuProps['items']
  }, [focusGroup, visibleTree.top, visibleTree.footer])

  const groupMenuItems = useMemo<MenuProps['items']>(
    () =>
      visibleTree.groups.map((group) => ({
        key: groupMenuKey(group.key),
        icon: menuIcon(group.icon),
        label: group.label,
      })),
    [visibleTree],
  )

  /** 混合 / 双列：点一级分组即切到该组，并落到组内第一页（与点菜单一致） */
  const activateGroup = (key: string) => {
    setPickedGroupKey(key)
    const group = visibleTree.groups.find((item) => groupMenuKey(item.key) === key)
    const first = group?.items[0]
    if (!first) return
    if (first.url) window.open(first.url, '_blank', 'noopener,noreferrer')
    else navigate(first.key)
  }

  // 顶栏当前位置（父级 / 页面）：折叠侧栏后仍能判断自己在哪一层
  const currentCrumb = useMemo(() => {
    const topLeaf = visibleTree.top.find((item) => item.key === selectedKey)
    if (topLeaf) return { group: null, label: topLeaf.label }
    for (const group of visibleTree.groups) {
      const leaf = group.items.find((item) => item.key === selectedKey)
      if (leaf) return { group: group.label, label: leaf.label }
    }
    const leaf = visibleTree.footer.find((item) => item.key === selectedKey)
    return leaf ? { group: null, label: leaf.label } : null
  }, [visibleTree, selectedKey])

  // 编辑页：Content 上下 padding 为 0，让编辑器工具栏贴顶
  // （sticky 钉在滚动容器的**内容区**顶部；容器有 24px 上内边距时，工具栏会停在卡片顶边下方
  //   24px，那条带没有元素遮挡 → 正文滚过会从工具栏上方透出来，即「镂空」。见 topics/03-editor.md）
  //
  // ⚠️ 系统设置页**不再**属于这一组：它没有任何 sticky 元素（页内无 position:sticky），
  // 却继承了 paddingTop:0，导致「系统设置」标题和右上角「保存设置」按钮直接贴在卡片顶边。
  // 只有当页面上真有吸顶条时才需要清零上下内边距 —— 新增页面别顺手加进来。
  const isEditPage = useMemo(
    () => /^\/(tutorials|nodes|resources|plugins)(\/\d+\/edit|\/new)$/.test(location.pathname),
    [location.pathname]
  )

  // 看板类页面直接在画布上铺卡片，不套白色页面卡（与 gi-demo 首页一致）
  const isCanvasPage =
    location.pathname === '/' ||
    location.pathname === '/dashboard' ||
    location.pathname === '/analysis'

  // ---- 多标签页：路径 key + 菜单/页头标题，localStorage 持久化（与演示站 worktab 一致） ----
  const pageTitles = usePageTitleStore((s) => s.titles)
  const clearTitle = usePageTitleStore((s) => s.clearTitle)
  const menuLabels = useMemo(() => collectMenuLabels(visibleTree), [visibleTree])
  const [tabs, setTabs] = useState<string[]>(() =>
    typeof window === 'undefined' ? [HOME_TAB] : readTabs(window.localStorage)
  )
  const [pinned, setPinned] = useState<string[]>(() =>
    typeof window === 'undefined' ? [] : readPinnedTabs(window.localStorage)
  )
  const [refreshKey, setRefreshKey] = useState(0)

  // 固定标签必须仍存在于标签栏；关闭后自动清理，避免持久化里留死键
  useEffect(() => {
    setPinned((prev) => {
      const next = prev.filter((key) => tabs.includes(key))
      if (next.length === prev.length) return prev
      writePinnedTabs(window.localStorage, next)
      return next
    })
  }, [tabs])

  useEffect(() => {
    setTabs((prev) => {
      const next = addTab(prev, location.pathname)
      if (next.length === prev.length && next.every((key, index) => key === prev[index])) return prev
      writeTabs(window.localStorage, next)
      return next
    })
  }, [location.pathname])

  // 权限/菜单变化后丢弃已不存在的入口；编辑页等子路由靠父菜单前缀保留
  useEffect(() => {
    if (!menuTree) return
    setTabs((prev) => {
      const menuKeys = [...menuLabels.keys()]
      const known = (key: string) =>
        key === HOME_TAB ||
        menuLabels.has(key) ||
        menuKeys.some((menuKey) => menuKey !== HOME_TAB && key.startsWith(`${menuKey}/`)) ||
        !!usePageTitleStore.getState().titles[key]
      const next = prev.filter(known)
      if (next.length === prev.length) return prev
      writeTabs(window.localStorage, next)
      return next
    })
  }, [menuLabels, menuTree])

  const tabTitle = useCallback(
    (key: string) =>
      pageTitles[key] ??
      menuLabels.get(key) ??
      menuLabels.get(resolveSelectedKey(visibleTree, key)) ??
      t('tabs.untitled'),
    [pageTitles, menuLabels, visibleTree, t]
  )

  /** 固定标签在「关闭其他 / 全部」时不被误删（与演示站一致） */
  const mergePinned = (kept: string[]) => {
    const set = new Set(kept)
    pinned.forEach((key) => {
      if (tabs.includes(key)) set.add(key)
    })
    return tabs.filter((key) => set.has(key))
  }

  const commitTabs = (next: string[], removed: string[]) => {
    removed.forEach(clearTitle)
    setTabs(next)
    writeTabs(window.localStorage, next)
  }

  const handleCloseTab = (key: string) => {
    if (key === HOME_TAB) return
    const nextActive = nextTabAfterClose(tabs, key, location.pathname)
    const next = removeTab(tabs, key)
    commitTabs(next, [key])
    if (key === location.pathname && nextActive) navigate(nextActive)
  }

  const handleCloseOthers = (key: string) => {
    const next = mergePinned(closeOtherTabs(tabs, key))
    commitTabs(next, tabs.filter((item) => !next.includes(item)))
  }

  const handleCloseLeft = (key: string) => {
    const next = closeLeftTabs(tabs, key, pinned)
    commitTabs(next, tabs.filter((item) => !next.includes(item)))
  }

  const handleCloseRight = (key: string) => {
    const next = closeRightTabs(tabs, key, pinned)
    commitTabs(next, tabs.filter((item) => !next.includes(item)))
  }

  const handleCloseAll = () => {
    const next = mergePinned(closeAllTabs())
    commitTabs(next, tabs.filter((item) => !next.includes(item)))
    if (!next.includes(location.pathname)) navigate(HOME_TAB)
  }

  const handleRefreshTab = (key: string = location.pathname) => {
    if (key !== location.pathname) navigate(key)
    setRefreshKey((value) => value + 1)
  }

  const handleTogglePin = (key: string) => {
    if (key === HOME_TAB) return
    const next = pinned.includes(key) ? pinned.filter((item) => item !== key) : [...pinned, key]
    setPinned(next)
    writePinnedTabs(window.localStorage, next)
  }

  const handleToggleFullscreen = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
      else await document.documentElement.requestFullscreen()
    } catch {
      // 浏览器拒绝（非用户手势 / 被策略禁用）时保持当前状态即可，不弹错误打断操作
    }
  }

  /**
   * 锁屏：口令存服务端（bcrypt），校验走后端并签发 5 分钟解锁令牌；
   * 令牌只放内存，刷新/sessionStorage 仍标记锁定，因此刷新后必须重新解锁。
   */
  const enterLocked = () => {
    setUnlockInput('')
    unlockTokenRef.current = null
    try {
      window.sessionStorage.setItem('adm-locked', '1')
    } catch {
      // 隐私模式写不进去时仍锁定当前页面，只是刷新后解除
    }
    setLocked(true)
  }

  const handleLock = () => {
    // 已设置口令：直接锁屏；首次使用才弹「设置口令」
    if (hasLockPassword) {
      enterLocked()
      return
    }
    setLockDraft('')
    setLockSetupOpen(true)
  }

  const handleConfirmLock = async () => {
    const value = lockDraft.trim()
    if (value.length < 4) {
      toast.warning(t('layout.lockPasswordRequired'))
      return
    }
    if (lockBusy) return
    setLockBusy(true)
    try {
      await saveLockPassword(value)
      markLockPasswordSet()
      setLockDraft('')
      setLockSetupOpen(false)
      enterLocked()
    } catch {
      toast.error(t('layout.lockSaveFailed'))
    } finally {
      setLockBusy(false)
    }
  }

  const handleUnlock = async () => {
    const value = unlockInput.trim()
    if (!value) {
      toast.warning(t('layout.lockPasswordRequired'))
      return
    }
    if (lockBusy) return
    setLockBusy(true)
    try {
      const data = await verifyLockPassword(value)
      unlockTokenRef.current = data.unlock_token
      setUnlockInput('')
      try {
        window.sessionStorage.removeItem('adm-locked')
      } catch {
        // 同上
      }
      setLocked(false)
    } catch (error) {
      const status = (error as { response?: { status?: number } })?.response?.status
      toast.error(status === 429 ? t('layout.lockTooMany') : t('layout.wrongPassword'))
    } finally {
      setLockBusy(false)
    }
  }

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  const userMenu: MenuProps = {
    items: [
      // 个人中心：顶栏账号下拉里的快捷入口（与左侧菜单同一路由）
      { key: 'profile', icon: <UserOutlined />, label: t('layout.profile'), onClick: () => navigate('/profile') },
      { type: 'divider' },
      { key: 'lock', icon: <LockOutlined />, label: t('layout.lock'), onClick: handleLock },
      { key: 'logout', icon: <LogoutOutlined />, label: t('layout.logout'), onClick: handleLogout },
    ],
  }

  /** 语言：切换后菜单会按新语言重拉（见上方 useEffect 的 locale 依赖） */
  const languageMenu: MenuProps = {
    items: [
      { key: 'zh-CN', label: LOCALE_LABELS['zh-CN'] },
      { key: 'en-US', label: LOCALE_LABELS['en-US'] },
    ],
    selectedKeys: [locale],
    onClick: ({ key }) => setLocale(key as Locale),
  }

  /** 组件尺寸：演示站右上角下拉（默认 / 大型 / 小型），同时写进 ConfigProvider */
  const sizeMenu: MenuProps = {
    items: [
      { key: 'default', label: t('layout.sizeDefault') },
      { key: 'large', label: t('layout.sizeLarge') },
      { key: 'small', label: t('layout.sizeSmall') },
    ],
    selectedKeys: [componentSize],
    onClick: ({ key }) => setPrefs({ componentSize: key as ComponentSize }),
  }

  /** 快速入口：取菜单树前 8 个可跳转叶子（演示站 8 宫格；首页「工作台」排第一） */
  const quickEntries = useMemo(
    () =>
      [...visibleTree.top, ...visibleTree.groups.flatMap((group) => group.items)]
        .filter((item) => !item.url)
        .slice(0, 8),
    [visibleTree]
  )

  const quickEntryContent = (
    <div className="app-quick-entry">
      {quickEntries.length === 0 ? (
        <Typography.Text type="secondary">{t('layout.quickEntryEmpty')}</Typography.Text>
      ) : (
        quickEntries.map((item) => (
          <button
            key={item.key}
            type="button"
            className="app-quick-entry__item"
            onClick={() => {
              setQuickOpen(false)
              navigate(item.key)
            }}
          >
            <span className="app-quick-entry__icon">{menuIcon(item.icon)}</span>
            <span className="app-quick-entry__label">{item.label}</span>
          </button>
        ))
      )}
    </div>
  )

  // 新手引导：开关打开、服务端/本地都没完成、菜单已就绪时才自动弹出
  useEffect(() => {
    if (!prefsReady || !showGuide || guideDone) return
    if (visibleTree.groups.length === 0) return
    setGuideOpen(true)
  }, [prefsReady, showGuide, guideDone, visibleTree])

  const closeGuide = () => {
    setGuideOpen(false)
    // 完成态并入偏好：随防抖 PUT 同步到服务端，换浏览器登录也不再重复弹
    setPrefs({ guideDone: true })
  }

  const headerActions = (
    <Space size={4}>
      {showMenuSearch && (
        <Button
          type="text"
          icon={<SearchOutlined />}
          aria-label={t('layout.search')}
          title={t('layout.searchTitle')}
          onClick={() => setPaletteOpen(true)}
        />
      )}
      {showRefreshButton && (
        <Button
          type="text"
          icon={<ReloadOutlined />}
          aria-label={t('layout.refresh')}
          title={t('layout.refresh')}
          onClick={() => handleRefreshTab()}
        />
      )}
      {showFastEnter && !isMobile && (
        <Popover
          content={quickEntryContent}
          placement="bottomRight"
          trigger="click"
          title={t('layout.quickEntry')}
          open={quickOpen}
          onOpenChange={setQuickOpen}
        >
          <Button
            type="text"
            icon={<AppstoreOutlined />}
            aria-label={t('layout.quickEntry')}
            title={t('layout.quickEntry')}
          />
        </Popover>
      )}
      {showNotification && <TodoBell />}
      {!isMobile && (
        <>
          {showFullscreen && (
            <Button
              type="text"
              icon={isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
              aria-label={isFullscreen ? t('layout.exitFullscreen') : t('layout.fullscreen')}
              title={isFullscreen ? t('layout.exitFullscreen') : t('layout.fullscreen')}
              onClick={() => void handleToggleFullscreen()}
            />
          )}
          {showSizeSelect && (
            <Dropdown menu={sizeMenu} placement="bottomRight" trigger={['click']}>
              <Button
                type="text"
                icon={<FontSizeOutlined />}
                aria-label={t('layout.size')}
                title={t('layout.size')}
              />
            </Dropdown>
          )}
          {showLanguage && (
            // 提示用原生 title 而非 Tooltip：Dropdown 会 cloneElement 给触发元素绑事件，
            // 中间再套一层 Tooltip 会吃掉这些事件
            <Dropdown menu={languageMenu} placement="bottomRight" trigger={['click']}>
              <Button
                type="text"
                icon={<TranslationOutlined />}
                aria-label={t('layout.language')}
                title={t('layout.language')}
              />
            </Dropdown>
          )}
          {/* 深色主题一键切换：与演示站右上角月亮/太阳按钮一致 */}
          <Button
            type="text"
            icon={resolvedTheme === 'dark' ? <SunOutlined /> : <MoonOutlined />}
            aria-label={t('layout.theme')}
            title={resolvedTheme === 'dark' ? t('layout.light') : t('layout.dark')}
            onClick={() => setMode(resolvedTheme === 'dark' ? 'light' : 'dark')}
          />
          <Button
            type="text"
            icon={<LockOutlined />}
            aria-label={t('layout.lock')}
            title={t('layout.lock')}
            onClick={handleLock}
          />
        </>
      )}
      <Button
        id="app-settings-trigger"
        type="text"
        icon={<SettingOutlined />}
        aria-label={t('layout.settings')}
        title={t('layout.settings')}
        onClick={() => setSettingsOpen(true)}
      />
      <Dropdown menu={userMenu} placement="bottomRight">
        <Space style={{ cursor: 'pointer' }}>
          <Avatar icon={<UserOutlined />} src={avatarUrl || undefined} />
          {!isMobile && <Typography.Text>{account || t('layout.adminFallback')}</Typography.Text>}
        </Space>
      </Dropdown>
    </Space>
  )

  /** 菜单区三态：加载中（无缓存）/ 加载失败（无缓存）/ 正常 */
  const renderMenu = () => {
    if (!menuTree && menuLoading) {
      return <Skeleton active title={false} paragraph={{ rows: 6 }} style={{ padding: '16px 20px' }} />
    }
    if (!menuTree && menuError) {
      return (
        <div style={menuFallbackStyle}>
          {!collapsed && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t('layout.menuLoadFailed')}
            </Typography.Text>
          )}
          <div style={{ marginTop: collapsed ? 0 : 8 }}>
            <Tooltip title={collapsed ? t('layout.retryMenu') : undefined}>
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={() => setReloadToken((token) => token + 1)}
              >
                {collapsed ? null : t('common.retry')}
              </Button>
            </Tooltip>
          </div>
        </div>
      )
    }
    return (
      <Menu
        mode={isTop ? 'horizontal' : 'inline'}
        theme={isMenuDark ? 'dark' : 'light'}
        selectedKeys={[selectedKey]}
        // 横向菜单由 antd 自行管理 hover 展开，不需要（也不能）传受控 openKeys
        openKeys={isTop ? undefined : openKeys}
        // 手风琴：同时只展开一个分组（判据见 utils/menu.ts::nextOpenKeys）；
        // 关闭后可同时展开多个分组
        onOpenChange={
          isTop
            ? undefined
            : (keys) =>
                setOpenKeys((prev) =>
                  accordion ? nextOpenKeys(prev, keys as string[]) : (keys as string[]),
                )
        }
        items={menuItems}
        onClick={({ key }) => {
          // 外链菜单由 <a> 新窗口打开、分组节点由 antd 自行展开，均不改变当前 SPA 路由
          if (externalKeys.has(key) || key.startsWith(GROUP_KEY_PREFIX)) return
          navigate(key)
          if (isMobile) setMobileMenuOpen(false)
        }}
        style={
          isTop
            ? { background: 'transparent', borderBottom: 0, minWidth: 0 }
            : { borderRight: 0 }
        }
      />
    )
  }

  /** 混合 / 双列布局的二级菜单：只渲染当前分组，分组切换由顶栏或左列完成 */
  const renderFocusedMenu = () => {
    if (!menuTree && menuLoading) {
      return <Skeleton active title={false} paragraph={{ rows: 6 }} style={{ padding: '16px 20px' }} />
    }
    if (!menuTree && menuError) {
      return (
        <div style={menuFallbackStyle}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t('layout.menuLoadFailed')}
          </Typography.Text>
          <div style={{ marginTop: 8 }}>
            <Button size="small" icon={<ReloadOutlined />} onClick={() => setReloadToken((token) => token + 1)}>
              {t('common.retry')}
            </Button>
          </div>
        </div>
      )
    }
    return (
      <Menu
        mode="inline"
        theme={isMenuDark ? 'dark' : 'light'}
        selectedKeys={[selectedKey]}
        items={focusedMenuItems}
        onClick={({ key }) => {
          if (externalKeys.has(key)) return
          navigate(key)
        }}
        style={{ borderRight: 0 }}
      />
    )
  }

  /** 双列布局左列：一级分组（图标 + 名称），点击即切换右侧二级菜单 */
  const renderGroupRail = () => (
    <div className="app-group-rail">
      {visibleTree.groups.map((group) => {
        const key = groupMenuKey(group.key)
        const active = key === focusGroupKey
        return (
          <button
            key={key}
            type="button"
            className={`app-group-rail__item${active ? ' is-active' : ''}`}
            title={group.label}
            aria-pressed={active}
            onClick={() => activateGroup(key)}
          >
            <span className="app-group-rail__icon">{menuIcon(group.icon)}</span>
            <span>{group.label}</span>
          </button>
        )
      })}
    </div>
  )

  const shellClass = [
    'app-shell',
    `app-shell--${navLayout}`,
    boxStyle === 'shadow' ? 'app-shell--box-shadow' : '',
    pageWidth === 'fixed' ? 'app-shell--fixed-width' : '',
    isMenuDark ? 'app-shell--menu-dark' : '',
  ]
    .filter(Boolean)
    .join(' ')

  // 菜单深色时侧栏/顶栏同步换成深色底，否则菜单文字与背景会打架
  const shellSiderStyle: CSSProperties = isMenuDark
    ? { ...siderStyle, background: '#001529' }
    : siderStyle
  const shellHeaderStyle: CSSProperties = isMenuDark
    ? {
        ...(isTop ? topHeaderStyle : headerStyle),
        background: '#001529',
        borderBottomColor: 'rgba(255, 255, 255, 0.12)',
      }
    : isTop
      ? topHeaderStyle
      : headerStyle
  // 只有垂直布局可折叠；混合/双列始终展示完整结构
  const siderCollapsed = navLayout === 'side' && collapsed

  return (
    <Layout className={shellClass} style={{ height: '100vh', overflow: 'hidden' }}>
      <Drawer
        open={mobileMenuOpen}
        onClose={() => setMobileMenuOpen(false)}
        placement="left"
        size={260}
        title={t('layout.brand')}
        styles={{ body: { padding: 0 } }}
      >
        <div className="app-drawer-menu">
          <div className="app-sider-menu" style={menuScrollStyle}>{renderMenu()}</div>
        </div>
      </Drawer>
      {!isTop && !isMobile && (
        <Sider
          trigger={null}
          collapsible={navLayout === 'side'}
          collapsed={siderCollapsed}
          width={siderWidth}
          className="app-sider"
          style={shellSiderStyle}
        >
          {showAppLogo && (
            <div className={`app-logo${siderCollapsed ? ' is-collapsed' : ''}`}>
              <img className="app-logo__img" src={`${import.meta.env.BASE_URL}favicon-64.png`} alt="" />
              {!siderCollapsed && <span className="app-logo__text">{t('layout.brand')}</span>}
            </div>
          )}
          {isDouble ? (
            <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
              {renderGroupRail()}
              <div className="app-sider-menu" style={{ ...menuScrollStyle, flex: 1, width: 'auto' }}>{renderFocusedMenu()}</div>
            </div>
          ) : isMixed ? (
            <>
              <div className="app-mixed-title">{focusGroup?.label}</div>
              <div className="app-sider-menu" style={menuScrollStyle}>{renderFocusedMenu()}</div>
            </>
          ) : (
            <div className="app-sider-menu" style={menuScrollStyle}>{renderMenu()}</div>
          )}
        </Sider>
      )}
      <Layout
        style={{
          marginLeft: isMobile || isTop ? 0 : siderCollapsed ? 80 : siderWidth,
          height: '100vh',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <Header className="app-header" style={shellHeaderStyle}>
          {isTop ? (
            <>
              <div className="app-logo">
                {showAppLogo && (
                  <>
                    <img className="app-logo__img" src={`${import.meta.env.BASE_URL}favicon-64.png`} alt="" />
                    <span className="app-logo__text">{t('layout.brand')}</span>
                  </>
                )}
              </div>
              <div style={topMenuWrapStyle}>{renderMenu()}</div>
              {headerActions}
            </>
          ) : isMixed ? (
            <>
              {/* 混合布局的 Logo 在侧栏顶部，这里不再重复渲染 */}
              <div style={{ ...topMenuWrapStyle, paddingLeft: 16 }}>
                <Menu
                  mode="horizontal"
                  theme={isMenuDark ? 'dark' : 'light'}
                  selectedKeys={focusGroupKey ? [focusGroupKey] : []}
                  items={groupMenuItems}
                  onClick={({ key }) => activateGroup(key)}
                  style={{ background: 'transparent', borderBottom: 0, minWidth: 0 }}
                />
              </div>
              {headerActions}
            </>
          ) : (
            <>
              <div style={headerLeftStyle}>
                {!isDouble && (isMobile || showMenuButton) && (
                  <button
                    type="button"
                    onClick={() => (isMobile ? setMobileMenuOpen(true) : setCollapsed(!collapsed))}
                    aria-label={
                      isMobile
                        ? t('layout.openMenu')
                        : collapsed
                          ? t('layout.expandSidebar')
                          : t('layout.collapseSidebar')
                    }
                    aria-expanded={isMobile ? mobileMenuOpen : !collapsed}
                    style={{ ...collapseTriggerStyle, background: 'none', border: 'none', color: 'inherit' }}
                  >
                    {isMobile || collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                  </button>
                )}
                {showCrumbs && currentCrumb && (
                  <Space size={4}>
                    {currentCrumb.group && (
                      <>
                        <Typography.Text type="secondary">{currentCrumb.group}</Typography.Text>
                        <Typography.Text type="secondary">/</Typography.Text>
                      </>
                    )}
                    <Typography.Text strong>{currentCrumb.label}</Typography.Text>
                  </Space>
                )}
              </div>
              {headerActions}
            </>
          )}
        </Header>
        {!isMobile && showTabs && (
          <PageTabs
            tabs={tabs}
            activeKey={location.pathname}
            getTitle={tabTitle}
            onChange={(key) => navigate(key)}
            onClose={handleCloseTab}
            onCloseOthers={handleCloseOthers}
            onCloseAll={handleCloseAll}
            onRefresh={() => handleRefreshTab()}
            tabStyle={tabStyle}
            pinned={pinned}
            onRefreshTab={handleRefreshTab}
            onTogglePin={handleTogglePin}
            onCloseLeft={handleCloseLeft}
            onCloseRight={handleCloseRight}
          />
        )}
        <Content
          className={[
            'app-content',
            isCanvasPage ? 'app-content--canvas' : '',
            isEditPage ? 'app-content--edit' : '',
          ]
            .filter(Boolean)
            .join(' ')}
        >
          {/* 页面级错误边界：某一页渲染挂了不该把侧栏一起带走（见组件注释）。
              同时承载「页面切换动画」：切路由/刷新时换 key 重放动画。 */}
          <div
            key={`${location.pathname}:${refreshKey}`}
            className={`app-page-transition${
              pageTransition === 'none' ? '' : ` app-page-transition--${pageTransition}`
            }`}
          >
            <RouteErrorBoundary path={location.pathname}>
              <Outlet />
            </RouteErrorBoundary>
          </div>
        </Content>
      </Layout>
      {showNprogress && (
        <div key={`${location.pathname}:${refreshKey}`} className="app-progress" aria-hidden="true" />
      )}
      {watermark && (
        <div
          className="app-watermark"
          aria-hidden="true"
          style={{
            backgroundImage: `url("data:image/svg+xml;utf8,${encodeURIComponent(
              `<svg xmlns="http://www.w3.org/2000/svg" width="220" height="140"><text x="10" y="90" fill="#909399" font-size="14" font-family="sans-serif" transform="rotate(-24 10 90)">${(account || 'AI Cat')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')}</text></svg>`
            )}")`,
          }}
        />
      )}
      {aiAssistant && !isMobile && (
        <Button
          type="primary"
          shape="circle"
          className="app-ai-float"
          icon={<RobotOutlined />}
          aria-label={t('layout.aiAssistant')}
          title={t('layout.aiAssistant')}
          onClick={() => navigate('/ai-playground')}
        />
      )}
      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        menuTree={visibleTree}
        permissions={permissions}
        isSuper={isSuper}
      />

      {/* 锁屏密码设置（演示站「锁定屏幕」弹窗） */}
      <Modal
        open={lockSetupOpen}
        title={t('layout.lockSetupTitle')}
        okText={t('layout.lockNow')}
        cancelText={t('common.cancel')}
        onOk={handleConfirmLock}
        confirmLoading={lockBusy}
        onCancel={() => setLockSetupOpen(false)}
        destroyOnHidden
      >
        <Typography.Paragraph type="secondary">{t('layout.lockSetupDesc')}</Typography.Paragraph>
        <Input.Password
          value={lockDraft}
          onChange={(event) => setLockDraft(event.target.value)}
          onPressEnter={() => void handleConfirmLock()}
          placeholder={t('layout.lockPasswordPlaceholder')}
          aria-label={t('layout.lockPassword')}
          autoFocus
        />
        <Typography.Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
          {t('layout.localOnlyHint')}
        </Typography.Text>
      </Modal>

      {/* 真锁屏遮罩：输入本机密码解锁；刷新后仍保持锁定（sessionStorage） */}
      {locked && (
        <div
          className="app-lock-screen"
          role="dialog"
          aria-modal="true"
          aria-label={t('layout.unlockTitle')}
        >
          <div className="app-lock-screen__card">
            <Avatar size={64} icon={<UserOutlined />} src={avatarUrl || undefined} />
            <Typography.Title level={4} style={{ margin: '12px 0 4px' }}>
              {account || t('layout.adminFallback')}
            </Typography.Title>
            <Typography.Text type="secondary">{t('layout.unlockDesc')}</Typography.Text>
            <Input.Password
              value={unlockInput}
              onChange={(event) => setUnlockInput(event.target.value)}
              onPressEnter={() => void handleUnlock()}
              placeholder={t('layout.lockPasswordPlaceholder')}
              aria-label={t('layout.lockPassword')}
              style={{ marginTop: 16 }}
              disabled={lockBusy}
              autoFocus
            />
            <Button
              type="primary"
              block
              style={{ marginTop: 12 }}
              loading={lockBusy}
              onClick={() => void handleUnlock()}
            >
              {t('layout.unlock')}
            </Button>
            <Button type="link" onClick={handleLogout}>
              {t('layout.logout')}
            </Button>
          </div>
        </div>
      )}

      {/* 新手引导（设置项「登录后开启新手引导」） */}
      {guideOpen && (
        <Tour
          open={guideOpen}
          onClose={closeGuide}
          steps={[
            {
              title: t('guide.step1Title'),
              description: t('guide.step1Desc'),
              target: () =>
                (document.querySelector('#app-settings-trigger') as HTMLElement) || document.body,
            },
            {
              title: t('guide.step2Title'),
              description: t('guide.step2Desc'),
              target: () => (document.querySelector('.app-sider') as HTMLElement) || document.body,
            },
            {
              title: t('guide.step3Title'),
              description: t('guide.step3Desc'),
              target: () => (document.querySelector('.app-content') as HTMLElement) || document.body,
            },
          ]}
        />
      )}
    </Layout>
  )
}
