import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Form,
  Input,
  Skeleton,
  Space,
  Tabs,
  Tag,
  Tooltip,
} from 'antd'
import {
  CodeOutlined,
  CopyOutlined,
  DownloadOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { apiDownload, apiGet } from '../../api/client'
import { PageHeader } from '../../components/ui'
import { useAuthStore } from '../../store/authStore'
import { toast } from '../../utils/toast'
import { getErrorMessage } from '../../utils/error'

interface CodegenColumn {
  name: string
  type: string
  nullable: boolean
  comment: string
  primary: boolean
  editable: boolean
}

interface CodegenTable {
  name: string
  comment: string
  columns: CodegenColumn[]
  suggested: {
    module: string
    class_name: string
    title: string
    route_prefix: string
    fields: string[]
  }
}

interface CodegenTablesData {
  available: boolean
  database: string
  error: string
  items: CodegenTable[]
}

interface CodegenFile {
  path: string
  content: string
}

interface CodegenPreviewData {
  available: boolean
  warnings: string[]
  files: CodegenFile[]
}

interface CodegenForm {
  module: string
  class_name: string
  title: string
  route_prefix: string
  fields: string[]
}

/** 代码生成器：选择表 → 配置命名/字段 → 预览并下载后端 CRUD 骨架。 */
export default function Codegen() {
  const { isSuper, permissions } = useAuthStore()
  const canDownload = isSuper || (permissions?.includes('setting:update') ?? false)
  const [tables, setTables] = useState<CodegenTable[]>([])
  const [available, setAvailable] = useState(true)
  const [database, setDatabase] = useState('')
  const [loading, setLoading] = useState(true)
  const [keyword, setKeyword] = useState('')
  const [selectedName, setSelectedName] = useState('')
  const [config, setConfig] = useState<CodegenForm | null>(null)
  const [preview, setPreview] = useState<CodegenPreviewData | null>(null)
  const [activeFile, setActiveFile] = useState('')
  const [previewing, setPreviewing] = useState(false)
  const [downloading, setDownloading] = useState(false)

  const loadTables = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiGet<CodegenTablesData>('/admin/codegen/tables')
      setAvailable(data.available)
      setDatabase(data.database)
      setTables(data.items ?? [])
      if (data.available && data.items.length > 0) {
        const preferred =
          data.items.find((item) => item.name === 'positions') ?? data.items[0]
        setSelectedName(preferred.name)
        setConfig({
          module: preferred.suggested.module,
          class_name: preferred.suggested.class_name,
          title: preferred.suggested.title,
          route_prefix: preferred.suggested.route_prefix,
          fields: preferred.suggested.fields,
        })
      }
    } catch (error) {
      setAvailable(false)
      setTables([])
      toast.error(getErrorMessage(error, '加载表结构失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadTables()
  }, [loadTables])

  const selected = useMemo(
    () => tables.find((item) => item.name === selectedName) ?? null,
    [tables, selectedName],
  )
  const filteredTables = useMemo(() => {
    const needle = keyword.trim().toLowerCase()
    if (!needle) return tables
    return tables.filter(
      (item) =>
        item.name.toLowerCase().includes(needle) ||
        item.comment.toLowerCase().includes(needle),
    )
  }, [tables, keyword])
  const editableColumns = useMemo(
    () => (selected?.columns ?? []).filter((column) => column.editable),
    [selected],
  )

  const chooseTable = (table: CodegenTable) => {
    setSelectedName(table.name)
    setConfig({
      module: table.suggested.module,
      class_name: table.suggested.class_name,
      title: table.suggested.title,
      route_prefix: table.suggested.route_prefix,
      fields: table.suggested.fields,
    })
    setPreview(null)
    setActiveFile('')
  }

  const patchConfig = (patch: Partial<CodegenForm>) => {
    setConfig((current) => (current ? { ...current, ...patch } : current))
    setPreview(null)
  }

  const generatePreview = async () => {
    if (!selected || !config) return
    if (config.fields.length === 0) {
      toast.error('请至少选择一个字段')
      return
    }
    setPreviewing(true)
    try {
      const data = await apiGet<CodegenPreviewData>('/admin/codegen/preview', {
        params: {
          table: selected.name,
          module: config.module,
          class_name: config.class_name,
          title: config.title,
          route_prefix: config.route_prefix,
          fields: config.fields.join(','),
        },
      })
      setPreview(data)
      setActiveFile(data.files[0]?.path ?? '')
    } catch (error) {
      toast.error(getErrorMessage(error, '生成预览失败'))
    } finally {
      setPreviewing(false)
    }
  }

  const download = async () => {
    if (!selected || !config) return
    if (!canDownload) {
      toast.error('下载需要「保存设置」权限')
      return
    }
    setDownloading(true)
    try {
      const blob = await apiDownload('/admin/codegen/download', {
        table: selected.name,
        module: config.module,
        class_name: config.class_name,
        title: config.title,
        route_prefix: config.route_prefix,
        fields: config.fields,
      })
      if (blob.type.includes('application/json')) {
        const payload = JSON.parse(await blob.text()) as { message?: string }
        throw new Error(payload.message || '下载失败')
      }
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${config.module}-codegen.zip`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
      toast.success('代码包已开始下载')
    } catch (error) {
      toast.error(getErrorMessage(error, '下载失败'))
    } finally {
      setDownloading(false)
    }
  }

  const copyFile = async () => {
    const file = preview?.files.find((item) => item.path === activeFile)
    if (!file) return
    try {
      await navigator.clipboard.writeText(file.content)
      toast.success('已复制到剪贴板')
    } catch {
      toast.error('复制失败，请手动选择文本')
    }
  }

  return (
    <div className="cg-page">
      <PageHeader
        icon={<CodeOutlined />}
        title="代码生成"
        subtitle="读取现有表结构，生成符合本项目约定的后端 CRUD 骨架；只读预览，不建表、不改库"
        count={available ? { total: tables.length, unit: '张表' } : undefined}
        actions={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void loadTables()}>
              刷新表
            </Button>
            <Tooltip title={canDownload ? '' : '需要「保存设置」权限'}>
              <span>
                <Button
                  type="primary"
                  icon={<DownloadOutlined />}
                  disabled={!canDownload || !preview}
                  loading={downloading}
                  onClick={() => void download()}
                >
                  下载 ZIP
                </Button>
              </span>
            </Tooltip>
          </Space>
        }
      />

      <Alert
        type="info"
        showIcon
        className="cg-note"
        title="生成物是骨架，不是即插即用"
        description="下载后仍需按包内 INTEGRATION.md 登记路由权限（fail-closed，不登记会 403）、审计动作码与菜单；前端页面请参考 Positions 手写。"
      />

      {!available ? (
        <Card variant="outlined" className="cg-card">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={`读取表结构失败${database ? `（${database}）` : ''}，请检查数据库连接后重试`}
          />
        </Card>
      ) : (
        <div className="cg-layout">
          <Card
            variant="outlined"
            className="cg-table-card"
            title="数据库表"
            extra={<span className="cg-muted">{database || '—'}</span>}
          >
            <Input.Search
              allowClear
              placeholder="搜索表名或注释"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              className="cg-search"
            />
            {loading ? (
              <Skeleton active paragraph={{ rows: 8 }} />
            ) : filteredTables.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的表" />
            ) : (
              <div className="cg-table-list">
                {filteredTables.map((table) => (
                  <button
                    key={table.name}
                    type="button"
                    className={`cg-table-item${table.name === selectedName ? ' is-active' : ''}`}
                    onClick={() => chooseTable(table)}
                  >
                    <span className="cg-table-item__name">{table.name}</span>
                    <span className="cg-table-item__meta">
                      {table.comment || '无注释'} · {table.columns.length} 列
                    </span>
                  </button>
                ))}
              </div>
            )}
          </Card>

          <div className="cg-main">
            <Card variant="outlined" className="cg-card" title="生成配置">
              {!config || !selected ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="先从左侧选择一张表" />
              ) : (
                <Form layout="vertical" className="cg-form">
                  <div className="cg-form-grid">
                    <Form.Item label="模块名（文件名 / 函数前缀）">
                      <Input
                        value={config.module}
                        onChange={(event) => patchConfig({ module: event.target.value })}
                      />
                    </Form.Item>
                    <Form.Item label="类名">
                      <Input
                        value={config.class_name}
                        onChange={(event) => patchConfig({ class_name: event.target.value })}
                      />
                    </Form.Item>
                    <Form.Item label="页面标题">
                      <Input
                        value={config.title}
                        onChange={(event) => patchConfig({ title: event.target.value })}
                      />
                    </Form.Item>
                    <Form.Item label="路由前缀">
                      <Input
                        value={config.route_prefix}
                        onChange={(event) => patchConfig({ route_prefix: event.target.value })}
                      />
                    </Form.Item>
                  </div>

                  <Form.Item label={`可编辑字段（${config.fields.length}/${editableColumns.length}）`}>
                    <Checkbox.Group
                      value={config.fields}
                      onChange={(values) => patchConfig({ fields: values as string[] })}
                      className="cg-fields"
                    >
                      {editableColumns.map((column) => (
                        <Checkbox key={column.name} value={column.name}>
                          <span className="cg-field-name">{column.name}</span>
                          <Tag variant="filled">{column.type}</Tag>
                          {column.comment ? (
                            <span className="cg-field-comment">{column.comment}</span>
                          ) : null}
                        </Checkbox>
                      ))}
                    </Checkbox.Group>
                  </Form.Item>

                  <Space>
                    <Button
                      type="primary"
                      icon={<ThunderboltOutlined />}
                      loading={previewing}
                      onClick={() => void generatePreview()}
                    >
                      生成预览
                    </Button>
                    <span className="cg-muted">
                      主键与 created_at / updated_at 会自动排除在入参之外
                    </span>
                  </Space>
                </Form>
              )}
            </Card>

            <Card
              variant="outlined"
              className="cg-card cg-preview"
              title="文件预览"
              extra={
                preview ? (
                  <Button size="small" icon={<CopyOutlined />} onClick={() => void copyFile()}>
                    复制当前文件
                  </Button>
                ) : null
              }
            >
              {preview ? (
                <>
                  {preview.warnings.length > 0 ? (
                    <Alert
                      type="warning"
                      showIcon
                      className="cg-warnings"
                      title="生成前请确认"
                      description={
                        <ul>
                          {preview.warnings.map((warning) => (
                            <li key={warning}>{warning}</li>
                          ))}
                        </ul>
                      }
                    />
                  ) : null}
                  <Tabs
                    activeKey={activeFile}
                    onChange={setActiveFile}
                    items={preview.files.map((file) => ({
                      key: file.path,
                      label: file.path.replace('backend/app/', ''),
                      children: (
                        <div className="cg-code-wrap">
                          <div className="cg-code-path">{file.path}</div>
                          <pre className="cg-code">{file.content}</pre>
                        </div>
                      ),
                    }))}
                  />
                </>
              ) : (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="配置命名与字段后，点击「生成预览」查看将下载的文件"
                />
              )}
            </Card>
          </div>
        </div>
      )}
    </div>
  )
}
