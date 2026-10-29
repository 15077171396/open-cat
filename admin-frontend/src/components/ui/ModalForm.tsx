import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Form, Modal } from 'antd'
import type { FormInstance } from 'antd'

export interface ModalFormProps {
  open: boolean
  title: string
  onCancel: () => void
  /** 校验通过后调用；抛出异常时按钮自行恢复，弹窗不关闭（由调用方决定是否关） */
  onSubmit: (values: Record<string, unknown>) => void | Promise<void>
  /** 回填值：`open` 从 false → true 时写入表单 */
  initialValues?: Record<string, unknown>
  /** 弹窗宽度，默认 520；表单字段多时传 640/720 */
  width?: number
  okText?: string
  /** 覆盖内部 form 实例（需要在外部控制某些字段时传） */
  form?: FormInstance
  children: ReactNode
}

/**
 * 带表单的弹窗。
 *
 * 解决三件重复劳动：
 * 1. **打开时回填、关闭时清空**：手写容易漏 reset，导致「编辑 A 后新增，表单里还留着 A 的值」；
 * 2. **确认按钮 loading 与校验**：`validateFields` 失败时不进 loading，成功后等 onSubmit 完成；
 * 3. **底部按钮常驻**：内容区独立滚动（`maxHeight` + `overflow`），字段再多也不会把按钮顶出屏幕。
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
  okText = '确定',
  form: externalForm,
  children,
}: ModalFormProps) {
  const [innerForm] = Form.useForm()
  const form = externalForm ?? innerForm
  const [submitting, setSubmitting] = useState(false)

  // 打开时回填、关闭时清空：两个方向都要，只做回填会出现「残留上次输入」
  useEffect(() => {
    if (open) {
      form.resetFields()
      if (initialValues) form.setFieldsValue(initialValues)
    } else {
      form.resetFields()
    }
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
      okText={okText}
      cancelText="取消"
      confirmLoading={submitting}
      onOk={handleOk}
      onCancel={onCancel}
      // antd 6 起 `maskClosable` 废弃，改由 mask 对象控制；误点遮罩关掉表单会丢输入
      mask={{ closable: false }}
      styles={{ body: { maxHeight: '60vh', overflowY: 'auto', paddingRight: 4 } }}
    >
      <Form form={form} layout="vertical" initialValues={initialValues}>
        {children}
      </Form>
    </Modal>
  )
}
