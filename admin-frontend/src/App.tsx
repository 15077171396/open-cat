import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import ErrorBoundary from './components/ErrorBoundary'
import { RequirePerm } from './components/PermGuard'
import { ADMIN_ROUTES, type AdminRouteDef } from './routes/adminRoutes'

// 性能：布局与路由守卫均懒加载，入口 chunk 仅含 react 全家桶 + 零依赖的
// ErrorBoundary / CSS spinner，antd 等重组件随首个业务页面按需并行加载
const Login = lazy(() => import('./pages/Login'))
const Setup = lazy(() => import('./pages/Setup'))
const RequireAdmin = lazy(() => import('./routes/RequireAdmin'))
const AdminLayout = lazy(() => import('./components/Layout/AdminLayout'))

const pageLoaderStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'center',
  alignItems: 'center',
  minHeight: '100vh',
}

/**
 * 按路由表声明包一层页面级权限守卫。
 * 没声明 `perm` / `anyPerms` 的页面原样渲染（当前只有个人中心，理由见路由表注释）。
 */
function guarded(route: AdminRouteDef) {
  if (!route.perm && !route.anyPerms?.length) return route.element
  return (
    <RequirePerm perm={route.perm} anyPerms={route.anyPerms}>
      {route.element}
    </RequirePerm>
  )
}

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
                {/* 业务页面全部来自路由表：路径 / 组件 / 权限码一处声明，
                    由 backend/tests/test_admin_routes.py 跨语言校验它与菜单、权限目录的一致性 */}
                {ADMIN_ROUTES.map((route) => (
                  <Route key={route.path} path={route.path} element={guarded(route)} />
                ))}
              </Route>
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ErrorBoundary>
  )
}
