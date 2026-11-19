import { useEffect, useState } from 'react'
import { Alert, Button, Modal, Table, Tag, Typography, Upload } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import type { UploadFile } from 'antd'

import client, { idempotentConfig, newIdempotencyKey } from '../../api/client'
import { getErrorData, getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'

/**
 * 用户批量导入弹窗（CSV 上传 → 原子导入 → 行级结果报告）。
 *
 * 语义与后端 user_import_service 对齐：
 * - 表头 `account,password,role,nickname`，每行一个账号；
 * - **全有或全无**：任一行校验失败则整体取消，后端返回行级错误清单（400 + data.errors）；
 * - 导入完成写一条汇总审计（user_batch_import），幂等 key 防重复提交。
 */

interface ImportErrorRow {
  row: number
  reason: string
  detail: string
  code: string
}

interface ImportResult {
  total: number
  imported: number
  ids: number[]
}

const TEMPLATE_TEXT = 'account,password,role,nickname\nuser1@example.com,Abc12345,user,小明\nuser2@example.com,Xyz98765,admin,管理员'

export default function ImportModal({
  open,
  onClose,
  onImported,
}: {
  open: boolean
  onClose: () => void
  onImported: () => void
}) {
  const [fileList, setFileList] = useState<UploadFile[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [idempotencyKey, setIdempotencyKey] = useState('')
  const [result, setResult] = useState<ImportResult | null>(null)
  const [errors, setErrors] = useState<ImportErrorRow[]>([])

  // 幂等 key 在弹窗打开时生成：一次打开 = 一次导入动作
  useEffect(() => {
    if (open) {
      setIdempotencyKey(newIdempotencyKey())
      setFileList([])
      setResult(null)
      setErrors([])
    }
  }, [open])

  const submit = async () => {
    const file = fileList[0]?.originFileObj
    if (!file) {
      toast.warning('请先选择 CSV 文件')
      return
    }
    setSubmitting(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const resp = await client.post('/admin/users/import', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        ...idempotentConfig(idempotencyKey),
      })
      const data = resp.data as ImportResult
      setResult(data)
      setErrors([])
      toast.success(`导入完成：成功创建 ${data.imported} 个账号`)
      onImported()
    } catch (err) {
      // 后端校验失败（400 + data.errors）：把行级清单展示出来而不是只弹一条 toast
      const errorData = getErrorData<{ errors?: ImportErrorRow[] }>(err)
      if (errorData?.errors?.length) {
        setErrors(errorData.errors)
        toast.error(getErrorMessage(err, '校验未通过'))
      } else {
        toast.error(getErrorMessage(err, '导入失败'))
      }
    } finally {
      setSubmitting(false)
    }
  }

  const errorColumns = [
    { title: 'CSV 行号', dataIndex: 'row', width: 90 },
    { title: '原因', dataIndex: 'reason', width: 200 },
    { title: '详情', dataIndex: 'detail', ellipsis: true },
  ]

  return (
    <Modal
      title="批量导入用户"
      open={open}
      onCancel={onClose}
      width={680}
      footer={[
        <Button key="cancel" onClick={onClose}>
          关闭
        </Button>,
        <Button key="submit" type="primary" loading={submitting} disabled={!fileList.length} onClick={() => void submit()}>
          开始导入
        </Button>,
      ]}
    >
      <Alert
        type="info"
        showIcon
        title="全有或全无"
        description="任一行校验失败则整体取消（不写入任何账号）；校验通过后一次性创建并写汇总审计。单次最多 500 行 / 1MB。"
        style={{ marginBottom: 12 }}
      />
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 6 }}>
        CSV 表头（首行）：<code>account,password,role,nickname</code>；role 取 user 或 admin；
        密码需满足当前强度策略。可把下面的模板保存为 .csv 编辑后上传：
      </Typography.Paragraph>
      <Typography.Paragraph>
        <Button
          size="small"
          onClick={() => {
            void navigator.clipboard.writeText(TEMPLATE_TEXT)
            toast.success('模板已复制')
          }}
        >
          复制 CSV 模板
        </Button>
      </Typography.Paragraph>
      <Upload.Dragger
        accept=".csv,text/csv"
        maxCount={1}
        fileList={fileList}
        beforeUpload={() => false}
        onChange={({ fileList: next }) => setFileList(next.slice(-1))}
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">点击或拖拽 CSV 文件到此处</p>
        <p className="ant-upload-hint">UTF-8 编码（Excel 导出请另存为 CSV UTF-8）</p>
      </Upload.Dragger>

      {result && (
        <Alert
          type="success"
          showIcon
          title={`导入完成：成功创建 ${result.imported} 个账号`}
          description={`共解析 ${result.total} 行数据。`}
          style={{ marginTop: 12 }}
        />
      )}
      {errors.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Alert
            type="error"
            showIcon
            title={`校验未通过：${errors.length} 行有问题（未写入任何账号）`}
            description="修正下列行后重新上传整份文件。"
            style={{ marginBottom: 8 }}
          />
          <Table<ImportErrorRow>
            size="small"
            columns={errorColumns}
            dataSource={errors}
            rowKey={(record) => `${record.row}-${record.code}`}
            pagination={false}
          />
        </div>
      )}
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 12, marginBottom: 0 }}>
        导入完成后可在操作审计（<Tag style={{ fontSize: 11 }}>user_batch_import</Tag>）中追溯本次批量授出的账号清单。
      </Typography.Paragraph>
    </Modal>
  )
}
