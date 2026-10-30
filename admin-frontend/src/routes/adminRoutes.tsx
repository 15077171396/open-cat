import { lazy, type ReactNode } from 'react'

/**
 * 管理端路由表（**唯一数据源**）。
 *
 * 为什么从 `App.tsx` 抽出来：路由此前是 34 个 lazy import + 37 个 `<Route>` 手写堆叠，
 * 与后端 `app/services/admin_menu.py` 的菜单项、`rbac_catalog.py` 的权限码靠人工对齐。
 * 三处任一处写错都只表现为「菜单点进去 403」或「有权限但没入口」，而且没有测试能发现。
 * 抽成表之后，`backend/tests/test_admin_routes.py` 可以跨语言校验三者的对应关系。
 *
 * 约定：
 * - `path` 必须与 `admin_menu.py` 里 `MenuItem.key` 逐字一致（外链除外，外链不走 SPA 路由）；
 * - `perm` 为单一权限码，`anyPerms` 为「任一命中即放行」，两者都必须是 `rbac_catalog.py`
 *   里真实存在的码；写错会让该页对**所有人**403（超管除外），且不会有任何报错；
 * - 两者都不给 = **不挂页面级守卫**。这类页面必须在测试的 `UNGUARDED_EXEMPT` 里登记并写明理由，
 *   否则测试变红 —— 目的是让「新页面忘了挂权限」变成一次失败的构建，而不是一次静默的裸奔。
 *
 * 新增页面：在下面加一条 + 在 `admin_menu.py` 加菜单项 + 在 `rbac_catalog.py` 登记权限码。
 * 三处漏一处，测试会告诉你是哪一处。
 */

// 性能：页面组件全部懒加载，入口 chunk 只保留 react 全家桶 + 零依赖的 ErrorBoundary / spinner，
// antd 等重组件随首个业务页面按需并行加载
const Dashboard = lazy(() => import('../pages/Dashboard'))
const WorkplacePage = lazy(() => import('../pages/Workplace'))
const Analysis = lazy(() => import('../pages/Analysis'))
const TutorialsList = lazy(() => import('../pages/Tutorials/List'))
const TutorialsEdit = lazy(() => import('../pages/Tutorials/Edit'))
const CategoriesList = lazy(() => import('../pages/Categories/List'))
const AnnouncementsList = lazy(() => import('../pages/Announcements/List'))
const UsersList = lazy(() => import('../pages/Users/List'))
const PointsList = lazy(() => import('../pages/Points/List'))
const RedeemCodesList = lazy(() => import('../pages/RedeemCodes/List'))
const AuthRequestsList = lazy(() => import('../pages/AuthRequests/List'))
const FeedbackList = lazy(() => import('../pages/Feedback/List'))
const MessagesList = lazy(() => import('../pages/Messages/List'))
const ModerationList = lazy(() => import('../pages/Moderation/List'))
const CommentsList = lazy(() => import('../pages/Comments/List'))
const SettingsPage = lazy(() => import('../pages/Settings'))
const MenuSettingsPage = lazy(() => import('../pages/MenuSettings'))
const DictionariesPage = lazy(() => import('../pages/Dictionaries'))
const PositionsPage = lazy(() => import('../pages/Positions'))
const MonitorOnlinePage = lazy(() => import('../pages/Monitor/Online'))
const MonitorServerPage = lazy(() => import('../pages/Monitor/Server'))
const MonitorCachePage = lazy(() => import('../pages/Monitor/Cache'))
const LoginSettingsPage = lazy(() => import('../pages/LoginSettings'))
const LegalDocumentsPage = lazy(() => import('../pages/LegalDocuments'))
const PaymentSettingsPage = lazy(() => import('../pages/PaymentSettings'))
const MailSettingsPage = lazy(() => import('../pages/MailSettings'))
const AiModelsPage = lazy(() => import('../pages/AiModels'))
const AiWritingPage = lazy(() => import('../pages/AiWriting'))
const PaymentProductsPage = lazy(() => import('../pages/PaymentProducts/List'))
const PaymentOrdersPage = lazy(() => import('../pages/PaymentOrders/List'))
const RolesList = lazy(() => import('../pages/Roles/List'))
const DepartmentsList = lazy(() => import('../pages/Departments/List'))
const AuditLogsList = lazy(() => import('../pages/AuditLogs/List'))
const RecycleBinPage = lazy(() => import('../pages/RecycleBin'))
const BackupsPage = lazy(() => import('../pages/Backups'))
const SearchKeywordsPage = lazy(() => import('../pages/SearchKeywords'))
const MaterialsPage = lazy(() => import('../pages/Materials'))
const ScheduledJobsPage = lazy(() => import('../pages/ScheduledJobs'))
const ReadinessPage = lazy(() => import('../pages/Readiness'))
const ProfilePage = lazy(() => import('../pages/Profile'))

export interface AdminRouteDef {
  /** SPA 路径，与后端菜单项的 key 一致 */
  path: string
  /** 页面组件（已懒加载包裹） */
  element: ReactNode
  /** 单一权限码 */
  perm?: string
  /** 任一命中即放行 */
  anyPerms?: string[]
}

