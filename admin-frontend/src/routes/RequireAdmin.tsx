import { useEffect, useState } from 'react'
import { Navigate, Outlet } from 'react-router-dom'
import { Spin, Result, Button } from 'antd'
import { useAuthStore } from '../store/authStore'

export default function RequireAdmin() {
  const { token, user, isAuthenticated } = useAuthStore()
  const [checking, setChecking] = useState(true)
  const [errorInfo, setErrorInfo] = useState<{ title: string; message: string } | null>(null)

  useEffect(() => {
    const init = async () => {
      if (!token) {
        setChecking(false)
        return
      }
      if (user) {
        setChecking(false)
        return
      }

      // 复用 store 的 fetchUser（含并发去重），错误分类由 FetchUserResult 判别联合承载
      const result = await useAuthStore.getState().fetchUser()
      if (result.kind === 'network') {
        setErrorInfo({ title: '网络连接异常', message: '无法连接到服务器，请检查网络后重试' })
      } else if (result.kind === 'http') {
        setErrorInfo({ title: `服务器错误 (${result.status})`, message: result.message })
      }
      // 'ok'：user 已写入 store，重新渲染后放行
      // 'unauthorized'：登录态已在 fetchUser 内清除，下方渲染分支跳转登录页
      setChecking(false)
    }
    init()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  if (checking) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh' }}>
        <Spin size="large" />
      </div>
    )
  }

  if (errorInfo) {
    return (
      <Result
        status="warning"
        title={errorInfo.title}
        subTitle={errorInfo.message}
        extra={
          <Button type="primary" onClick={() => window.location.reload()}>
            重试
          </Button>
        }
      />
    )
  }

  if (!isAuthenticated || !token) {
    return <Navigate to="/login" replace />
  }

  if (!user || user.role !== 'admin') {
    // 非管理员：在 effect 中清空状态，避免渲染过程中 setState
    return <AdminClearAndRedirect />
  }

  return <Outlet />
}

// 非管理员访问时：在 effect 中清空状态并跳转登录页（避免渲染阶段 setState 警告）
function AdminClearAndRedirect() {
  useEffect(() => {
    useAuthStore.getState().clearAuth()
  }, [])
  return <Navigate to="/login" replace />
}
