import { useState } from 'react'
import { Button, Tooltip } from 'antd'
import { DownloadOutlined } from '@ant-design/icons'
import { downloadCsv, exportErrorMessage } from '../../api/download'
import { toast } from '../../utils/toast'

export interface ExportButtonProps {
  /** 导出端点（相对 `/api/v1`），如 `/admin/export/users` */
  url: string
  /** 当前筛选条件，必须与页面上正在生效的一致 —— 否则导出的不是用户看到的那批数据 */
  params: Record<string, unknown>
  /** 兜底文件名（后端会给带时间戳的名字，这里只在响应头缺失时用） */
  fallbackName?: string
  /** 提示文案里用的名词，如「用户」 */
  label: string
  disabled?: boolean
}

/**
 * 列表导出按钮。
 *
 * 只做三件事：带上当前筛选、下载、失败时把后端给的原因原样告诉用户。
 * **不**在前端判断「有没有数据」——空结果是合法导出（只有表头），
 * 而前端判断会多一次请求且与后端口径分叉。
 *
 * 为什么把 params 显式传进来而不是组件自己去页面里读：
 * 筛选状态散在 20 多个列表页的 useState 里，没有统一形状；
 * 显式传能让每个页面自己决定「哪些条件算当前筛选」，也便于测试。
 */
export default function ExportButton({
  url,
  params,
  fallbackName,
  label,
  disabled,
}: ExportButtonProps) {
  const [loading, setLoading] = useState(false)

  const handleClick = async () => {
    setLoading(true)
    try {
      await downloadCsv(url, params, fallbackName)
      // 成功不弹 toast：浏览器已经有下载提示，再弹一次是噪音
    } catch (err) {
      toast.error(exportErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Tooltip title={`按当前筛选条件导出${label}为 CSV（Excel 可直接打开）`}>
      <Button
        icon={<DownloadOutlined />}
        loading={loading}
        disabled={disabled}
        onClick={() => void handleClick()}
      >
        导出
      </Button>
    </Tooltip>
  )
}