/** 需要登录 + 权限守卫的页面路由（登录页 / 安装向导在 `App.tsx` 里单独挂，不在此表） */
export const ADMIN_ROUTES: AdminRouteDef[] = [
  { path: '/', element: <Dashboard />, perm: 'dashboard:view' },
  // 工作台：快捷入口 + 待办 + 最近动态，复用既有接口
  { path: '/workplace', element: <WorkplacePage />, perm: 'dashboard:view' },
  // 数据分析：全页演示数据（不请求后端），权限与数据看板同层级
  { path: '/analysis', element: <Analysis />, perm: 'dashboard:view' },
  { path: '/tutorials', element: <TutorialsList />, perm: 'tutorial:view' },
  // 新建与编辑是列表页的子页面，菜单只挂列表入口，因此不进菜单覆盖校验
  { path: '/tutorials/new', element: <TutorialsEdit />, perm: 'tutorial:create' },
  { path: '/tutorials/:id/edit', element: <TutorialsEdit />, perm: 'tutorial:edit' },
  { path: '/categories', element: <CategoriesList />, perm: 'category:view' },
  { path: '/announcements', element: <AnnouncementsList />, perm: 'announcement:view' },
  { path: '/payment-products', element: <PaymentProductsPage />, perm: 'payment_product:view' },
  { path: '/payment-orders', element: <PaymentOrdersPage />, perm: 'payment_order:view' },
  { path: '/users', element: <UsersList />, perm: 'user:view' },
  { path: '/points', element: <PointsList />, perm: 'points:view' },
  { path: '/redeem-codes', element: <RedeemCodesList />, perm: 'redeem_code:view' },
  { path: '/auth-requests', element: <AuthRequestsList />, perm: 'auth_request:view' },
  { path: '/feedback', element: <FeedbackList />, perm: 'feedback:view' },
  { path: '/messages', element: <MessagesList />, perm: 'message:view' },
  { path: '/search-keywords', element: <SearchKeywordsPage />, perm: 'dashboard:view' },
  // 素材库归内容域：此前这里还列了 resource/plugin/node 三个已随模块裁剪删除的权限码，
  // 属于「守卫引用了不存在的码」——功能上被 tutorial:view 兜住了，但语义是错的。
  { path: '/materials', element: <MaterialsPage />, perm: 'tutorial:view' },
  // 机审留痕与人工举报同属内容审核页，任一查看权限即可进入
  { path: '/moderation', element: <ModerationList />, anyPerms: ['moderation:view', 'report:view'] },
  { path: '/comments', element: <CommentsList />, perm: 'comment:view' },
  { path: '/settings', element: <SettingsPage />, perm: 'setting:view' },
  // 菜单管理：只调整展示层，路由与权限码仍由代码同源定义；复用 setting 权限
  { path: '/menu-settings', element: <MenuSettingsPage />, perm: 'setting:view' },
  // 数据字典：业务枚举的类型与字典项；与菜单管理同属系统配置层
  { path: '/dicts', element: <DictionariesPage />, perm: 'setting:view' },
  // 登录管理：从「系统设置 → 登录与短信」抽出的独立模块，权限码独立
  { path: '/login-settings', element: <LoginSettingsPage />, perm: 'login_setting:view' },
  // 法律文档：对外合规文本单独授权
  { path: '/legal-documents', element: <LegalDocumentsPage />, perm: 'legal_doc:view' },
  // 支付与渠道：资金链路配置单独授权
  { path: '/payment-settings', element: <PaymentSettingsPage />, perm: 'payment_setting:view' },
  // 邮件通知：SMTP 通道（验证码发信），与短信同属通道型配置
  { path: '/mail-settings', element: <MailSettingsPage />, perm: 'mail_setting:view' },
  { path: '/ai-models', element: <AiModelsPage />, perm: 'ai_model:view' },
  // AI 写作：提示词是 AI 行为配置，与模型管理共用 ai_model:view
  { path: '/ai-writing', element: <AiWritingPage />, perm: 'ai_model:view' },
  // 部署体检 / 定时任务：只读运维页，与「系统状态」卡同层级，复用 dashboard:view
  { path: '/readiness', element: <ReadinessPage />, perm: 'dashboard:view' },
  { path: '/scheduled-jobs', element: <ScheduledJobsPage />, perm: 'dashboard:view' },
  { path: '/roles', element: <RolesList />, perm: 'role:view' },
  { path: '/departments', element: <DepartmentsList />, perm: 'department:view' },
  // 岗位目录：组织架构，复用部门权限
  { path: '/positions', element: <PositionsPage />, perm: 'department:view' },
  // 监控管理：只读运维数据，与看板同层级
  { path: '/monitor/online', element: <MonitorOnlinePage />, perm: 'dashboard:view' },
  { path: '/monitor/server', element: <MonitorServerPage />, perm: 'dashboard:view' },
  { path: '/monitor/cache', element: <MonitorCachePage />, perm: 'dashboard:view' },
  { path: '/audit-logs', element: <AuditLogsList />, perm: 'audit:view' },
  { path: '/backups', element: <BackupsPage />, perm: 'setting:view' },
  // 回收站跨教程 / 评论 / 用户三个域：任一查看权限即可进入，类型由接口按权限裁剪
  { path: '/recycle-bin', element: <RecycleBinPage />, anyPerms: ['tutorial:view', 'comment:view', 'user:view'] },
  // 个人中心：任何已登录管理员都能进，**不挂页面级权限守卫**
  // （看自己的资料 / 改自己的密码 / 查自己的记录，与业务权限无关）
  { path: '/profile', element: <ProfilePage /> },
]
