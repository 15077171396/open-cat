import { useCallback, useEffect, useMemo, useState } from 'react'
import { Drawer, Grid, Layout, Menu, Avatar, Dropdown, Typography, Space, Skeleton, Button, Tooltip } from 'antd'
import type { MenuProps } from 'antd'
import type { CSSProperties } from 'react'
import {
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  LogoutOutlined,
  UserOutlined,
  ReloadOutlined,
  SearchOutlined,
  BulbOutlined,
  LayoutOutlined,
  TranslationOutlined,
} from '@ant-design/icons'
import { useNavigate, useLocation, Outlet } from 'react-router-dom'
import { useAuthStore } from '../../store/authStore'
import { usePrefsStore } from '../../store/prefsStore'
import type { LayoutMode, ThemeMode } from '../../store/prefsStore'
import { apiGet } from '../../api/client'
import RouteErrorBoundary from '../RouteErrorBoundary'
import TodoBell from './TodoBell'
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
  closeOtherTabs,
  nextTabAfterClose,
  readTabs,
  removeTab,
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
const MENU_CACHE_VERSION = 1
/** 首次加载失败后的自动重试延迟（再失败才交给用户手动重试） */
const MENU_RETRY_DELAY_MS = 1500

const EMPTY_TREE: MenuTree = { groups: [], footer: [] }

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
  overflowY: 'auto',
  overflowX: 'hidden',
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
  borderBottom: '1px solid var(--app-border-light)',
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
  const [collapsed, setCollapsed] = useState(false)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)

  // 展示偏好：独立 store + localStorage 持久化，退出登录不丢
  const mode = usePrefsStore((s) => s.mode)
  const layout = usePrefsStore((s) => s.layout)
  const setMode = usePrefsStore((s) => s.setMode)
  const setLayout = usePrefsStore((s) => s.setLayout)
  const { locale, setLocale, t } = useI18n()
  const screens = Grid.useBreakpoint()
  // md=768：手机/小平板改用抽屉菜单；首帧 screens 为空时按桌面处理，避免桌面闪一下移动布局
  const isMobile = screens.md === false
  const isTop = layout === 'top' && !isMobile

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
    const toLeaf = (leaf: MenuLeaf) => ({
      key: leaf.key,
      icon: menuIcon(leaf.icon),
      // 外链交给 <a> 自己开新窗口，onClick 里直接 return（见下方 Menu onClick）
      label: leaf.url ? (
        <a href={leaf.url} target="_blank" rel="noreferrer">
          {leaf.label}
        </a>
      ) : (
        leaf.label
      ),
    })
    const groups = visibleTree.groups.map((group) => ({
      key: groupMenuKey(group.key),
      icon: menuIcon(group.icon),
      label: group.label,
      children: group.items.map(toLeaf),
      // 折叠态只剩图标，靠主色标出「当前页面所在分组」，否则用户不知道自己在哪一层
      className:
        collapsed && !isTop && !isMobile && groupMenuKey(group.key) === activeGroupKey
          ? 'adm-menu-group-active'
          : undefined,
    }))
    const footer = visibleTree.footer.map(toLeaf)
    return (footer.length > 0
      ? [...groups, { type: 'divider' as const }, ...footer]
      : groups) as MenuProps['items']
  }, [visibleTree, collapsed, activeGroupKey, isTop, isMobile])

  // 顶栏当前位置（父级 / 页面）：折叠侧栏后仍能判断自己在哪一层
  const currentCrumb = useMemo(() => {
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
    location.pathname === '/workplace' ||
    location.pathname === '/analysis'

  // ---- 多标签页：路径 key + 菜单/页头标题，sessionStorage 持久化 ----
  const pageTitles = usePageTitleStore((s) => s.titles)
  const clearTitle = usePageTitleStore((s) => s.clearTitle)
  const menuLabels = useMemo(() => collectMenuLabels(visibleTree), [visibleTree])
  const [tabs, setTabs] = useState<string[]>(() =>
    typeof window === 'undefined' ? [HOME_TAB] : readTabs(window.sessionStorage)
  )
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    setTabs((prev) => {
      const next = addTab(prev, location.pathname)
      if (next.length === prev.length && next.every((key, index) => key === prev[index])) return prev
      writeTabs(window.sessionStorage, next)
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
      writeTabs(window.sessionStorage, next)
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

  const handleCloseTab = (key: string) => {
    const nextActive = nextTabAfterClose(tabs, key, location.pathname)
    const next = removeTab(tabs, key)
    setTabs(next)
    writeTabs(window.sessionStorage, next)
    clearTitle(key)
    if (key === location.pathname && nextActive) navigate(nextActive)
  }

  const handleCloseOthers = (key: string) => {
    const next = closeOtherTabs(tabs, key)
    tabs.filter((item) => !next.includes(item)).forEach(clearTitle)
    setTabs(next)
    writeTabs(window.sessionStorage, next)
  }

  const handleCloseAll = () => {
    tabs.filter((item) => item !== HOME_TAB).forEach(clearTitle)
    const next = closeAllTabs()
    setTabs(next)
    writeTabs(window.sessionStorage, next)
    navigate(HOME_TAB)
  }

  const handleRefreshTab = () => setRefreshKey((value) => value + 1)

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  const userMenu: MenuProps = {
    items: [
      // 个人中心：顶栏账号下拉里的快捷入口（与左侧菜单同一路由）
      { key: 'profile', icon: <UserOutlined />, label: t('layout.profile'), onClick: () => navigate('/profile') },
      { type: 'divider' },
      { key: 'logout', icon: <LogoutOutlined />, label: t('layout.logout'), onClick: handleLogout },
    ],
  }

  /** 主题：亮 / 暗 / 跟随系统（system 由 main.tsx 的 matchMedia 实时解析） */
  const themeMenu: MenuProps = {
    items: [
      { key: 'light', label: t('layout.light') },
      { key: 'dark', label: t('layout.dark') },
      { key: 'system', label: t('layout.system') },
    ],
    selectedKeys: [mode],
    onClick: ({ key }) => setMode(key as ThemeMode),
  }

  /** 布局：侧边导航 / 顶部导航，与主题同属展示偏好 */
  const layoutMenu: MenuProps = {
    items: [
      { key: 'side', label: t('layout.side') },
      { key: 'top', label: t('layout.top') },
    ],
    selectedKeys: [layout],
    onClick: ({ key }) => setLayout(key as LayoutMode),
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

  const headerActions = (
    <Space size={4}>
      <Button
        type="text"
        icon={<SearchOutlined />}
        aria-label={t('layout.search')}
        title={t('layout.searchTitle')}
        onClick={() => setPaletteOpen(true)}
      />
      <TodoBell />
      {/* 手机顶栏只留搜索 / 待办 / 头像；主题、布局、语言在命令面板里仍可用 */}
      {!isMobile && (
        <>
          {/* 提示用原生 title 而非 Tooltip：Dropdown 会 cloneElement 给触发元素绑事件，
              中间再套一层 Tooltip 会吃掉这些事件 */}
          <Dropdown menu={languageMenu} placement="bottomRight" trigger={['click']}>
            <Button
              type="text"
              icon={<TranslationOutlined />}
              aria-label={t('layout.language')}
              title={t('layout.language')}
            />
          </Dropdown>
          <Dropdown menu={themeMenu} placement="bottomRight" trigger={['click']}>
            <Button type="text" icon={<BulbOutlined />} aria-label={t('layout.theme')} title={t('layout.theme')} />
          </Dropdown>
          <Dropdown menu={layoutMenu} placement="bottomRight" trigger={['click']}>
            <Button type="text" icon={<LayoutOutlined />} aria-label={t('layout.layout')} title={t('layout.layout')} />
          </Dropdown>
        </>
      )}
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
        selectedKeys={[selectedKey]}
        // 横向菜单由 antd 自行管理 hover 展开，不需要（也不能）传受控 openKeys
        openKeys={isTop ? undefined : openKeys}
        // 手风琴：同时只展开一个分组（判据见 utils/menu.ts::nextOpenKeys）
        onOpenChange={isTop ? undefined : (keys) => setOpenKeys((prev) => nextOpenKeys(prev, keys as string[]))}
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

  return (
    <Layout style={{ height: '100vh', overflow: 'hidden' }}>
      <Drawer
        open={mobileMenuOpen}
        onClose={() => setMobileMenuOpen(false)}
        placement="left"
        width={260}
        title={t('layout.brand')}
        styles={{ body: { padding: 0 } }}
      >
        <div style={menuScrollStyle}>{renderMenu()}</div>
      </Drawer>
      {!isTop && !isMobile && (
        <Sider trigger={null} collapsible collapsed={collapsed} width={220} style={siderStyle}>
          <div className={`app-logo${collapsed ? ' is-collapsed' : ''}`}>
            <img className="app-logo__img" src={`${import.meta.env.BASE_URL}favicon-64.png`} alt="" />
            {!collapsed && <span className="app-logo__text">{t('layout.brand')}</span>}
          </div>
          <div style={menuScrollStyle}>{renderMenu()}</div>
        </Sider>
      )}
      <Layout
        style={{
          marginLeft: isMobile ? 0 : isTop ? 0 : collapsed ? 80 : 220,
          height: '100vh',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <Header className="app-header" style={isTop ? topHeaderStyle : headerStyle}>
          {isTop ? (
            <>
              <div className="app-logo">
                <img className="app-logo__img" src={`${import.meta.env.BASE_URL}favicon-64.png`} alt="" />
                <span className="app-logo__text">{t('layout.brand')}</span>
              </div>
              <div style={topMenuWrapStyle}>{renderMenu()}</div>
              {headerActions}
            </>
          ) : (
            <>
              <div style={headerLeftStyle}>
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
                {currentCrumb && (
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
        {!isMobile && (
          <PageTabs
            tabs={tabs}
            activeKey={location.pathname}
            getTitle={tabTitle}
            onChange={(key) => navigate(key)}
            onClose={handleCloseTab}
            onCloseOthers={handleCloseOthers}
            onCloseAll={handleCloseAll}
            onRefresh={handleRefreshTab}
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
          {/* 页面级错误边界：某一页渲染挂了不该把侧栏一起带走（见组件注释） */}
          <RouteErrorBoundary key={`${location.pathname}:${refreshKey}`} path={location.pathname}>
            <Outlet />
          </RouteErrorBoundary>
        </Content>
      </Layout>
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        menuTree={visibleTree}
        permissions={permissions}
        isSuper={isSuper}
      />
    </Layout>
  )
}
