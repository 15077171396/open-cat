import { useEffect, useMemo, useState } from 'react'
import { Layout, Menu, Avatar, Dropdown, Typography, Space, Skeleton, Button, Tooltip } from 'antd'
import type { MenuProps } from 'antd'
import type { CSSProperties } from 'react'
import {
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  LogoutOutlined,
  UserOutlined,
  ReloadOutlined,
  BulbOutlined,
  LayoutOutlined,
} from '@ant-design/icons'
import { useNavigate, useLocation, Outlet } from 'react-router-dom'
import { useAuthStore } from '../../store/authStore'
import { usePrefsStore } from '../../store/prefsStore'
import type { LayoutMode, ThemeMode } from '../../store/prefsStore'
import { apiGet } from '../../api/client'
import { BRAND } from '../../theme'
import RouteErrorBoundary from '../RouteErrorBoundary'
import { menuIcon } from '../../constants/menuIcons'
import {
  GROUP_KEY_PREFIX,
  externalLeafKeys,
  filterMenuTree,
  groupMenuKey,
  nextOpenKeys,
  resolveOpenGroupKey,
  resolveSelectedKey,
} from '../../utils/menu'
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

function readMenuCache(): MenuTree | null {
  try {
    const raw = sessionStorage.getItem(MENU_CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { v?: number; tree?: MenuTree }
    if (parsed?.v !== MENU_CACHE_VERSION || !parsed.tree || !Array.isArray(parsed.tree.groups)) {
      return null
    }
    return parsed.tree
  } catch {
    return null
  }
}

function writeMenuCache(tree: MenuTree): void {
  try {
    sessionStorage.setItem(MENU_CACHE_KEY, JSON.stringify({ v: MENU_CACHE_VERSION, tree }))
  } catch {
    // 隐私模式/配额满：缓存只是可选优化，写不进去不影响主流程
  }
}

// 提取内联样式为模块级常量，避免每次渲染创建新引用
// 尺寸与圆角遵循设计规范（docs/07-开源文档/06-UI设计规范.md）：顶栏 56 / 内边距 16 / 卡片圆角 12
// 颜色一律用 CSS 变量（tokens.css），暗色主题下由 [data-theme='dark'] 覆盖，不写死十六进制
const siderStyle: CSSProperties = {
  background: 'var(--app-card)',
  borderRight: '1px solid var(--app-border)',
  position: 'fixed',
  left: 0,
  top: 0,
  height: '100vh',
  zIndex: 10,
  display: 'flex',
  flexDirection: 'column',
}

const logoStyle: CSSProperties = {
  height: 56,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  borderBottom: '1px solid var(--app-border)',
  flex: 'none',
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
  borderBottom: '1px solid var(--app-border)',
  flex: 'none',
  position: 'sticky',
  top: 0,
  zIndex: 1,
}

/** 顶栏布局：logo 与操作区固定，中间的横向菜单吃掉剩余宽度 */
const topHeaderStyle: CSSProperties = { ...headerStyle, gap: 8 }
const topLogoStyle: CSSProperties = { ...logoStyle, borderBottom: 'none', paddingRight: 8 }
const topMenuWrapStyle: CSSProperties = { flex: 1, minWidth: 0, overflow: 'hidden' }

const headerLeftStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
}

const contentStyle: CSSProperties = {
  flex: 1,
  overflow: 'auto',
  margin: 16,
  padding: 16,
  background: 'var(--app-card)',
  // 与卡片圆角一致：内容区本身就是一张大卡片，页面内的卡片叠在其上时靠间距区分
  borderRadius: 12,
}

