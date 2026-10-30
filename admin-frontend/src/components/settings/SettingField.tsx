import { memo, lazy, Suspense, useState, type CSSProperties } from 'react'
import { Typography, Space, Button, Input, Upload, Spin, Tag, Switch, Select } from 'antd'
import {
  UploadOutlined,
  UndoOutlined,
  PictureOutlined,
  EyeOutlined,
  EyeInvisibleOutlined,
} from '@ant-design/icons'
import { uploadImage } from '../../utils/upload'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'
import type { SettingDef } from './types'

// 富文本编辑器体积较大，法律文档组渲染时才按需加载
const QuillEditor = lazy(() => import('../QuillEditor'))

/** Quill 空文档的语义 HTML 形态（视为未配置，保存空值恢复默认文案） */
const RICHTEXT_EMPTY_RE = /^<p>(<br\s*\/?>)?<\/p>$/

function normalizeRichText(v: string): string {
  const t = (v || '').trim()
  return RICHTEXT_EMPTY_RE.test(t) ? '' : t
}

/**
 * 设置项下方的补充说明：优先用后端 `help_text`（单位 / 取值范围 / 生效时机由定义处统一维护），
 * 未提供时退回各分支自己的兜底文案。
 */
function HelpText({ def, fallback }: { def: SettingDef; fallback?: string }) {
  return (
    <>
      {fallback ? (
        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 4 }}>
          {fallback}
        </Typography.Text>
      ) : null}
      {def.help_text ? (
        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 2 }}>
          {def.help_text}
        </Typography.Text>
      ) : null}
    </>
  )
}

/** 图片设置项：预览 + 上传 + 恢复默认 */
function ImageSettingItem({
  def,
  value,
  onChange,
}: {
  def: SettingDef
  value: string
  onChange: (v: string) => void
}) {
  const [uploading, setUploading] = useState(false)
  // 当前生效值：自定义值优先，其次默认值（与前台展示逻辑一致）
  const effective = value || def.default
  const isCustom = !!value

  const handleUpload = async (file: File) => {
    setUploading(true)
    try {
      const result = await uploadImage(file)
      onChange(result.url)
      toast.success('图片已上传，保存后生效')
    } catch (err) {
      toast.error(getErrorMessage(err, '上传失败'))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
      {/* 预览：contain 适配横图/方图，未配置且无默认时显示占位 */}
      <div
        style={{
          width: 120,
          height: 120,
          flexShrink: 0,
          borderRadius: "var(--app-radius)",
          border: '1px dashed #d9d9d9',
          background: 'var(--app-subtle)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          overflow: 'hidden',
        }}
      >
        {effective ? (
          <img src={effective} alt={def.label} style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }} />
        ) : (
          <PictureOutlined style={{ fontSize: 28, color: 'var(--app-text-4)' }} />
        )}
      </div>
      <Space orientation="vertical" size={8}>
        <Space size={8} wrap>
          <Upload
            showUploadList={false}
            accept="image/jpeg,image/png,image/webp,image/gif"
            beforeUpload={(file) => {
              handleUpload(file)
              return false // 阻止 antd 自动上传，走自定义 COS 通道
            }}
          >
            <Button icon={<UploadOutlined />} loading={uploading}>
              上传图片
            </Button>
          </Upload>
          {isCustom ? (
            <Button icon={<UndoOutlined />} onClick={() => onChange('')}>
              恢复默认
            </Button>
          ) : (
            <Tag style={{ margin: 0 }}>当前使用默认</Tag>
          )}
        </Space>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          支持 JPG/PNG/WEBP/GIF，不超过 5MB
          {def.default ? '；不设置时使用系统默认图' : '；不设置时前台展示原有回退样式'}
        </Typography.Text>
      </Space>
    </div>
  )
}

/**
 * 单个设置项的表单渲染（text/url/richtext/image/password 等全部类型）。
 * memo 判等仅比较 def/value：onChange 内部只用 setState 函数式更新，忽略其引用变化是安全的；
 * 编辑任一字段时其余已挂载设置项（含重量级 QuillEditor）不再重渲染。
 */
export const SettingField = memo(
  SettingItem,
  (prev, next) => prev.def === next.def && prev.value === next.value,
)

