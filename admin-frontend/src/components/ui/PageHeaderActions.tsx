import { Fragment, isValidElement, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode, RefObject } from 'react'
import { Button, Popover, Space, Tooltip } from 'antd'
import { EllipsisOutlined } from '@ant-design/icons'

/**
 * 页头操作区：一行放不下时，把溢出的操作收进最右侧的「更多」弹层。
 *
 * 为什么用 ResizeObserver 实测而不是断点：
 * 每个页面的操作项数量、文案长度、搜索框宽度都不同（用户管理 3 项、定时任务 1 项），
 * 写 `@media (max-width: 1200px)` 只能猜个大概，窄屏上仍然会出现半截按钮。
 * 这里量「页头容器宽度 + 每个操作项宽度」，算出能放下几项，再把放不下的收起来。
 *
 * 收起策略：纯展示项最先收 → 筛选/输入类 → 普通操作 → 主操作（type="primary"）；
 * 同一优先级内按原顺序从后往前收。
 *
 * 测量方式：在页头里放一层 `visibility: hidden` 的测量行（同样的 gap、同样的控件），
 * 量完即用。测试环境（jsdom）量不到宽度，会直接渲染全部操作，不产生任何重复 DOM。
 */

/** 操作项之间与页头 actions 的 gap 保持一致 */
const GAP = 8
/** 留一点余量，避免像素取整后刚好溢出 1px 产生横向滚动 */
const SAFETY = 4

/** 把 actions 摊平成「一个控件一项」，Fragment / 数组会被展开，其余原地保留 */
function toItems(actions: ReactNode | undefined): ReactNode[] {
  const out: ReactNode[] = []
  const walk = (node: ReactNode) => {
    if (node === null || node === undefined || typeof node === 'boolean') return
    if (typeof node === 'string' || typeof node === 'number') {
      // 纯空白文本节点不参与排版，直接丢弃
      if (typeof node === 'string' && !node.trim()) return
      out.push(node)
      return
    }
    if (Array.isArray(node)) {
      node.forEach(walk)
      return
    }
    if (isValidElement(node) && node.type === Fragment) {
      walk((node.props as { children?: ReactNode }).children)
      return
    }
    // 有些页面把操作包在 <Space> 里；拆开才能逐项判断溢出与主次
    if (isValidElement(node) && node.type === Space) {
      walk((node.props as { children?: ReactNode }).children)
      return
    }
    out.push(node)
  }
  walk(actions)
  return out
}

/** 收起优先级：数字越小越先被收进「更多」 */
const TIER_DISPLAY = 0
const TIER_FILTER = 1
const TIER_ACTION = 2
const TIER_PRIMARY = 3

/**
 * 从**渲染后的测量节点**判断这一项的收起优先级。
 *
 * 为什么不在 React 元素树上判断：操作项可能是自定义组件（ExportButton、PermGuard），
 * 拿不到最终的 `type="primary"`、也看不出它是不是输入框。测量层里已经是真实 DOM，
 * 直接看类名最可靠。
 *
 * - 主操作（`.ant-btn-primary`）最后收起；
 * - 筛选/输入类（搜索框、Select、Segmented、日期选择、开关）先于按钮收起；
 * - 纯展示（Tag、说明文字）最先收起；
 * - 其余可点击项（普通按钮、链接）居中。
 */
function tierOf(el: HTMLDivElement | null): number {
  if (!el) return TIER_DISPLAY
  if (el.querySelector('.ant-btn-primary')) return TIER_PRIMARY
  if (
    el.querySelector(
      'input, textarea, .ant-select, .ant-segmented, .ant-picker, .ant-switch, .ant-radio-group, .ant-checkbox-group, .ant-upload',
    )
  ) {
    return TIER_FILTER
  }
  if (el.querySelector('button, a[href], [role="button"]')) return TIER_ACTION
  return TIER_DISPLAY
}

function sameIndices(a: number[], b: number[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i])
}

interface PageHeaderActionsProps {
  actions: ReactNode
  /** 页头容器，用来量「一行可用宽度」 */
  containerRef: RefObject<HTMLDivElement | null>
}

