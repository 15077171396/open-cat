import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Col, Row, Segmented, Space, Table, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { apiGet } from '../../api/client'
import { getErrorMessage } from '../../utils/error'
import { formatDateTime } from '../../utils/datetime'
import type { SearchKeywordReport, SearchKeywordRow, SearchZeroRow } from '../../types'

import { toast } from '../../utils/toast'
import { TableEmpty } from '../../components/TableEmpty'

/**
 * 站内搜索词分析：**只读**页面。
 *
 * 这张页面的价值全在右侧那张表 ——「零结果词」就是「用户搜了但站内没有」的清单，
 * 直接可以变成选题。左侧热搜榜用来看「哪些主题真的有人关心」。
 *
 * 三条约定（改之前先读）：
 * - `available === false` 表示**还没采集到数据**（或迁移未执行），
 *   必须与「采集了但这几天没人搜」区分开 —— 前者要去跑迁移，后者是正常现象；
 * - 关键词按**用户原始输入**展示，不做归一化：运营照着用户的说法写标题，
 *   转化率比「规范化」后的词高；
 * - 本页不提供「删除某个词」之类写操作：日志是事实记录，删掉只会让统计失真。
 */

const RANGE_OPTIONS = [
  { value: '7', label: '近 7 天' },
  { value: '30', label: '近 30 天' },
  { value: '90', label: '近 90 天' },
]

const MODULE_LABELS: Record<string, string> = {
  tutorial: '教程',
  resource: '资源',
  node: '知识百科',
  plugin: '扩展',
  tool: '在线工具',
}

