import { useEffect, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { Form, Modal } from 'antd'
import type { FormInstance } from 'antd'
import { useI18n } from '../../i18n'

export interface ModalFormProps {
  open: boolean
  title: ReactNode
  onCancel: () => void
  /** 校验通过后调用；抛出异常时按钮自行恢复，弹窗不关闭（由调用方决定是否关） */
  onSubmit: (values: Record<string, unknown>) => void | Promise<void>
  /**
   * 回填值：`open` 从 false → true 时写入表单。
   *
   * ⚠️ 不要再用 `form.setFieldsValue(...)` 后紧接 `setOpen(true)` —— 本组件在打开时会先
   * `resetFields()` 再写 `initialValues`，那次的赋值会被清掉。把值放进这里即可。
   */
  initialValues?: Record<string, unknown>
  /** 弹窗宽度，默认 520；表单字段多时传 640/720 */
  width?: number
  okText?: string
  /** 覆盖内部 form 实例（需要在外部控制某些字段时传） */
  form?: FormInstance
  /** 表单布局，默认 vertical（后台表单统一竖排，标签在字段上方） */
  layout?: 'vertical' | 'horizontal'
  /** 表单整体样式（如 `{ marginTop: 16 }`） */
  formStyle?: CSSProperties
  /** 层级：弹窗内再弹 Modal.confirm 时要抬高，否则确认框会被盖住 */
  zIndex?: number
  children: ReactNode
}

/**
 * 带表单的弹窗。
 *
 * 解决四件重复劳动：
 * 1. **打开时回填、关闭时清空**：手写容易漏 reset，导致「编辑 A 后新增，表单里还留着 A 的值」；
 * 2. **确认按钮 loading 与校验**：`validateFields` 失败时不进 loading，成功后等 onSubmit 完成；
 * 3. **底部按钮常驻**：内容区独立滚动（`maxHeight` + `overflow`），字段再多也不会把按钮顶出屏幕；
 * 4. **字段与表单实例的连接不因关闭而断开**：`forceRender` + `destroyOnHidden=false`
 *    保证关闭后 Form 仍挂载，外部持有 form 实例调用 `setFieldsValue` 不会报
 *    「Instance created by useForm is not connected to any Form element」。
 *
 * 约定：
 * - `onSubmit` 内部失败请**抛异常**而不是自己 try/catch 后静默，
 *   这样按钮 loading 能正确结束、弹窗也不会误关（关闭由调用方在成功后显式 `onCancel` 或置 `open=false`）；
 * - 校验文案与字段说明走中文短句，不写英文占位。
 */
export default function ModalForm({
  open,
  title,
  onCancel,
  onSubmit,
  initialValues,
  width = 520,
  okText,
  form: externalForm,
  layout = 'vertical',
  formStyle,
  zIndex,
  children,
}: ModalFormProps) {
  const { t } = useI18n()
  const [innerForm] = Form.useForm()
  const form = externalForm ?? innerForm
  const [submitting, setSubmitting] = useState(false)

  // 打开时回填、关闭时清空：两个方向都要，只做回填会出现「残留上次输入」
  useEffect(() => {
    form.resetFields()
    if (open && initialValues) form.setFieldsValue(initialValues)
    // initialValues 每次渲染都是新对象，依赖它会导致死循环；只在开关变化时同步
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const handleOk = async () => {
    let values: Record<string, unknown>
    try {
      values = await form.validateFields()
    } catch {
      // 校验失败：antd 已在字段下方给出提示，这里只需中止
      return
    }
    setSubmitting(true)
    try {
      await onSubmit(values)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      open={open}
      title={title}
      width={width}
      okText={okText ?? t('common.confirm')}
      cancelText={t('common.cancel')}
      confirmLoading={submitting}
      onOk={handleOk}
      onCancel={onCancel}
      // antd 6 起 `maskClosable` 废弃，改由 mask 对象控制；误点遮罩关掉表单会丢输入
      mask={{ closable: false }}
      zIndex={zIndex}
      // 常驻挂载：表单实例与 Form 元素始终连着，关闭后外部仍可安全操作 form
      forceRender
      styles={{ body: { maxHeight: '60vh', overflowY: 'auto', paddingRight: 4 } }}
    >
      <Form form={form} layout={layout} style={formStyle}>
        {children}
      </Form>
    </Modal>
  )
}
