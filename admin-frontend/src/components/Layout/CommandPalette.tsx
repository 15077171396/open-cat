import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'
import { Input, Modal, Spin } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { apiGet } from '../../api/client'
import { usePrefsStore } from '../../store/prefsStore'
import { useI18n } from '../../i18n'
import type { MenuTree } from '../../types'

/**
 * 全局命令面板（Ctrl/Cmd + K）：
 * - **页面跳转**：直接复用后端下发的可见菜单树，权限过滤已经在树上完成；
 * - **业务搜索**：教程 / 用户按关键词请求各自的管理列表接口，结果可直接落到编辑页或筛选后的列表；
 * - **快捷动作**：新建教程、切主题 / 布局等高频小动作，不必先找入口。
 *
 * 这里不新增后端聚合接口：两个列表接口本来就有权限与关键词参数，
 * 复制一套搜索规则只会让「命令面板搜到的」与「列表页搜到的」慢慢不一致。
 */

interface PaletteCommand {
  key: string
  label: string
  hint: string
  keywords?: string
  run: () => void
}

interface PaletteSection {
  title: string
  items: PaletteCommand[]
}

interface RemoteTutorial {
  id: number
  title: string
  category?: string | null
}

interface RemoteUser {
  id: number
  account: string
  role?: string
}

export interface CommandPaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  menuTree: MenuTree
  permissions: string[] | null
  isSuper: boolean
}

