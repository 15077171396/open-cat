import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Skeleton, Tag, Tooltip, Typography } from 'antd'
import {
  CheckCircleFilled,
  CloseCircleFilled,
  ExclamationCircleFilled,
  MedicineBoxOutlined,
  MinusCircleFilled,
  ReloadOutlined,
  RightOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { apiGet } from '../../api/client'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'
import PageHeader from '../../components/ui/PageHeader'
import StatusTag from '../../components/ui/StatusTag'
import type { ReadinessData, ReadinessItem } from '../../types'

/**
 * 部署体检（系统 → 部署体检）。
 *
 * 为什么要有这个页面：开源版第一次部署时，最劝退的不是「跑不起来」（有报错可查），
 * 而是「跑起来了但不能用」—— 用户点「获取验证码」只看到发送失败、上传图片只看到
 * 「存储失败」、邮件里的链接指向 localhost。这些全是「不配也不报错，只是静默不生效」
 * 的配置，界面上原本没有任何地方告诉他去填。
 *
 * 与「数据看板 → 系统状态」的分工：那边看依赖是否**活着**（MySQL/Redis/备份/心跳），
 * 这边看能力是否**可用**（存储/站点地址/邮件/短信/AI/支付）。
 *
 * 判定全部在后端（`app/services/readiness_service.py`），本页只负责呈现与跳转 ——
 * 前端不重复判断，否则迟早出现「页面说好、实际报错」。
 */
const STATUS_META: Record<
  ReadinessItem['status'],
  { tag: React.ReactNode; icon: React.ReactNode; color: string }
> = {
  // 危险色只留给「配了但不完整」：这类最危险，因为用户以为自己配好了
  error: {
    tag: <StatusTag tone="danger">有问题</StatusTag>,
    icon: <CloseCircleFilled style={{ color: 'var(--app-danger)' }} />,
    color: 'var(--app-danger)',
  },
  unconfigured: {
    tag: <StatusTag tone="warning">未配置</StatusTag>,
    icon: <ExclamationCircleFilled style={{ color: 'var(--app-warning)' }} />,
    color: 'var(--app-warning)',
  },
  ready: {
    tag: <StatusTag tone="success">就绪</StatusTag>,
    icon: <CheckCircleFilled style={{ color: 'var(--app-success)' }} />,
    color: 'var(--app-success)',
  },
  off: {
    tag: <StatusTag tone="neutral">未启用</StatusTag>,
    icon: <MinusCircleFilled style={{ color: 'var(--app-neutral)' }} />,
    color: 'var(--app-neutral)',
  },
}

export default function Readiness() {
  const navigate = useNavigate()
  const [data, setData] = useState<ReadinessData | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setFailed(false)
    try {
      setData(await apiGet<ReadinessData>('/admin/readiness'))
    } catch (err) {
      setFailed(true)
      toast.error(getErrorMessage(err, '体检接口请求失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const summary = data?.summary
  const blockingKeys = summary?.blocking_keys ?? []
  const blockingLabels = (data?.items ?? [])
    .filter((i) => blockingKeys.includes(i.key))
    .map((i) => i.label)

  return (
    <div className="adm-page">
      <PageHeader
        icon={<MedicineBoxOutlined />}
        title="部署体检"
        subtitle="不配也不报错、只是静默不生效的配置，都会在这里现形"
        actions={
          <Tooltip title="重新检查（存储会实际写一个探针文件）">
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
              重新检查
            </Button>
          </Tooltip>
        }
      />

      {loading && !data ? (
        <Card className="adm-tablecard" variant="outlined">
          <Skeleton active paragraph={{ rows: 6 }} />
        </Card>
      ) : failed && !data ? (
        <Card className="adm-tablecard" variant="outlined">
          <Typography.Text type="secondary">
            体检接口没能返回结果。请确认后端容器已启动（<code>docker compose ps</code>），
            然后点右上角「重新检查」。
          </Typography.Text>
        </Card>
      ) : (
        <>
          {/* 汇总：先回答「能不能上线」，再逐项说细节 */}
          <Card className="adm-toolbar" variant="outlined">
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                flexWrap: 'wrap',
                fontSize: 14,
              }}
            >
              {summary?.deployable ? (
                <>
                  <CheckCircleFilled style={{ color: 'var(--app-success)', fontSize: 16 }} />
                  <strong>必填项全部就绪，可以上线。</strong>
                  <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                    可选项没配只影响对应功能，不影响后台使用。
                  </Typography.Text>
                </>
              ) : (
                <>
                  <ExclamationCircleFilled style={{ color: 'var(--app-warning)', fontSize: 16 }} />
                  <strong>
                    还有 {summary?.blocking ?? 0} 项必填未就绪：{blockingLabels.join('、')}
                  </strong>
                  <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                    这些不补齐时，用户会看到「发送失败」「存储失败」这类结果，但原因不会出现在界面上。
                  </Typography.Text>
                </>
              )}
            </div>
          </Card>

          <Card className="adm-tablecard" variant="outlined">
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {(data?.items ?? []).map((item, index) => {
                const meta = STATUS_META[item.status] ?? STATUS_META.off
                return (
                  <div
                    key={item.key}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 12,
                      padding: '14px 0',
                      // 分隔线只画在条目之间，不画在最后一条下方
                      borderBottom:
                        index === (data?.items.length ?? 0) - 1
                          ? 'none'
                          : '1px solid var(--app-border)',
                    }}
                  >
                    <span style={{ lineHeight: '22px', fontSize: 15 }}>{meta.icon}</span>

                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontWeight: 600 }}>{item.label}</span>
                        <Tag
                          style={{ margin: 0, fontSize: 11, lineHeight: '16px' }}
                          color={item.required ? 'blue' : undefined}
                          bordered={false}
                        >
                          {item.required ? '必填' : '可选'}
                        </Tag>
                      </div>
                      <div style={{ fontSize: 13, color: 'var(--app-text-2)', marginTop: 2 }}>
                        {item.detail}
                      </div>
                      {item.action && (
                        <div style={{ fontSize: 12, color: 'var(--app-text-3)', marginTop: 4 }}>
                          {item.action}
                        </div>
                      )}
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 'none' }}>
                      {meta.tag}
                      {item.link && (
                        <Button
                          type="link"
                          size="small"
                          style={{ paddingInline: 0 }}
                          onClick={() => navigate(item.link)}
                        >
                          去配置 <RightOutlined style={{ fontSize: 10 }} />
                        </Button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </Card>

          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            提示：带「未做网络连通测试」的项只校验了凭据齐全，不代表服务端一定连得通
            （例如 COS 的 bucket 名写错、微信支付的商户号未开通对应产品）。
            上线前请各做一次真实动作：上传一张图、发一封测试邮件、用小额订单跑完支付回调。
            当前存储驱动：<strong>{data?.storage_driver ?? '未知'}</strong> · 环境：
            <strong>{data?.app_env ?? '未知'}</strong>
          </Typography.Text>
        </>
      )}
    </div>
  )
}
