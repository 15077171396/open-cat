/**
 * 审计明细的展示契约。
 *
 * 这里最怕两件事：把 `changes` 又原样 JSON 甩给管理员（等于没做 diff），
 * 以及把敏感字段的 `***` 当成普通文本吃掉。两种都只在前端体现，后端测试看不到。
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AuditDetail } from './AuditDetail'

describe('AuditDetail', () => {
  it('把字段级 changes 渲染成旧值 → 新值，而不是原始 JSON', () => {
    render(
      <AuditDetail
        detail={{
          changes: {
            amount_cents: [100, 200],
            name: ['旧套餐', '新套餐'],
          },
          fields: ['amount_cents', 'name'],
        }}
      />,
    )

    expect(screen.getByText('amount_cents')).toBeInTheDocument()
    expect(screen.getByText('100')).toBeInTheDocument()
    expect(screen.getByText('200')).toBeInTheDocument()
    expect(screen.getByText('旧套餐')).toBeInTheDocument()
    expect(screen.getByText('新套餐')).toBeInTheDocument()
    expect(screen.getAllByText('→')).toHaveLength(2)
    // fields 只是字段名清单，与 changes 重复，不应再渲染一遍
    expect(screen.queryByText('fields')).not.toBeInTheDocument()
  })

  it('null / 空字符串有可读占位，而不是显示成空白', () => {
    render(
      <AuditDetail
        detail={{ changes: { vip_expire_time: [null, '2026-12-31'], badge: ['', '推荐'] } }}
      />,
    )
    expect(screen.getByText('（空）')).toBeInTheDocument()
    expect(screen.getByText('（空字符串）')).toBeInTheDocument()
  })

  it('敏感值只显示脱敏标记', () => {
    render(<AuditDetail detail={{ changes: { api_key: ['***', '***'] } }} />)
    expect(screen.getAllByText('***')).toHaveLength(2)
  })

  it('权限集合用 + / - 芯片表达增删', () => {
    render(
      <AuditDetail
        detail={{ added: ['user:view', 'role:view'], removed: ['audit:view'] }}
      />,
    )
    expect(screen.getByText('+ user:view')).toBeInTheDocument()
    expect(screen.getByText('+ role:view')).toBeInTheDocument()
    expect(screen.getByText('- audit:view')).toBeInTheDocument()
  })

  it('定时任务的 from/to 只展示真正变化的配置项', () => {
    render(
      <AuditDetail
        detail={{
          job_id: 'check_vip_expiry',
          from: { enabled: true, hour: 2, minute: 0 },
          to: { enabled: false, hour: 2, minute: 0 },
        }}
      />,
    )
    expect(screen.getByText('enabled')).toBeInTheDocument()
    expect(screen.getByText('true')).toBeInTheDocument()
    expect(screen.getByText('false')).toBeInTheDocument()
    expect(screen.queryByText('hour')).not.toBeInTheDocument()
    expect(screen.queryByText('minute')).not.toBeInTheDocument()
  })

  it('无法识别的旧记录回退为键值表', () => {
    render(<AuditDetail detail={{ old_role: 'user', new_role: 'admin' }} />)
    expect(screen.getByText('old_role')).toBeInTheDocument()
    expect(screen.getByText('user')).toBeInTheDocument()
    expect(screen.getByText('new_role')).toBeInTheDocument()
    expect(screen.getByText('admin')).toBeInTheDocument()
  })

  it('来源字段与明细一起展示', () => {
    render(
      <AuditDetail
        detail={{ changes: { role: ['user', 'admin'] } }}
        source={{ ip: '10.0.0.8', request_id: 'req-1', user_agent: 'UA' }}
      />,
    )
    expect(screen.getByText('来源')).toBeInTheDocument()
    expect(screen.getByText('10.0.0.8')).toBeInTheDocument()
    expect(screen.getByText('req-1')).toBeInTheDocument()
    expect(screen.getByText('UA')).toBeInTheDocument()
  })

  it('完全空明细给出明确提示', () => {
    render(<AuditDetail detail={null} />)
    expect(screen.getByText('无结构化明细')).toBeInTheDocument()
  })
})