function SettingItem({
  def,
  value,
  onChange,
}: {
  def: SettingDef
  value: string
  onChange: (v: string) => void
}) {
  // 密钥字段是否显示明文（password 分支用 CSS 打码，见其注释）
  const [revealSecret, setRevealSecret] = useState(false)

  if (def.type === 'password') {
    // 密钥类：已配置时 value 是后端掩码；不改 → 不会被提交（只提交差异项）；
    // 清空后保存 = 移除配置、回退 .env 环境变量。
    return (
      <div style={{ maxWidth: 520 }}>
        <Typography.Text strong style={{ display: 'block', marginBottom: 8 }}>
          {def.label}
        </Typography.Text>
        <Input
          value={value}
          type="text"
          placeholder="未配置（将回退到服务器 .env 的环境变量）"
          maxLength={500}
          autoComplete="off"
          // 刻意不用 type="password"：配置项不在 <form> 内，Chrome 会报错并诱使密码管理器
          // 把 API Key 当登录密码保存；改用 -webkit-text-security 在普通输入框上打码
          style={
            revealSecret
              ? undefined
              : // csstype 未收录 -webkit-text-security，需断言（该属性 Chrome/Safari/Firefox 133+ 均支持）
                ({ WebkitTextSecurity: 'disc' } as unknown as CSSProperties)
          }
          suffix={
            <Button
              type="text"
              size="small"
              aria-label={revealSecret ? '隐藏密钥' : '显示密钥'}
              icon={revealSecret ? <EyeInvisibleOutlined /> : <EyeOutlined />}
              onClick={() => setRevealSecret((v) => !v)}
            />
          }
          onChange={(e) => onChange(e.target.value.trim())}
        />
        <HelpText
          def={def}
          fallback="已配置时显示为掩码（******** 开头）。更换请先清空再输入新值；清空保存即移除配置、回退 .env。"
        />
      </div>
    )
  }
  if (def.type === 'secret_text') {
    // 长密钥（PEM 私钥/证书）：已配置时后端只回显掩码；输入框视为空，
    // 用户不粘贴新值就保持原值，显式“清除配置”才提交空串。
    const masked = !!value && value.startsWith('********')
    return (
      <div style={{ maxWidth: 720 }}>
        <Typography.Text strong style={{ display: 'block', marginBottom: 8 }}>
          {def.label}
        </Typography.Text>
        <Input.TextArea
          value={masked ? '' : value}
          placeholder={
            masked
              ? '已配置（掩码）；留空不修改，粘贴新内容即可替换'
              : '粘贴 PEM 全文（含 BEGIN/END 行）'
          }
          autoSize={{ minRows: 4, maxRows: 12 }}
          maxLength={20000}
          style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
          onChange={(e) => onChange(e.target.value)}
        />
        <HelpText
          def={def}
          fallback="密钥加密存储，页面不回显明文；保存后立即生效"
        />
        {masked && (
          <Button size="small" danger style={{ marginTop: 8 }} onClick={() => onChange('')}>
            清除配置（回退 .env / 默认）
          </Button>
        )}
      </div>
    )
  }
  if (def.type === 'text') {
    return (
      <div style={{ maxWidth: 420 }}>
        <Typography.Text strong style={{ display: 'block', marginBottom: 8 }}>
          {def.label}
        </Typography.Text>
        <Input
          value={value}
          placeholder={def.default}
          maxLength={50}
          showCount
          onChange={(e) => onChange(e.target.value)}
        />
        <HelpText def={def} fallback={`清空保存后恢复默认「${def.default}」`} />
      </div>
    )
  }
  if (def.type === 'textarea') {
    // 多行文本（敏感词表等）：长度上限由后端校验（20000 字符），这里只做展示
    return (
      <div style={{ maxWidth: 560 }}>
        <Typography.Text strong style={{ display: 'block', marginBottom: 8 }}>
          {def.label}
        </Typography.Text>
        <Input.TextArea
          value={value}
          placeholder={def.default}
          autoSize={{ minRows: 4, maxRows: 12 }}
          maxLength={20000}
          onChange={(e) => onChange(e.target.value)}
        />
        <HelpText def={def} fallback="清空保存后恢复默认（内置基线词库始终生效）" />
      </div>
    )
  }
  if (def.type === 'bool') {
    // 开关类：统一存 "1" / "0"。
    // ⚠️ 未配置时（value 为空串）要显示**生效中的默认值**，否则会误报「已关闭」——
    // 用户以为关了、实际还在按 .env 开着，是这类表单最典型的坑。
    const effective = (value || def.default) === '1'
    return (
      <div style={{ maxWidth: 520 }}>
        <Space size={12} align="center">
          <Switch checked={effective} onChange={(checked) => onChange(checked ? '1' : '0')} />
          <Typography.Text strong>{def.label}</Typography.Text>
          <Tag color={effective ? 'green' : 'default'} style={{ margin: 0 }}>
            {effective ? '已开启' : '已关闭'}
          </Tag>
          {!value && <Tag style={{ margin: 0 }}>默认</Tag>}
        </Space>
        <HelpText
          def={def}
          fallback={`未配置时使用服务器 .env 默认值（当前默认：${def.default === '1' ? '开启' : '关闭'}）`}
        />
      </div>
    )
  }
  if (def.type === 'select') {
    // 枚举类：候选值来自后端 options，前端不再维护第二份清单
    return (
      <div style={{ maxWidth: 420 }}>
        <Typography.Text strong style={{ display: 'block', marginBottom: 8 }}>
          {def.label}
        </Typography.Text>
        <Select
          value={value || def.default || undefined}
          placeholder="未配置"
          allowClear
          style={{ width: '100%' }}
          options={(def.options || []).map((o) => ({ value: o.value, label: o.label }))}
          onChange={(v) => onChange(v ?? '')}
        />
        <HelpText
          def={def}
          fallback={`未配置时使用服务器 .env 默认值（当前默认：${def.default}）；清空即恢复默认`}
        />
      </div>
    )
  }
  if (def.type === 'url') {
    return (
      <div style={{ maxWidth: 520 }}>
        <Typography.Text strong style={{ display: 'block', marginBottom: 8 }}>
          {def.label}
        </Typography.Text>
        <Input
          value={value}
          placeholder={def.default || 'https://'}
          maxLength={500}
          onChange={(e) => onChange(e.target.value.trim())}
        />
        <HelpText
          def={def}
          fallback={`仅支持 http(s) 链接；清空保存后回退默认${def.default ? '（当前默认展示）' : ''}`}
        />
      </div>
    )
  }
  if (def.type === 'richtext') {
    return (
      <div>
        <Typography.Text strong style={{ display: 'block', marginBottom: 8 }}>
          {def.label}
        </Typography.Text>
        {/* 说明文字放在编辑器**上方**：编辑器自带底部状态栏（灰底、紧贴下边框），
            说明再压在它下面会和状态栏挤成一坨。放到标签下方既不影响阅读顺序，
            也让编辑器下方只剩状态栏这一行。 */}
        <div style={{ marginBottom: 10 }}>
          <HelpText
            def={def}
            fallback="清空保存后恢复系统默认文案；保存后前台对应页面即时展示（缓存最多延迟 5 分钟）"
          />
        </div>
        <Suspense
          fallback={
            <div style={{ display: 'flex', justifyContent: 'center', padding: '48px 0' }}>
              <Spin />
            </div>
          }
        >
          <QuillEditor
            value={value}
            onChange={(v) => onChange(normalizeRichText(v))}
            placeholder={`请输入${def.label}内容...`}
            height={360}
            // 法律文书通常很长：固定高度时编辑器内部滚动，工具栏吸顶行程被容器高度限死
            // （滚过 ~318px 就跟着走）；自适应高度后工具栏在整篇文档范围内保持吸顶
            autoHeight
            showToc={false}
          />
        </Suspense>
      </div>
    )
  }
  if (def.type === 'number') {
    // 数值项（AI 单价 / 全站上限）：草稿始终是字符串，校验与规范化由后端统一负责，
    // 前端只限制输入法键盘与长度，避免出现「前端能填、后端拒收」两套规则。
    return (
      <div style={{ maxWidth: 320 }}>
        <Typography.Text strong style={{ display: 'block', marginBottom: 8 }}>
          {def.label}
        </Typography.Text>
        <Input
          value={value}
          placeholder={def.default}
          inputMode="decimal"
          maxLength={32}
          onChange={(e) => onChange(e.target.value.trim())}
        />
        <HelpText
          def={def}
          fallback="只填数字（可带小数）；留空保存则回退服务器 .env 配置"
        />
      </div>
    )
  }
  if (def.type === 'json') {
    // JSON 对象项（按模型单价表）：后端保存时严格校验结构，前端只做格式提示
    return (
      <div style={{ maxWidth: 560 }}>
        <Typography.Text strong style={{ display: 'block', marginBottom: 8 }}>
          {def.label}
        </Typography.Text>
        <Input.TextArea
          value={value}
          placeholder={def.default || '{"deepseek": {"input": 1, "output": 2}}'}
          autoSize={{ minRows: 3, maxRows: 8 }}
          maxLength={4000}
          showCount
          style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
          onChange={(e) => onChange(e.target.value)}
        />
        <HelpText
          def={def}
          fallback={`形如 {"deepseek": {"input": 1, "output": 2}}，单位为元 / 百万 tokens；未列出的模型回退上面的兜底单价。留空保存则回退服务器 .env 的 AI_MODEL_PRICES`}
        />
      </div>
    )
  }
  return (
    <div>
      <Typography.Text strong style={{ display: 'block', marginBottom: 8 }}>
        {def.label}
      </Typography.Text>
      <ImageSettingItem def={def} value={value} onChange={onChange} />
    </div>
  )
}
