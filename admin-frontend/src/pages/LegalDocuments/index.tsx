import { useState } from 'react'
import { Button, Empty, Popconfirm, Tag, Tooltip, Typography } from 'antd'
import { FileProtectOutlined, ReloadOutlined } from '@ant-design/icons'
import SettingsEditor from '../../components/settings/SettingsEditor'
import { SettingField } from '../../components/settings/SettingField'
import type { SettingDef, SettingsPayload } from '../../components/settings/types'
import { formatDateTime } from '../../utils/datetime'
import { docState, plainTextLength, type LegalDocMeta } from './legalDoc'
import './index.css'

interface LegalDocumentsPayload extends SettingsPayload {
  documents: LegalDocMeta[]
}

/** 默认选中第一份（隐私政策）；documents 顺序由后端 LEGAL_DOC_TYPES 决定 */
const DEFAULT_DOC_KEY = 'legal_privacy'

/**
 * 法律文档：隐私政策 / 用户协议 / 免责声明。
 *
 * 2026-10-27 从「系统设置 → 法律文档」抽出，动机：
 * 1. 权限面 —— 改对外公示的合规文本通常只由负责人做，不该连带 `setting:update`
 *    （那份权限同时能改 COS 密钥、支付商户私钥）；
 * 2. 信息面 —— 原来的 tab 里只有三段并排的富文本编辑器，看不出「哪份是自定义的、
 *    哪份还挂着系统默认文案」「多大」「上次谁什么时候改的」。左侧导航把这几件事显性化，
 *    因为法务文本改错的代价远高于改错一个开关。
 *
 * 布局沿用「模型管理」的左侧导航 + 右侧详情：一次只挂载一个 Quill 实例（三个编辑器
 * 同时挂载在低配机器上会有明显卡顿），且切文档不会丢失其他文档的未保存草稿 ——
 * 草稿在 `SettingsEditor` 里是全量的，这里只是选择渲染哪一份。
 */
export default function LegalDocumentsPage() {
  const [activeKey, setActiveKey] = useState(DEFAULT_DOC_KEY)

  return (
    <SettingsEditor<LegalDocumentsPayload>
      endpoint="/admin/legal-documents"
      title="法律文档"
      icon={<FileProtectOutlined />}
      savePerm="legal_doc:update"
      // 法务文本有独立 Redis 缓存（legal_doc:*），保存后由后端主动失效，前台即时可见
      saveSuccessText="法律文档已保存，前台页脚刷新后即时生效"
      loadErrorText="加载法律文档失败"
    >
      {({ groups, draft, saved, dirtyGroups, isDirty, setValue, payload }) => {
        const docs = payload.documents || []
        const group = groups[0]
        const current = docs.find((d) => d.key === activeKey) || docs[0]
        const def: SettingDef | undefined = current
          ? payload.definitions.find((d) => d.key === current.key)
          : undefined

        if (!current || !def) {
          return <Empty description="没有可编辑的法律文档" style={{ padding: '60px 0' }} />
        }

        const defaultValue = def.default
        const isDefaultText = !(draft[current.key] || '').trim()

        return (
          <>
            <div className="legal-intro">
              <Typography.Text type="secondary">{group?.description}</Typography.Text>
              {isDirty && (
                <div className="legal-banner">
                  有未保存的修改（
                  {docs.filter((d) => dirtyGroups.has(d.key)).map((d) => d.title).join('、')}），
                  点右上角「保存设置」后生效。
                </div>
              )}
            </div>

            <div className="legal-layout">
              <nav className="legal-docs" aria-label="法律文档列表">
                <div className="legal-docs__head">
                  <span className="legal-docs__title">文档</span>
                  <span className="legal-docs__count">{docs.length}</span>
                </div>
                {docs.map((doc) => {
                  const docDef = payload.definitions.find((d) => d.key === doc.key)
                  const st = docState(
                    doc,
                    draft[doc.key] ?? '',
                    saved[doc.key] ?? '',
                    docDef?.default || '',
                  )
                  const active = doc.key === current.key
                  return (
                    <button
                      type="button"
                      key={doc.key}
                      className={`legal-doc${active ? ' legal-doc--active' : ''}`}
                      onClick={() => setActiveKey(doc.key)}
                    >
                      <span className="legal-doc__row">
                        <span className="legal-doc__name">{doc.title}</span>
                        {st.pending && <span className="legal-doc__dot" title="有未保存修改" />}
                      </span>
                      <span className="legal-doc__meta">
                        {st.isCustom ? (
                          <Tag color="blue" style={{ marginInlineEnd: 6 }}>
                            自定义
                          </Tag>
                        ) : (
                          <Tag style={{ marginInlineEnd: 6 }}>系统默认</Tag>
                        )}
                        {st.length} 字
                      </span>
                    </button>
                  )
                })}
                <div className="legal-docs__foot">
                  清空正文并保存 = 前台回退系统默认文案，不是「空白页」。
                </div>
              </nav>

              <section className="legal-editor">
                <header className="legal-editor__head">
                  <div>
                    <h3 className="legal-editor__title">{current.title}</h3>
                    <div className="legal-editor__meta">
                      <span>前台位置：{current.public_hint}</span>
                      <span>
                        最后保存：
                        {current.is_custom ? formatDateTime(current.updated_at) : '从未修改（系统默认）'}
                      </span>
                    </div>
                  </div>
                  <Popconfirm
                    title="恢复系统默认文案？"
                    description="会清空当前文档的正文，保存后前台展示内置默认文案。"
                    okText="清空正文"
                    cancelText="取消"
                    disabled={isDefaultText}
                    onConfirm={() => setValue(current.key, '')}
                  >
                    <Tooltip title={isDefaultText ? '当前已是系统默认文案' : '清空正文，保存后回退默认'}>
                      <Button icon={<ReloadOutlined />} disabled={isDefaultText}>
                        恢复默认文案
                      </Button>
                    </Tooltip>
                  </Popconfirm>
                </header>

                {isDefaultText && (
                  <div className="legal-editor__note">
                    当前未自定义：前台展示的是系统内置默认文案（{plainTextLength(defaultValue)} 字）。
                    直接编辑并保存即成为自定义文本。
                  </div>
                )}

                <SettingField
                  def={def}
                  value={draft[current.key] ?? ''}
                  onChange={(v) => setValue(current.key, v)}
                />
              </section>
            </div>
          </>
        )
      }}
    </SettingsEditor>
  )
}
