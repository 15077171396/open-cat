import { useCallback, useEffect, useRef } from 'react'
import { Modal } from 'antd'
import type { FormInstance } from 'antd'

/**
 * antd v6 的 setFieldsValue 会把程序化赋值的字段标记为 touched，导致未做修改也误弹离开确认框；
 * 异步回填数据后调用本函数清除指定字段的 touched。
 */
export function clearFormTouched(form: FormInstance, names: string[]) {
  form.setFields(names.map((name) => ({ name, touched: false })))
}

/**
 * 未保存修改离开守卫：刷新/关闭走 beforeunload 原生确认框，页内取消经 confirmLeave 弹确认框。
 * 注：SPA 菜单导航未拦截（BrowserRouter 下 useBlocker 不可用），章节编辑有 3 秒自动保存兜底。
 */
export function useUnsavedChangesGuard(isDirty: () => boolean) {
  // ref 每次渲染后刷新，确保守卫回调读到的脏判断总是最新闭包
  const isDirtyRef = useRef(isDirty)
  isDirtyRef.current = isDirty

  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (isDirtyRef.current()) {
        // 现代浏览器忽略自定义文案，只需设置 returnValue 触发原生确认框
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [])

  /** 有未保存修改时弹确认框再执行 onLeave，否则直接执行 */
  const confirmLeave = useCallback((onLeave: () => void) => {
    if (!isDirtyRef.current()) {
      onLeave()
      return
    }
    Modal.confirm({
      title: '存在未保存的修改',
      content: '当前页面有未保存的修改，离开后将丢失。确定要离开吗？',
      okText: '放弃修改并离开',
      okButtonProps: { danger: true },
      cancelText: '留在本页',
      onOk: onLeave,
    })
  }, [])

  return confirmLeave
}