export default function CommandPalette({
  open,
  onOpenChange,
  menuTree,
  permissions,
  isSuper,
}: CommandPaletteProps) {
  const navigate = useNavigate()
  const setMode = usePrefsStore((s) => s.setMode)
  const setLayout = usePrefsStore((s) => s.setLayout)
  const { t, setLocale } = useI18n()
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [tutorials, setTutorials] = useState<RemoteTutorial[]>([])
  const [users, setUsers] = useState<RemoteUser[]>([])
  const [searching, setSearching] = useState(false)
  const activeRef = useRef<HTMLButtonElement | null>(null)

  const can = useCallback(
    (code: string) => isSuper || (permissions?.includes(code) ?? false),
    [isSuper, permissions],
  )
  const canEditTutorial = can('tutorial:edit')
  const canSearchTutorial = can('tutorial:view')
  const canSearchUser = can('user:view')

  useEffect(() => {
    if (!open) return
    setQuery('')
    setActive(0)
    setTutorials([])
    setUsers([])
  }, [open])

  // 全局唤起：在输入框里也生效（Ctrl+K 是命令面板的固定键位）
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        onOpenChange(!open)
        return
      }
      if (event.key === 'Escape' && open) {
        event.preventDefault()
        onOpenChange(false)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onOpenChange])

  // 远程搜索：250ms 防抖；请求失败只表现为「没有结果」，不弹 toast 打断输入
  useEffect(() => {
    if (!open) return
    const keyword = query.trim()
    if (!keyword) {
      setTutorials([])
      setUsers([])
      setSearching(false)
      return
    }
    let cancelled = false
    setSearching(true)
    const timer = window.setTimeout(async () => {
      const [tutorialResult, userResult] = await Promise.all([
        canSearchTutorial
          ? apiGet<{ items: RemoteTutorial[] }>('/admin/tutorials', {
              params: { page: 1, page_size: 5, keyword },
            }).catch(() => null)
          : Promise.resolve(null),
        canSearchUser
          ? apiGet<{ items: RemoteUser[] }>('/admin/users', {
              params: { page: 1, page_size: 5, keyword },
            }).catch(() => null)
          : Promise.resolve(null),
      ])
      if (cancelled) return
      setTutorials(tutorialResult?.items ?? [])
      setUsers(userResult?.items ?? [])
      setSearching(false)
    }, 250)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [open, query, canSearchTutorial, canSearchUser])

  const pageCommands = useMemo<PaletteCommand[]>(() => {
    const commands: PaletteCommand[] = []
    for (const group of menuTree.groups) {
      for (const leaf of group.items) {
        commands.push({
          key: `page:${leaf.key}`,
          label: leaf.label,
          hint: group.label,
          keywords: `${leaf.label} ${group.label} ${leaf.key}`,
          run: () => {
            if (leaf.url) window.open(leaf.url, '_blank', 'noopener,noreferrer')
            else navigate(leaf.key)
          },
        })
      }
    }
    for (const leaf of menuTree.footer) {
      commands.push({
        key: `page:${leaf.key}`,
        label: leaf.label,
        hint: t('command.hintShortcut'),
        keywords: `${leaf.label} ${leaf.key}`,
        run: () => navigate(leaf.key),
      })
    }
    return commands
  }, [menuTree, navigate, t])

  const actionCommands = useMemo<PaletteCommand[]>(() => {
    const actions: PaletteCommand[] = []
    if (can('tutorial:create')) {
      actions.push({
        key: 'action:new-tutorial',
        label: t('command.newTutorial'),
        hint: t('command.hintTutorial'),
        keywords: '新建 教程 创建 create new tutorial',
        run: () => navigate('/tutorials/new'),
      })
    }
    actions.push(
      {
        key: 'action:theme-light',
        label: t('command.themeLight'),
        hint: t('command.hintAppearance'),
        keywords: '主题 亮色 light theme appearance',
        run: () => setMode('light'),
      },
      {
        key: 'action:theme-dark',
        label: t('command.themeDark'),
        hint: t('command.hintAppearance'),
        keywords: '主题 暗色 dark theme appearance',
        run: () => setMode('dark'),
      },
      {
        key: 'action:theme-system',
        label: t('command.themeSystem'),
        hint: t('command.hintAppearance'),
        keywords: '主题 系统 system theme appearance',
        run: () => setMode('system'),
      },
      {
        key: 'action:layout-top',
        label: t('command.layoutTop'),
        hint: t('command.hintLayout'),
        keywords: '布局 导航 顶部 top layout navigation',
        run: () => setLayout('top'),
      },
      {
        key: 'action:layout-side',
        label: t('command.layoutSide'),
        hint: t('command.hintLayout'),
        keywords: '布局 导航 侧边 side layout navigation',
        run: () => setLayout('side'),
      },
      {
        key: 'action:language-zh',
        label: t('command.languageZh'),
        hint: t('layout.language'),
        keywords: '语言 中文 简体 chinese zh language locale',
        run: () => setLocale('zh-CN'),
      },
      {
        key: 'action:language-en',
        label: t('command.languageEn'),
        hint: t('layout.language'),
        keywords: '语言 英文 english en language locale',
        run: () => setLocale('en-US'),
      },
    )
    return actions
  }, [can, navigate, setMode, setLayout, setLocale, t])

  const normalizedQuery = query.trim().toLowerCase()
  const matches = useCallback(
    (command: PaletteCommand) => {
      if (!normalizedQuery) return true
      return `${command.label} ${command.keywords ?? ''}`.toLowerCase().includes(normalizedQuery)
    },
    [normalizedQuery],
  )

  const filteredPages = useMemo(
    () => pageCommands.filter(matches),
    [pageCommands, matches],
  )

  const filteredActions = useMemo(
    () => actionCommands.filter(matches),
    [actionCommands, matches],
  )

  const tutorialCommands = useMemo<PaletteCommand[]>(
    () =>
      tutorials.map((tutorial) => ({
        key: `tutorial:${tutorial.id}`,
        label: tutorial.title,
        hint: `${t('command.tutorials')}${tutorial.category ? ` · ${tutorial.category}` : ''}`,
        run: () =>
          navigate(
            canEditTutorial
              ? `/tutorials/${tutorial.id}/edit`
              : `/tutorials?keyword=${encodeURIComponent(tutorial.title)}`,
          ),
      })),
    [tutorials, canEditTutorial, navigate, t],
  )

  const userCommands = useMemo<PaletteCommand[]>(
    () =>
      users.map((user) => ({
        key: `user:${user.id}`,
        label: user.account,
        hint: `${t('command.users')}${user.role ? ` · ${user.role}` : ''}`,
        run: () => navigate(`/users?keyword=${encodeURIComponent(user.account)}`),
      })),
    [users, navigate, t],
  )

  const sections = useMemo<PaletteSection[]>(
    () =>
      [
        { title: t('command.pages'), items: filteredPages },
        { title: t('command.actions'), items: filteredActions },
        { title: t('command.tutorials'), items: tutorialCommands },
        { title: t('command.users'), items: userCommands },
      ].filter((section) => section.items.length > 0),
    [filteredPages, filteredActions, tutorialCommands, userCommands, t],
  )

  const flatItems = useMemo(() => sections.flatMap((section) => section.items), [sections])

  useEffect(() => {
    setActive(0)
  }, [query])

  useEffect(() => {
    setActive((index) => Math.min(index, Math.max(0, flatItems.length - 1)))
  }, [flatItems.length])

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest' })
  }, [active])

  const execute = (command: PaletteCommand | undefined) => {
    if (!command) return
    onOpenChange(false)
    command.run()
  }

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive((index) => Math.min(index + 1, Math.max(0, flatItems.length - 1)))
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((index) => Math.max(index - 1, 0))
      return
    }
    if (event.key === 'Enter') {
      event.preventDefault()
      execute(flatItems[active])
    }
  }

  let flatIndex = -1

  return (
    <Modal
      open={open}
      onCancel={() => onOpenChange(false)}
      footer={null}
      closable={false}
      keyboard={false}
      destroyOnHidden
      width={640}
      rootClassName="cmdk-modal"
      styles={{ body: { padding: 0 } }}
    >
      <Input
        autoFocus
        size="large"
        variant="borderless"
        prefix={<SearchOutlined />}
        suffix={searching ? <Spin size="small" /> : null}
        placeholder={t('command.placeholder')}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={handleKeyDown}
        className="cmdk-input"
      />

      <div className="cmdk-list">
        {sections.map((section) => (
          <div className="cmdk-section" key={section.title}>
            <div className="cmdk-section__title">{section.title}</div>
            {section.items.map((item) => {
              flatIndex += 1
              const index = flatIndex
              const isActive = index === active
              return (
                <button
                  key={item.key}
                  ref={isActive ? activeRef : undefined}
                  type="button"
                  className={`cmdk-item${isActive ? ' is-active' : ''}`}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => execute(item)}
                >
                  <span className="cmdk-item__label">{item.label}</span>
                  <span className="cmdk-item__hint">{item.hint}</span>
                </button>
              )
            })}
          </div>
        ))}

        {!searching && flatItems.length === 0 && (
          <div className="cmdk-empty">
            {normalizedQuery ? t('command.noResults') : t('command.noQuery')}
          </div>
        )}
      </div>

      <div className="cmdk-footer">
        <span>{t('command.footerHint')}</span>
        <span>{t('command.shortcutHint')}</span>
      </div>
    </Modal>
  )
}
