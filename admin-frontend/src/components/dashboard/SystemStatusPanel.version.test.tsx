/**
 * 版本与升级区块：待执行迁移/有新版本时必须给出「先备份」的提醒；
 * 未配置更新源时明确说「不检查更新」，而不是显示成检查失败。
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import SystemStatusPanel from './SystemStatusPanel'
import { normalizeSystemStatus } from './dashboardUtils'

const base = normalizeSystemStatus({})

describe('SystemStatusPanel 版本与升级', () => {
  it('有待执行迁移时显示数量和备份提醒', () => {
    render(
      <SystemStatusPanel
        data={base}
        version={{
          version: '1.0.0',
          app_env: 'production',
          migration: { current: 'old', head: 'new', pending: 2, in_sync: false },
          latest_release: { configured: true, checked: true, tag: 'v1.2.0', error: null },
          update_available: true,
          backup_hint: '升级前请先备份数据库（scripts/backup_mysql.sh）',
        }}
      />,
    )
    expect(screen.getByText('版本与升级')).toBeInTheDocument()
    expect(screen.getByText(/待执行 2 个/)).toBeInTheDocument()
    expect(screen.getByText(/升级前请先备份数据库/)).toBeInTheDocument()
    expect(screen.getByText('有新版本')).toBeInTheDocument()
  })

  it('未配置更新源时明确提示不检查更新', () => {
    render(
      <SystemStatusPanel
        data={base}
        version={{
          version: '1.0.0',
          migration: { current: 'head', head: 'head', pending: 0, in_sync: true },
          latest_release: { configured: false, checked: false, error: null },
          update_available: null,
        }}
      />,
    )
    expect(screen.getByText('未配置 APP_UPDATE_REPO，不检查更新')).toBeInTheDocument()
    expect(screen.queryByText(/升级前请先备份数据库/)).not.toBeInTheDocument()
  })

  it('版本接口失败时只影响本区块', () => {
    render(<SystemStatusPanel data={base} version={null} versionFailed />)
    expect(screen.getByText(/版本接口不可用/)).toBeInTheDocument()
    expect(screen.getByText('依赖服务')).toBeInTheDocument()
  })
})