export default function SearchKeywords() {
  const [days, setDays] = useState('30')
  const [data, setData] = useState<SearchKeywordReport | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await apiGet<SearchKeywordReport>(`/admin/search-keywords?days=${days}&limit=20`))
    } catch (err) {
      toast.error(getErrorMessage(err, '搜索词数据加载失败'))
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [days])

  useEffect(() => {
    void load()
  }, [load])

  const topColumns: TableProps<SearchKeywordRow>['columns'] = [
    {
      title: '关键词',
      dataIndex: 'keyword',
      key: 'keyword',
      render: (value: string) => <span style={{ fontWeight: 500 }}>{value}</span>,
    },
    {
      title: '搜索次数',
      dataIndex: 'count',
      key: 'count',
      width: 96,
      align: 'right',
      render: (value: number) => value ?? 0,
    },
    {
      title: '零结果次数',
      dataIndex: 'zero_count',
      key: 'zero_count',
      width: 110,
      align: 'right',
      render: (value: number) =>
        value ? <Tag color="warning">{value}</Tag> : <Typography.Text type="secondary">0</Typography.Text>,
    },
    {
      title: '平均命中',
      dataIndex: 'avg_results',
      key: 'avg_results',
      width: 96,
      align: 'right',
      render: (value: number | null) => (value === null || value === undefined ? '—' : value),
    },
  ]

  const zeroColumns: TableProps<SearchZeroRow>['columns'] = [
    {
      title: '关键词',
      dataIndex: 'keyword',
      key: 'keyword',
      render: (value: string) => <span style={{ fontWeight: 500 }}>{value}</span>,
    },
    {
      title: '搜索次数',
      dataIndex: 'count',
      key: 'count',
      width: 96,
      align: 'right',
      render: (value: number) => value ?? 0,
    },
    {
      title: '最近一次',
      dataIndex: 'last_at',
      key: 'last_at',
      width: 160,
      render: (value: string | null) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {value ? formatDateTime(value) : '—'}
        </Typography.Text>
      ),
    },
  ]

  const report = data ?? {}
  const total = report.total ?? 0
  const zeroTotal = report.zero_result_total ?? 0
  const zeroRate = report.zero_result_rate
  const available = report.available !== false

  return (
    <div className="adm-page">
      {/* 页头 */}
      <div className="adm-header">
        <div className="adm-header__main">
          <span className="adm-header__icon">
            <SearchOutlined />
          </span>
          <div style={{ minWidth: 0 }}>
            <div className="adm-header__title">搜索词分析</div>
            <div className="adm-header__sub">
              <span>用户搜了什么、搜不到什么 —— 零结果词就是选题清单</span>
              <span className="adm-header__dot">·</span>
              <span>
                近 {report.days ?? days} 天共 <strong>{total}</strong> 次搜索
                {zeroTotal > 0 ? `，其中 ${zeroTotal} 次零结果` : ''}
              </span>
            </div>
          </div>
        </div>
        <div className="adm-header__actions">
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
            刷新
          </Button>
        </div>
      </div>

      {/* 工具条 */}
      <Card className="adm-toolbar" variant="outlined">
        <div className="adm-toolbar__row">
          <Space wrap size={12}>
            <Segmented
              value={days}
              onChange={(value) => setDays(String(value))}
              options={RANGE_OPTIONS}
            />
            {zeroRate !== null && zeroRate !== undefined ? (
              <Tooltip title="零结果占比越高，说明站内越缺用户要找的内容（建议 >30% 就开始补内容）">
                <Tag color={zeroRate >= 0.3 ? 'warning' : 'default'}>
                  零结果占比 {(zeroRate * 100).toFixed(1)}%
                </Tag>
              </Tooltip>
            ) : null}
          </Space>
          <Space size={8} wrap>
            {(report.modules ?? []).map((item) => (
              <Tag key={item.module}>
                {MODULE_LABELS[item.module ?? ''] ?? item.module ?? '未知'} {item.count ?? 0}
              </Tag>
            ))}
          </Space>
        </div>
      </Card>

      {!available ? (
        <Card className="adm-tablecard" variant="outlined">
          <Typography.Text type="secondary">
            尚未采集到搜索数据。若这是刚部署的新版本，请先执行数据库迁移
            （`alembic upgrade head`）并确认后端已重启 —— 之后用户每次站内搜索都会记录一条。
          </Typography.Text>
        </Card>
      ) : (
        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col xs={24} xl={12}>
            <Card
              className="adm-tablecard"
              variant="outlined"
              title={<span style={{ fontSize: 15, fontWeight: 600 }}>热搜词 Top 20</span>}
              extra={
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  去重 {report.unique_keywords ?? 0} 个词
                </Typography.Text>
              }
            >
              <Table<SearchKeywordRow>
                className="adm-table"
                dataSource={report.top ?? []}
                columns={topColumns}
                rowKey={(row) => row.keyword ?? ''}
                loading={loading}
                size="small"
                pagination={false}
                locale={{
                  emptyText: (
                    <TableEmpty
                      hasFilter={false}
                      filteredText="没有符合条件的数据"
                      emptyText={`近 ${report.days ?? days} 天没有站内搜索记录`}
                    />
                  ),
                }}
              />
            </Card>
          </Col>
          <Col xs={24} xl={12}>
            <Card
              className="adm-tablecard"
              variant="outlined"
              title={<span style={{ fontSize: 15, fontWeight: 600 }}>零结果词（选题清单）</span>}
              extra={
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  搜了但站内没有
                </Typography.Text>
              }
            >
              <Table<SearchZeroRow>
                className="adm-table"
                dataSource={report.zero_result ?? []}
                columns={zeroColumns}
                rowKey={(row) => row.keyword ?? ''}
                loading={loading}
                size="small"
                pagination={false}
                locale={{
                  emptyText: (
                    <TableEmpty
                      hasFilter={false}
                      filteredText="没有符合条件的数据"
                      emptyText="这段时间所有搜索都有命中 —— 内容覆盖不错"
                    />
                  ),
                }}
              />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                提示：按用户的**原始说法**补内容（不要先归一化再判断），这样标题与搜索词更容易对上。
              </Typography.Text>
            </Card>
          </Col>
        </Row>
      )}
    </div>
  )
}