const contentStyleZeroVerticalPadding: CSSProperties = {
  ...contentStyle,
  paddingTop: 0,
  paddingBottom: 0,
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

  // 展示偏好：独立 store + localStorage 持久化，退出登录不丢
  const mode = usePrefsStore((s) => s.mode)
  const layout = usePrefsStore((s) => s.layout)
  const setMode = usePrefsStore((s) => s.setMode)
  const setLayout = usePrefsStore((s) => s.setLayout)
  const isTop = layout === 'top'

  // 有缓存就先渲染（避免刷新时侧栏闪一下骨架），随后静默校准
  const [menuTree, setMenuTree] = useState<MenuTree | null>(() => readMenuCache())
  const [menuLoading, setMenuLoading] = useState(false)
  const [menuError, setMenuError] = useState(false)
  const [reloadToken, setReloadToken] = useState(0)
  const [openKeys, setOpenKeys] = useState<string[]>([])

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

  // 拉取菜单结构（失败自动重试一次，仍失败则交给用户手动重试）
  useEffect(() => {
    let alive = true
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    const load = async (attempt: number) => {
      setMenuLoading(true)
      try {
        const tree = await apiGet<MenuTree>('/admin/menus')
        if (!alive) return
        setMenuTree(tree)
        writeMenuCache(tree)
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
  }, [reloadToken])

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
        collapsed && !isTop && groupMenuKey(group.key) === activeGroupKey
          ? 'adm-menu-group-active'
          : undefined,
    }))
    const footer = visibleTree.footer.map(toLeaf)
    return (footer.length > 0
      ? [...groups, { type: 'divider' as const }, ...footer]
      : groups) as MenuProps['items']
  }, [visibleTree, collapsed, activeGroupKey, isTop])

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

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  const userMenu: MenuProps = {
    items: [
      // 个人中心：顶栏账号下拉里的快捷入口（与左侧菜单同一路由）
      { key: 'profile', icon: <UserOutlined />, label: '个人中心', onClick: () => navigate('/profile') },
      { type: 'divider' },
      { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: handleLogout },
    ],
  }

  /** 主题：亮 / 暗 / 跟随系统（system 由 main.tsx 的 matchMedia 实时解析） */
  const themeMenu: MenuProps = {
    items: [
      { key: 'light', label: '亮色' },
      { key: 'dark', label: '暗色' },
      { key: 'system', label: '跟随系统' },
    ],
    selectedKeys: [mode],
    onClick: ({ key }) => setMode(key as ThemeMode),
  }

  /** 布局：侧边导航 / 顶部导航，与主题同属展示偏好 */
  const layoutMenu: MenuProps = {
    items: [
      { key: 'side', label: '侧边导航' },
      { key: 'top', label: '顶部导航' },
    ],
    selectedKeys: [layout],
    onClick: ({ key }) => setLayout(key as LayoutMode),
  }

  const headerActions = (
    <Space size={4}>
      {/* 提示用原生 title 而非 Tooltip：Dropdown 会 cloneElement 给触发元素绑事件，
          中间再套一层 Tooltip 会吃掉这些事件 */}
      <Dropdown menu={themeMenu} placement="bottomRight" trigger={['click']}>
        <Button type="text" icon={<BulbOutlined />} aria-label="主题模式" title="主题模式" />
      </Dropdown>
      <Dropdown menu={layoutMenu} placement="bottomRight" trigger={['click']}>
        <Button type="text" icon={<LayoutOutlined />} aria-label="布局" title="布局" />
      </Dropdown>
      <Dropdown menu={userMenu} placement="bottomRight">
        <Space style={{ cursor: 'pointer' }}>
          <Avatar icon={<UserOutlined />} src={avatarUrl || undefined} />
          <Typography.Text>{account || '管理员'}</Typography.Text>
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
              菜单加载失败
            </Typography.Text>
          )}
          <div style={{ marginTop: collapsed ? 0 : 8 }}>
            <Tooltip title={collapsed ? '重新加载菜单' : undefined}>
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={() => setReloadToken((token) => token + 1)}
              >
                {collapsed ? null : '重试'}
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
      {!isTop && (
        <Sider trigger={null} collapsible collapsed={collapsed} width={220} style={siderStyle}>
          <div style={logoStyle}>
            <Typography.Title level={4} style={{ margin: 0, color: BRAND.primary }}>
              {collapsed ? '猫' : 'AI猫后台'}
            </Typography.Title>
          </div>
          <div style={menuScrollStyle}>{renderMenu()}</div>
        </Sider>
      )}
      <Layout
        style={{
          marginLeft: isTop ? 0 : collapsed ? 80 : 220,
          height: '100vh',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <Header style={isTop ? topHeaderStyle : headerStyle}>
          {isTop ? (
            <>
              <div style={topLogoStyle}>
                <Typography.Title level={4} style={{ margin: 0, color: BRAND.primary, whiteSpace: 'nowrap' }}>
                  AI猫后台
                </Typography.Title>
              </div>
              <div style={topMenuWrapStyle}>{renderMenu()}</div>
              {headerActions}
            </>
          ) : (
            <>
              <div style={headerLeftStyle}>
                <button
                  type="button"
                  onClick={() => setCollapsed(!collapsed)}
                  aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
                  aria-expanded={!collapsed}
                  style={{ ...collapseTriggerStyle, background: 'none', border: 'none', color: 'inherit' }}
                >
                  {collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
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
        <Content style={isEditPage ? contentStyleZeroVerticalPadding : contentStyle}>
          {/* 页面级错误边界：某一页渲染挂了不该把侧栏一起带走（见组件注释） */}
          <RouteErrorBoundary path={location.pathname}>
            <Outlet />
          </RouteErrorBoundary>
        </Content>
      </Layout>
    </Layout>
  )
}
