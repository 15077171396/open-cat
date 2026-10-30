import { Button, Dropdown, Space, Tooltip } from 'antd'
import { CloseOutlined, MoreOutlined, ReloadOutlined } from '@ant-design/icons'
import { HOME_TAB } from '../../utils/pageTabs'
import { useI18n } from '../../i18n'

export interface PageTabsProps {
  tabs: string[]
  activeKey: string
  getTitle: (key: string) => string
  onChange: (key: string) => void
  onClose: (key: string) => void
  onCloseOthers: (key: string) => void
  onCloseAll: () => void
  onRefresh: () => void
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
}: PageTabsProps) {
  const { t } = useI18n()

  return (
    <div className="page-tabs">
      <div className="page-tabs__list" role="tablist" aria-label={t('tabs.label')}>
        {tabs.map((key) => {
          const title = getTitle(key)
          const active = key === activeKey
          return (
            <div
              key={key}
              className={`page-tabs__item${active ? ' is-active' : ''}`}
              role="tab"
              aria-selected={active}
            >
              <button type="button" className="page-tabs__label" onClick={() => onChange(key)}>
                {title}
              </button>
              {key !== HOME_TAB && (
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
        <Dropdown
          placement="bottomRight"
          menu={{
            items: [
              { key: 'others', label: t('tabs.closeOthers'), onClick: () => onCloseOthers(activeKey) },
              { key: 'all', label: t('tabs.closeAll'), onClick: onCloseAll },
            ],
          }}
        >
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
