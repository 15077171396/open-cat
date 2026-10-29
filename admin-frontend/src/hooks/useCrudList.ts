import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Modal } from 'antd'
import client, { apiGet } from '../api/client'
import { DEFAULT_PAGE_SIZE } from '../constants'
import { getErrorMessage } from '../utils/error'
import type { PaginatedResponse } from '../types'

import { toast } from '../utils/toast'
interface UseCrudListOptions {
  /** API 路径，如 /admin/tutorials */
  basePath: string
  params?: Record<string, string | number | boolean>
  /** 默认 true */
  paginated?: boolean
  /** 默认「确认删除」 */
  deleteTitle?: string
  /** 默认「删除成功」 */
  deleteSuccessMessage?: string
  /**
   * 自定义删除确认文案（可选）。
   * 软删除的页面（教程/资源）要说明「可恢复」，否则用户会以为点下去就没了。
   * 不传时保持原文案，行为完全不变。
   */
  deleteConfirm?: (label?: string) => string
}

interface UseCrudListResult<T> {
  items: T[]
  loading: boolean
  page: number
  pageSize: number
  total: number
  refresh: () => void
  handleDelete: (id: number, label?: string) => void
  setPage: (page: number) => void
}

/** 通用 CRUD 列表 hook：封装加载、分页、删除确认 */
export function useCrudList<T extends { id: number }>(options: UseCrudListOptions): UseCrudListResult<T> {
  const {
    basePath,
    paginated = true,
    deleteTitle = '确认删除',
    deleteSuccessMessage = '删除成功',
    deleteConfirm,
  } = options
  const [items, setItems] = useState<T[]>([])
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const pageSize = DEFAULT_PAGE_SIZE
  // 请求序号：快速翻页时仅允许最新请求落盘，防止旧数据覆盖新页
  const loadSeqRef = useRef(0)
  // params 是调用方的内联字面量（每次渲染新引用），直接进依赖会变成无限请求；
  // 而 paramsKey = JSON.stringify(params) 是它的稳定指纹：值变了 params 内容必然变。
  // 这里以 key 为准做依赖、再解析回来用，而不是把对象本体放进依赖 —— 旧行为保持不变。
  const paramsKey = JSON.stringify(options.params ?? null)
  const parsedParams = useMemo<Record<string, string | number | boolean>>(
    () => (paramsKey === 'null' ? {} : (JSON.parse(paramsKey) as Record<string, string | number | boolean>)),
    [paramsKey],
  )

  const load = useCallback(async () => {
    const seq = ++loadSeqRef.current
    setLoading(true)
    try {
      const queryParams: Record<string, string | number | boolean> = { ...parsedParams }
      if (paginated) {
        queryParams.page = page
        queryParams.page_size = pageSize
      }
      const data = (await apiGet<PaginatedResponse<T>>(basePath, { params: queryParams })) || { items: [], total: 0 }
      if (seq !== loadSeqRef.current) return // 过期响应（已被更新的请求取代），直接丢弃
      setItems(data.items || [])
      setTotal(data.total ?? (data.items?.length ?? 0))
    } catch (err) {
      if (seq !== loadSeqRef.current) return
      toast.error(getErrorMessage(err, '加载列表失败'))
    } finally {
      // loading 只由最新请求负责复位
      if (seq === loadSeqRef.current) setLoading(false)
    }
  }, [basePath, page, pageSize, paginated, parsedParams])

  useEffect(() => {
    load()
  }, [load])

  const refresh = useCallback(() => {
    load()
  }, [load])

  const handleDelete = useCallback((id: number, label?: string) => {
    Modal.confirm({
      title: deleteTitle,
      content: deleteConfirm ? deleteConfirm(label) : label ? `确认删除 ${label}？` : '确认删除该项？',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await client.delete(`${basePath}/${id}`)
          toast.success(deleteSuccessMessage)
          // 删除当前页最后一条且不在第一页时回退一页，避免停留在越界空页
          if (items.length <= 1 && page > 1) {
            setPage(page - 1)
          } else {
            load()
          }
        } catch (err) {
          toast.error(getErrorMessage(err, '删除失败'))
        }
      },
    })
  }, [basePath, deleteTitle, deleteSuccessMessage, deleteConfirm, load, items, page])

  return {
    items,
    loading,
    page,
    pageSize,
    total,
    refresh,
    handleDelete,
    setPage,
  }
}
