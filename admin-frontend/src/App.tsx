import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import ErrorBoundary from './components/ErrorBoundary'
import { RequirePerm } from './components/PermGuard'

// 性能：布局与路由守卫均懒加载，入口 chunk 仅含 react 全家桶 + 零依赖的
// ErrorBoundary / CSS spinner，antd 等重组件随首个业务页面按需并行加载
const Login = lazy(() => import('./pages/Login'))
const Setup = lazy(() => import('./pages/Setup'))
const RequireAdmin = lazy(() => import('./routes/RequireAdmin'))
const AdminLayout = lazy(() => import('./components/Layout/AdminLayout'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const TutorialsList = lazy(() => import('./pages/Tutorials/List'))
const TutorialsEdit = lazy(() => import('./pages/Tutorials/Edit'))
const ResourcesList = lazy(() => import('./pages/Resources/List'))
const ResourcesEdit = lazy(() => import('./pages/Resources/Edit'))
const NodesList = lazy(() => import('./pages/Nodes/List'))
const NodesEdit = lazy(() => import('./pages/Nodes/Edit'))
const ToolsList = lazy(() => import('./pages/Tools/List'))
const ToolsEdit = lazy(() => import('./pages/Tools/Edit'))
const PluginsList = lazy(() => import('./pages/Plugins/List'))
const PluginsEdit = lazy(() => import('./pages/Plugins/Edit'))
const CategoriesList = lazy(() => import('./pages/Categories/List'))
const AnnouncementsList = lazy(() => import('./pages/Announcements/List'))
const UsersList = lazy(() => import('./pages/Users/List'))
const PointsList = lazy(() => import('./pages/Points/List'))
const RedeemCodesList = lazy(() => import('./pages/RedeemCodes/List'))
const AuthRequestsList = lazy(() => import('./pages/AuthRequests/List'))
const FeedbackList = lazy(() => import('./pages/Feedback/List'))
const MessagesList = lazy(() => import('./pages/Messages/List'))
const ModerationList = lazy(() => import('./pages/Moderation/List'))
const CommentsList = lazy(() => import('./pages/Comments/List'))
const SettingsPage = lazy(() => import('./pages/Settings'))
const LoginSettingsPage = lazy(() => import('./pages/LoginSettings'))
const LegalDocumentsPage = lazy(() => import('./pages/LegalDocuments'))
const PaymentSettingsPage = lazy(() => import('./pages/PaymentSettings'))
const MailSettingsPage = lazy(() => import('./pages/MailSettings'))
const AiModelsPage = lazy(() => import('./pages/AiModels'))
const AiWritingPage = lazy(() => import('./pages/AiWriting'))
const PaymentProductsPage = lazy(() => import('./pages/PaymentProducts/List'))
const PaymentOrdersPage = lazy(() => import('./pages/PaymentOrders/List'))
const RolesList = lazy(() => import('./pages/Roles/List'))
const DepartmentsList = lazy(() => import('./pages/Departments/List'))
const AuditLogsList = lazy(() => import('./pages/AuditLogs/List'))
const SearchKeywordsPage = lazy(() => import('./pages/SearchKeywords'))
const MaterialsPage = lazy(() => import('./pages/Materials'))
const ScheduledJobsPage = lazy(() => import('./pages/ScheduledJobs'))
const ReadinessPage = lazy(() => import('./pages/Readiness'))
const ProfilePage = lazy(() => import('./pages/Profile'))

const pageLoaderStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'center',
  alignItems: 'center',
  minHeight: '100vh',
}

/** 页面级 RBAC 守卫包装：无菜单权限直接访问 URL 时渲染 403 */
const perm = (code: string, element: React.ReactNode) => (
  <RequirePerm perm={code}>{element}</RequirePerm>
)

const anyPerm = (codes: string[], element: React.ReactNode) => (
  <RequirePerm anyPerms={codes}>{element}</RequirePerm>
)

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter
        basename="/admin"
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Suspense
          fallback={
            <div style={pageLoaderStyle}>
              <div className="page-loading-spinner" />
            </div>
          }
        >
          <Routes>
            <Route path="/login" element={<Login />} />
            {/* 安装向导：必须在 RequireAdmin 之外 —— 空库时还没有任何账号可登录 */}
            <Route path="/setup" element={<Setup />} />
            <Route element={<RequireAdmin />}>
              <Route element={<AdminLayout />}>
                <Route path="/" element={perm('dashboard:view', <Dashboard />)} />
                <Route path="/tutorials" element={perm('tutorial:view', <TutorialsList />)} />
                <Route path="/tutorials/new" element={perm('tutorial:create', <TutorialsEdit />)} />
                <Route path="/tutorials/:id/edit" element={perm('tutorial:edit', <TutorialsEdit />)} />
                <Route path="/resources" element={perm('resource:view', <ResourcesList />)} />
                <Route path="/resources/new" element={perm('resource:create', <ResourcesEdit />)} />
                <Route path="/resources/:id/edit" element={perm('resource:edit', <ResourcesEdit />)} />
                <Route path="/nodes" element={perm('node:view', <NodesList />)} />
                <Route path="/nodes/new" element={perm('node:create', <NodesEdit />)} />
                <Route path="/nodes/:id/edit" element={perm('node:edit', <NodesEdit />)} />
                <Route path="/tools" element={perm('tool:view', <ToolsList />)} />
                <Route path="/tools/new" element={perm('tool:create', <ToolsEdit />)} />
                <Route path="/tools/:id/edit" element={perm('tool:edit', <ToolsEdit />)} />
                <Route path="/plugins" element={perm('plugin:view', <PluginsList />)} />
                <Route path="/plugins/new" element={perm('plugin:create', <PluginsEdit />)} />
                <Route path="/plugins/:id/edit" element={perm('plugin:edit', <PluginsEdit />)} />
                <Route path="/categories" element={perm('category:view', <CategoriesList />)} />
                <Route path="/announcements" element={perm('announcement:view', <AnnouncementsList />)} />
                <Route path="/payment-products" element={perm('payment_product:view', <PaymentProductsPage />)} />
                <Route path="/payment-orders" element={perm('payment_order:view', <PaymentOrdersPage />)} />
                <Route path="/users" element={perm('user:view', <UsersList />)} />
                <Route path="/points" element={perm('points:view', <PointsList />)} />
                <Route path="/redeem-codes" element={perm('redeem_code:view', <RedeemCodesList />)} />
                <Route path="/auth-requests" element={perm('auth_request:view', <AuthRequestsList />)} />
                <Route path="/feedback" element={perm('feedback:view', <FeedbackList />)} />
                <Route path="/messages" element={perm('message:view', <MessagesList />)} />
                <Route path="/search-keywords" element={perm('dashboard:view', <SearchKeywordsPage />)} />
                <Route
                  path="/materials"
                  element={anyPerm(
                    ['tutorial:view', 'resource:view', 'plugin:view', 'node:view'],
                    <MaterialsPage />,
                  )}
                />
                {/* 机审留痕与人工举报同属内容审核页，任一查看权限即可进入 */}
                <Route
                  path="/moderation"
                  element={
                    <RequirePerm anyPerms={['moderation:view', 'report:view']}>
                      <ModerationList />
                    </RequirePerm>
                  }
                />
                <Route path="/comments" element={perm('comment:view', <CommentsList />)} />
                <Route path="/settings" element={perm('setting:view', <SettingsPage />)} />
                {/* 登录管理：从「系统设置 → 登录与短信」抽出的独立模块，权限码独立 */}
                <Route
                  path="/login-settings"
                  element={perm('login_setting:view', <LoginSettingsPage />)}
                />
                {/* 法律文档：从「系统设置 → 法律文档」抽出的独立模块，对外合规文本单独授权 */}
                <Route
                  path="/legal-documents"
                  element={perm('legal_doc:view', <LegalDocumentsPage />)}
                />
                {/* 支付与渠道：从「系统设置 → 支付与渠道」抽出，资金链路配置单独授权 */}
                <Route
                  path="/payment-settings"
                  element={perm('payment_setting:view', <PaymentSettingsPage />)}
                />
                {/* 邮件通知：SMTP 通道（验证码发信），与短信同属通道型配置 */}
                <Route
                  path="/mail-settings"
                  element={perm('mail_setting:view', <MailSettingsPage />)}
                />
                <Route path="/ai-models" element={perm('ai_model:view', <AiModelsPage />)} />
                {/* AI 写作：提示词是 AI 行为配置，与模型管理共用 ai_model:view */}
                <Route path="/ai-writing" element={perm('ai_model:view', <AiWritingPage />)} />
                {/* 部署体检：只读运维页，与「系统状态」卡同层级，复用 dashboard:view */}
                <Route
                  path="/readiness"
                  element={perm('dashboard:view', <ReadinessPage />)}
                />
                {/* 定时任务：只读运维页，复用看板的 dashboard:view（与「系统状态」卡同层级数据） */}
                <Route
                  path="/scheduled-jobs"
                  element={perm('dashboard:view', <ScheduledJobsPage />)}
                />
                <Route path="/roles" element={perm('role:view', <RolesList />)} />
                <Route path="/departments" element={perm('department:view', <DepartmentsList />)} />
                <Route path="/audit-logs" element={perm('audit:view', <AuditLogsList />)} />
                {/* 个人中心：任何已登录管理员都能进，**不挂页面级权限守卫**
                    （看自己的资料/改自己的密码/查自己的记录，与业务权限无关） */}
                <Route path="/profile" element={<ProfilePage />} />
              </Route>
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ErrorBoundary>
  )
}
