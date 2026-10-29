import { useEffect, useRef, useState } from 'react'
import { DatePicker, Form, Input, InputNumber, Modal, Radio, Select, Switch } from 'antd'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { apiPut } from '../../api/client'
import { getErrorMessage } from '../../utils/error'
import { isSafeUrl } from '../../utils/url'
import type { User } from '../../types'

import { toast } from '../../utils/toast'
interface EditFormValues {
  role: string
  vip_expire_time: Dayjs | null
  avatar_url: string
  reset_free_seconds: boolean
  points_enabled: boolean
  points_action: 'set' | 'grant' | 'deduct'
  points_value: number
  points_note: string
}

interface UserEditModalProps {
  open: boolean
  user: User | null
  onCancel: () => void
  /** 保存成功后由父组件刷新详情与列表；pointsAdjusted 标识本次是否调整过积分（需联动刷新抽屉流水） */
  onSaved: (pointsAdjusted: boolean) => void
}

/** 编辑用户信息弹窗：角色 / VIP 到期 / 头像 / 免费时长重置 / 积分调整 */
export default function UserEditModal({ open, user, onCancel, onSaved }: UserEditModalProps) {
  const [form] = Form.useForm<EditFormValues>()
  const [saving, setSaving] = useState(false)
  // 积分调整表单联动：开关 / 方式 / 数量（Form.useWatch 供预览；字段用 hidden 隐藏而非
  // 条件渲染卸载——preserve={false} 下卸载字段会丢预设值，导致 Radio 无默认选中）
  const pointsEnabled = Form.useWatch('points_enabled', form)
  const pointsAction = Form.useWatch('points_action', form)
  const pointsValue = Form.useWatch('points_value', form)
  // 回填标记：仅在弹窗打开且目标用户变化时回填，避免详情刷新（user 引用更新）覆盖编辑中的表单
  const filledUserIdRef = useRef<number | null>(null)

  useEffect(() => {
    if (open && user && filledUserIdRef.current !== user.id) {
      filledUserIdRef.current = user.id
      form.setFieldsValue({
        role: user.role,
        vip_expire_time: user.vip_expire_time ? dayjs(user.vip_expire_time) : null,
        avatar_url: user.avatar_url || '',
        reset_free_seconds: false,
        points_enabled: false,
        points_action: 'grant',
        points_value: 0,
        points_note: '',
      })
    }
    if (!open) filledUserIdRef.current = null
  }, [open, user, form])

  const handleSave = async () => {
    if (!user) return
    let values: EditFormValues
    try {
      values = await form.validateFields()
    } catch {
      return
    }
    // 安全：头像 URL 仅允许 http/https 协议（与封面图/网盘链接校验标准一致）
    const avatarUrl = (values.avatar_url || '').trim()
    if (avatarUrl && !isSafeUrl(avatarUrl)) {
      toast.error('头像 URL 必须以 http:// 或 https:// 开头')
      return
    }
    setSaving(true)
    const payload: Record<string, unknown> = {
      role: values.role,
      avatar_url: avatarUrl,
      reset_free_seconds: !!values.reset_free_seconds,
    }
    // 仅勾选「调整积分」时才提交积分参数（避免改角色等操作误写流水）
    if (values.points_enabled) {
      payload.points_action = values.points_action
      payload.points_value = values.points_value ?? 0
      const note = (values.points_note || '').trim()
      if (note) payload.points_note = note
    }
    if (values.vip_expire_time) {
      payload.vip_expire_time = values.vip_expire_time.format('YYYY-MM-DDTHH:mm:ss')
    } else {
      payload.vip_expire_time = ''
    }
    try {
      await apiPut(`/admin/users/${user.id}`, payload)
      toast.success('用户信息已更新')
      onSaved(!!values.points_enabled)
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, '更新失败'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title="编辑用户信息"
      open={open}
      onOk={handleSave}
      onCancel={onCancel}
      confirmLoading={saving}
      okText="保存"
      cancelText="取消"
      zIndex={2000}
      destroyOnHidden
      forceRender
    >
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item
          name="role"
          label="角色"
          rules={[{ required: true, message: '请选择角色' }]}
        >
          <Select
            options={[
              { value: 'user', label: '普通用户 (user)' },
              { value: 'vip', label: 'VIP 用户 (vip)' },
              { value: 'admin', label: '管理员 (admin)' },
            ]}
          />
        </Form.Item>
        <Form.Item
          name="vip_expire_time"
          label="VIP 到期时间"
          extra="留空表示清除 VIP 状态"
        >
          <DatePicker
            showTime
            style={{ width: '100%' }}
            format="YYYY-MM-DD HH:mm"
            placeholder="选择 VIP 到期时间"
          />
        </Form.Item>
        <Form.Item
          name="avatar_url"
          label="头像 URL"
          extra="留空表示清除头像"
        >
          <Input placeholder="https://..." allowClear />
        </Form.Item>
        <Form.Item
          name="reset_free_seconds"
          label="重置免费时长"
          valuePropName="checked"
          extra="勾选后重新赠送 3 小时免费时长（从当前时刻起算）"
        >
          <Switch />
        </Form.Item>
        <Form.Item
          name="points_enabled"
          label="调整积分"
          valuePropName="checked"
          extra="勾选后才执行本次积分调整，每次调整都会写入积分流水"
        >
          <Switch />
        </Form.Item>
        <Form.Item
          name="points_action"
          label="调整方式"
          hidden={!pointsEnabled}
          rules={[{ required: pointsEnabled, message: '请选择调整方式' }]}
        >
          <Radio.Group
            options={[
              { value: 'grant', label: '充值 (+)' },
              { value: 'deduct', label: '扣减 (-)' },
              { value: 'set', label: '设置为 (=)' },
            ]}
            optionType="button"
            buttonStyle="solid"
          />
        </Form.Item>
        <Form.Item
          name="points_value"
          label="积分数量"
          hidden={!pointsEnabled}
          rules={[{ required: pointsEnabled, message: '请填写积分数量' }]}
          extra={(() => {
            const current = user?.points ?? 0
            const v = pointsValue
            if (!pointsEnabled || v == null || v < 0 || !pointsAction) return `当前余额 ${current} 分`
            if (pointsAction === 'set') return `当前余额 ${current} 分 → 调整后 ${v} 分`
            if (pointsAction === 'grant') return `当前余额 ${current} 分 → 调整后 ${current + v} 分`
            if (v > current) return `扣减失败：当前余额仅 ${current} 分`
            return `当前余额 ${current} 分 → 调整后 ${current - v} 分`
          })()}
        >
          <InputNumber min={0} max={10000000} precision={0} style={{ width: '100%' }} suffix="分" />
        </Form.Item>
        <Form.Item
          name="points_note"
          label="备注"
          hidden={!pointsEnabled}
          extra="选填，写入积分流水供用户查看（如：活动奖励）"
        >
          <Input placeholder="如：活动奖励" maxLength={200} allowClear />
        </Form.Item>
      </Form>
    </Modal>
  )
}
