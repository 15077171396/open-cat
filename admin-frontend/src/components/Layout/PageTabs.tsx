import { Button, Dropdown, Space, Tooltip } from 'antd'
import type { MenuProps } from 'antd'
import { CloseOutlined, MoreOutlined, PushpinFilled, PushpinOutlined, ReloadOutlined } from '@ant-design/icons'
import { HOME_TAB } from '../../utils/pageTabs'
import { useI18n } from '../../i18n'
import type { TabStyle } from '../../store/prefsStore'

export interface PageTabsProps {
  tabs: string[]
  activeKey: string
  getTitle: (key: string) => string
  onChange: (key: string) => void
  onClose: (key: string) => void
  onCloseOthers: (key: string) => void
  onCloseAll: () => void
  onRefresh: () => void
  /** 标签页风格（胶囊 / 卡片 / 谷歌） */
  tabStyle?: TabStyle
  /** 已固定的标签（关闭其他/全部时保留） */
  pinned?: string[]
  /** 刷新指定标签（演示站标签菜单里的「刷新」）；缺省回退 onRefresh */
  onRefreshTab?: (key: string) => void
  onTogglePin?: (key: string) => void
  onCloseLeft?: (key: string) => void
  onCloseRight?: (key: string) => void
}

export default function PageTabs({
  tabs,
  activeKey,
  getTitle,
  onChange,
  onClose,
  onCloseOthers,
  onCloseAll,
  onRefresh,
  tabStyle = 'google',
  pinned = [],
  onRefreshTab,
  onTogglePin,
  onCloseLeft,
  onCloseRight,
}: PageTabsProps) {
  const { t } = useI18n()

  /** 单个标签的操作菜单：演示站右键/下拉里的刷新、固定、关闭左右/其他/全部 */
  const tabMenu = (key: string): MenuProps => {
    const isPinned = pinned.includes(key)
    const isHome = key === HOME_TAB
    return {
      items: [
        {
          key: 'refresh',
          icon: <ReloadOutlined />,
          label: t('tabs.refreshTab'),
          onClick: () => (onRefreshTab ? onRefreshTab(key) : onRefresh()),
        },
        {
          key: 'pin',
          icon: isPinned ? <PushpinFilled /> : <PushpinOutlined />,
          label: isPinned ? t('tabs.unpin') : t('tabs.pin'),
          disabled: isHome || !onTogglePin,
          onClick: () => onTogglePin?.(key),
        },
        { type: 'divider' },
        {
          key: 'close',
          label: t('tabs.closeCurrent'),
          disabled: isHome || isPinned,
          onClick: () => onClose(key),
        },
        {
          key: 'left',
          label: t('tabs.closeLeft'),
          disabled: !onCloseLeft,
          onClick: () => onCloseLeft?.(key),
        },
        {
          key: 'right',
          label: t('tabs.closeRight'),
          disabled: !onCloseRight,
          onClick: () => onCloseRight?.(key),
        },
        { type: 'divider' },
        { key: 'others', label: t('tabs.closeOthers'), onClick: () => onCloseOthers(key) },
        { key: 'all', label: t('tabs.closeAll'), onClick: onCloseAll },
      ],
    }
  }

  return (
    <div className={`page-tabs page-tabs--${tabStyle}`}>
      <div className="page-tabs__list" role="tablist" aria-label={t('tabs.label')}>
        {tabs.map((key) => {
          const title = getTitle(key)
          const active = key === activeKey
          const isPinned = pinned.includes(key)
          return (
            <Dropdown key={key} menu={tabMenu(key)} trigger={['contextMenu']}>
              <div
                className={`page-tabs__item${active ? ' is-active' : ''}${isPinned ? ' is-pinned' : ''}`}
                role="tab"
                aria-selected={active}
                title={title}
              >
                {isPinned && <PushpinFilled className="page-tabs__pin" />}
                <button type="button" className="page-tabs__label" onClick={() => onChange(key)}>
                  {title}
                </button>
                {key !== HOME_TAB && !isPinned && (
                  <button
                    type="button"
                    className="page-tabs__close"
                    aria-label={t('tabs.close', { title })}
                    onClick={(event) => {
                      event.stopPropagation()
                      onClose(key)
                    }}
                  >
                    <CloseOutlined />
                  </button>
                )}
              </div>
            </Dropdown>
          )
        })}
      </div>
      <Space size={2} className="page-tabs__actions">
        <Tooltip title={t('tabs.refresh')}>
          <Button
            type="text"
            size="small"
            icon={<ReloadOutlined />}
            aria-label={t('tabs.refresh')}
            onClick={onRefresh}
          />
        </Tooltip>
        <Dropdown placement="bottomRight" menu={tabMenu(activeKey)} trigger={['click']}>
          <Button
            type="text"
            size="small"
            icon={<MoreOutlined />}
            aria-label={t('tabs.more')}
          />
        </Dropdown>
      </Space>
    </div>
  )
}
