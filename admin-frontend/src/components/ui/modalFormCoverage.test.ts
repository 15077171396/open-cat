/**
 * 表单弹窗的**覆盖率棘轮**。
 *
 * 背景：`ModalForm` 早就写好了，但采用率长期是 0 —— 18 个页面各自手写 `<Modal>` +
 * `<Form>` + `validateFields` + `confirmLoading` + `resetFields`，同一个 bug（编辑 A 后
 * 新增还留着 A 的值）在 10 个页面里各修一遍。本文件锁住「别退回去」。
 *
 * 判定口径：一个文件里同时出现 `<Modal` 与 `<Form`，且没有用 `<ModalForm>`，
 * 就视为手写表单弹窗。允许逃生，但必须在这里登记理由 —— 新增白名单是一次显式决策。
 */

import { describe, expect, it } from 'vitest'

const SOURCES = import.meta.glob('../../pages/**/*.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/**
 * 仍然手写「弹窗 + 表单」的页面（键 = 相对 src/pages 的路径，值 = 理由）。
 *
 * 只列**真被判定为手写**的文件：不含 `<Form` 的纯展示弹窗（素材预览、分类的受控
 * Input、关闭 2FA 的单密码框）不在此列 —— 它们本来就不该套 ModalForm。
 */
const RAW_MODAL_FORM_ALLOWED: Record<string, string> = {
  'AuthRequests/List.tsx': '审核弹窗：正文是只读的申请信息 + 审批结论，表单只是附带的备注字段',
  'Feedback/List.tsx': '反馈详情弹窗：主体是只读正文与状态处理，不是「填表新建实体」',
  'Moderation/List.tsx': '机审复核弹窗：审批型（通过/驳回 + 备注），无实体字段',
  'Users/UserEditModal.tsx': '按 user.id 变化回填的自定义生命周期，套 ModalForm 的「打开即 reset」会丢状态',
  'Users/UserRbacModal.tsx': '权限树勾选：值是一个权限码数组，不是 Form.Item 字段',
}

function pageFiles(): string[] {
  return Object.keys(SOURCES)
    .filter((p) => p.startsWith('../../pages/'))
    .map((p) => p.replace('../../pages/', ''))
}

/** 手写「Modal 里套 Form」的文件（ModalForm 内部自己就是 Modal + Form，不算） */
function handwrittenModalForm(): string[] {
  return pageFiles().filter((f) => {
    const src = SOURCES[`../../pages/${f}`] ?? ''
    return src.includes('<Modal') && src.includes('<Form') && !src.includes('<ModalForm')
  })
}

describe('表单弹窗覆盖率', () => {
  it('扫描范围本身要有内容（防止 glob 失效导致空断言）', () => {
    expect(pageFiles().length).toBeGreaterThan(20)
  })

  it('手写表单弹窗的页面只剩白名单里的', () => {
    const offenders = handwrittenModalForm().filter((f) => !(f in RAW_MODAL_FORM_ALLOWED))
    expect(
      offenders,
      `这些页面应改用 <ModalForm>（校验/loading/回填/清空都已内置）：${offenders.join(', ')}`,
    ).toEqual([])
  })

  it('白名单里的文件必须仍然存在（删了页面要同步删条目）', () => {
    for (const f of Object.keys(RAW_MODAL_FORM_ALLOWED)) {
      expect(pageFiles(), `白名单条目 ${f} 已不存在`).toContain(f)
    }
  })

  it('采用 ModalForm 的页面数不再回退', () => {
    // 只是「不少于下限」：新增页面套壳会让数字变大，不需要改这里
    const users = pageFiles().filter((f) => (SOURCES[`../../pages/${f}`] ?? '').includes('<ModalForm'))
    expect(users.length).toBeGreaterThanOrEqual(10)
  })
})