export default function PageHeaderActions({ actions, containerRef }: PageHeaderActionsProps) {
  const items = toItems(actions)
  const itemCount = items.length

  const [hidden, setHidden] = useState<number[]>([])
  /** 量不到宽度（jsdom / display:none）时不做任何收缩，也不渲染测量层，避免重复 DOM */
  const [canMeasure, setCanMeasure] = useState(false)
  const [open, setOpen] = useState(false)

  const measureItemRefs = useRef<Array<HTMLDivElement | null>>([])
  const measureInnerRef = useRef<HTMLDivElement>(null)
  const measureMoreRef = useRef<HTMLDivElement>(null)
  const moreContentRef = useRef<HTMLDivElement>(null)

  // 测量层渲染前先确认页头有真实宽度；jsdom 下为 0，会保持 canMeasure=false
  useLayoutEffect(() => {
    const width = containerRef.current?.getBoundingClientRect().width ?? 0
    setCanMeasure(width > 0)
  }, [containerRef])

  /** 重新计算哪些项放得下 */
  const recompute = useCallback(() => {
    // 只有一项时不做收缩：收起后只剩「更多」，等于把唯一的操作藏起来
    if (itemCount <= 1) {
      setHidden((prev) => (prev.length === 0 ? prev : []))
      return
    }

    const containerWidth = containerRef.current?.getBoundingClientRect().width ?? 0
    if (containerWidth <= 0) {
      setHidden((prev) => (prev.length === 0 ? prev : []))
      return
    }

    const widths = Array.from({ length: itemCount }, (_, i) =>
      measureItemRefs.current[i]?.getBoundingClientRect().width ?? 0,
    )
    // 测量行还没布局出来（隐藏容器、字体未加载等）：先全部露出来，等尺寸变化再算
    if (!widths.length || widths.every((w) => w === 0)) {
      setHidden((prev) => (prev.length === 0 ? prev : []))
      return
    }

    const moreWidth = measureMoreRef.current?.getBoundingClientRect().width ?? 0
    // 收起优先级：展示 < 筛选 < 普通操作 < 主操作；同层从后往前收
    const tiers = widths.map((_, i) => tierOf(measureItemRefs.current[i]))
    const hideOrder = widths.map((_, i) => i).sort((a, b) => tiers[a] - tiers[b] || b - a)

    const totalWidth = (visible: number[]) =>
      visible.reduce((sum, i) => sum + widths[i], 0) +
      GAP * Math.max(0, visible.length - 1) +
      (visible.length < itemCount ? (visible.length > 0 ? GAP : 0) + moreWidth : 0)

    let visible = widths.map((_, i) => i)
    const limit = containerWidth - SAFETY
    while (visible.length > 0 && totalWidth(visible) > limit) {
      const victim = hideOrder.find((i) => visible.includes(i))
      if (victim === undefined) break
      visible = visible.filter((i) => i !== victim)
    }

    const nextHidden = widths.map((_, i) => i).filter((i) => !visible.includes(i))
    setHidden((prev) => (sameIndices(prev, nextHidden) ? prev : nextHidden))
  }, [containerRef, itemCount])

  // 有真实宽度后才渲染测量层；测量层出现后再算一遍（useLayoutEffect 在绘制前）
  useLayoutEffect(() => {
    if (!canMeasure) return
    recompute()
  }, [canMeasure, recompute])

  // 容器宽度、测量行宽度（文案/loading 变化会改变行宽）都要重算
  useEffect(() => {
    if (!canMeasure) return
    const container = containerRef.current
    const inner = measureInnerRef.current
    const ro =
      typeof ResizeObserver !== 'undefined'
        ? new ResizeObserver(() => {
            recompute()
          })
        : null
    if (ro && container) ro.observe(container)
    if (ro && inner) ro.observe(inner)
    const onResize = () => recompute()
    window.addEventListener('resize', onResize)
    return () => {
      ro?.disconnect()
      window.removeEventListener('resize', onResize)
    }
  }, [canMeasure, containerRef, recompute])

  // 溢出项被全部放回可见区时，收起的弹层要跟着关掉
  useEffect(() => {
    if (open && hidden.length === 0) setOpen(false)
  }, [open, hidden.length])

  const hiddenSet = new Set(hidden)
  const hiddenItems = hidden.map((i) => items[i])

  const handleOpenChange = (next: boolean) => {
    setOpen(next)
    if (!next) return
    // 展开后把焦点送进弹层第一项，键盘用户不用再 Tab 一圈
    requestAnimationFrame(() => {
      const focusable = moreContentRef.current?.querySelector<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )
      focusable?.focus()
    })
  }

  return (
    <>
      <div className="adm-header__actions" data-managed={hidden.length > 0 ? 'true' : undefined}>
        {items.map((node, i) =>
          hiddenSet.has(i) ? null : (
            <div className="adm-header__action-item" key={i}>
              {node}
            </div>
          ),
        )}

        {hidden.length > 0 && (
          <Tooltip title="更多操作">
            <Popover
              trigger="click"
              placement="bottomRight"
              open={open}
              onOpenChange={handleOpenChange}
              content={
                <div className="adm-header__more-list" ref={moreContentRef}>
                  {hiddenItems.map((node, i) => (
                    <div className="adm-header__more-item" key={i}>
                      {node}
                    </div>
                  ))}
                </div>
              }
            >
              <Button
                className="adm-header__more"
                icon={<EllipsisOutlined />}
                aria-label="更多操作"
                aria-haspopup="dialog"
                aria-expanded={open}
              >
                更多
              </Button>
            </Popover>
          </Tooltip>
        )}
      </div>

      {canMeasure && (
        <div className="adm-header__measure" aria-hidden="true">
          <div className="adm-header__measure-inner" ref={measureInnerRef}>
            {items.map((node, i) => (
              <div
                className="adm-header__measure-item"
                key={i}
                ref={(el) => {
                  measureItemRefs.current[i] = el
                }}
              >
                {node}
              </div>
            ))}
            <div className="adm-header__measure-item" ref={measureMoreRef}>
              <Button icon={<EllipsisOutlined />}>更多</Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
