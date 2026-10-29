/** 设置类页面（系统设置 / 登录管理）共用的类型。 */

/** 后端 SETTING_DEFINITIONS 输出的设置项定义（两个接口同构） */
export interface SettingDef {
  key: string
  label: string
  type:
    | 'text'
    | 'textarea'
    | 'image'
    | 'url'
    | 'richtext'
    | 'password'
    | 'secret_text'
    | 'number'
    | 'json'
    | 'bool'
    | 'select'
  group: string
  group_label: string
  default: string
  /** select 类型的候选值 */
  options?: { value: string; label: string }[]
  /** 后端给出的补充说明（单位 / 取值范围 / 生效时机） */
  help_text?: string
  /** 密钥类：服务器 .env 是否已有值（值为空，只给提示） */
  has_default?: boolean
}

/**
 * 分组元信息由后端下发（`SETTING_GROUPS`，顺序即区块顺序）。
 *
 * 此前这里是 `GROUP_ORDER` + `GROUP_DESCRIPTIONS` 两个手写常量，且渲染是
 * `GROUP_ORDER.filter(g => map.has(g))` —— 后端新增一个分组而前端没同步时，
 * **整组设置会被静默丢弃**（接口返回了、后台看不到、也没有任何报错）。
 * 现在按后端返回的顺序渲染；万一遇到未在 groups 里声明的分组，
 * 追加到末尾而不是丢掉（宁可位置难看，不可让人以为设置项不存在）。
 */
export interface SettingGroupMeta {
  key: string
  label: string
  description?: string
  /** 归属的管理界面（settings / login），仅用于排查，不参与渲染 */
  surface?: string
}

/** 两个设置接口的公共载荷 */
export interface SettingsPayload {
  definitions: SettingDef[]
  groups?: SettingGroupMeta[]
  values: Record<string, string>
}

/** 渲染用的分组视图：元信息 + 该组下的设置项 */
export interface SettingGroupView {
  key: string
  label: string
  description: string
  items: SettingDef[]
}
