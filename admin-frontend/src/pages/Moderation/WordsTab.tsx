import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Input, Modal, Space, Tag, Typography } from 'antd'
import { ImportOutlined, ReloadOutlined, SaveOutlined, ThunderboltOutlined } from '@ant-design/icons'

import { apiGet, apiPost, apiPut } from '../../api/client'
import { PermGuard } from '../../components/PermGuard'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'

/**
 * 敏感词库 Tab：内置兜底词（只读）+ 自定义词（可运营）。
 *
 * 自定义词存后端运行期配置，保存后**立即生效**（本进程即时重建匹配器，
 * 其他 worker 60s 内跟上），无需重启。词库直接决定 UGC 拦截边界 ——
 * 删一个词等于放行一类内容，所以保存动作有独立权限码且写审计。
 */

interface WordsData {
  builtin_words: string[]
  extra_words: string[]
  max_words: number
  word_max_len: number
}

/** 词表解析（与后端 parse_words 同口径：逗号/换行分隔、去重、截断） */
function parseWords(raw: string): string[] {
  const seen = new Set<string>()
  const words: string[] = []
  for (const part of raw.replace(/\r/g, '\n').replace(/\n/g, ',').split(',')) {
    const word = part.trim()
    if (word && !seen.has(word.toLowerCase())) {
      seen.add(word.toLowerCase())
      words.push(word)
    }
  }
  return words
}

export default function WordsTab({ active }: { active: boolean }) {
  const [meta, setMeta] = useState<WordsData | null>(null)
  const [words, setWords] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [newWord, setNewWord] = useState('')
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [testText, setTestText] = useState('')
  const [testHits, setTestHits] = useState<string[] | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiGet<WordsData>('/admin/moderation/words')
      setMeta(data)
      setWords(data.extra_words)
    } catch (err) {
      toast.error(getErrorMessage(err, '词库加载失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  // 懒加载：切到本 Tab 才拉数据（词表可能几百条，首屏没必要）
  useEffect(() => {
    if (active && !meta) void load()
  }, [active, meta, load])

  const dirty = useMemo(() => {
    if (!meta) return false
    return JSON.stringify(words) !== JSON.stringify(meta.extra_words)
  }, [words, meta])

  const addWord = () => {
    const word = newWord.trim().slice(0, meta?.word_max_len ?? 40)
    if (!word) return
    if (words.some((w) => w.toLowerCase() === word.toLowerCase())) {
      toast.warning('该词已在自定义词库中')
      return
    }
    setWords((prev) => [...prev, word])
    setNewWord('')
  }

  const removeWord = (word: string) => {
    setWords((prev) => prev.filter((w) => w !== word))
  }

  const openImport = () => setImportOpen(true)

  const confirmImport = () => {
    const parsed = parseWords(importText)
    if (!parsed.length) {
      toast.warning('没有解析到有效词条（每行一个，或用逗号分隔）')
      return
    }
    setWords((prev) => {
      const seen = new Set(prev.map((w) => w.toLowerCase()))
      const merged = [...prev]
      for (const word of parsed) {
        if (!seen.has(word.toLowerCase())) {
          seen.add(word.toLowerCase())
          merged.push(word)
        }
      }
      return merged
    })
    setImportOpen(false)
    setImportText('')
  }

  const save = async () => {
    if (meta && words.length > meta.max_words) {
      toast.error(`自定义词数量超过上限（${meta.max_words}）`)
      return
    }
    setSaving(true)
    try {
      await apiPut('/admin/moderation/words', { extra_words: words })
      toast.success('词库已保存，立即生效（无需重启）')
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, '词库保存失败'))
    } finally {
      setSaving(false)
    }
  }

  const runTest = async () => {
    if (!testText.trim()) return
    try {
      const data = await apiPost<{ hits: string[] }>('/admin/moderation/words/test', { text: testText })
      setTestHits(data.hits)
    } catch (err) {
      toast.error(getErrorMessage(err, '测试失败'))
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card
        variant="outlined"
        title="自定义词库"
        extra={
          <Space>
            {dirty && (
              <Typography.Text type="warning" style={{ fontSize: 12 }}>
                有未保存修改（{words.length} 词）
              </Typography.Text>
            )}
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
              重载
            </Button>
            <Button icon={<ImportOutlined />} onClick={openImport}>
              批量导入
            </Button>
            <PermGuard perm="moderation:words">
              <Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={!dirty} onClick={() => void save()}>
                保存并生效
              </Button>
            </PermGuard>
          </Space>
        }
      >
        <Space wrap style={{ marginBottom: 12 }}>
          <Input
            style={{ width: 240 }}
            placeholder="输入词条后回车添加"
            value={newWord}
            maxLength={meta?.word_max_len ?? 40}
            onChange={(e) => setNewWord(e.target.value)}
            onPressEnter={addWord}
            disabled={!meta}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {words.length} / {meta?.max_words ?? 500} 词 · 保存后立即生效，无需重启
          </Typography.Text>
        </Space>
        <Space wrap size={6}>
          {words.length === 0 && !loading && (
            <Typography.Text type="secondary">暂无自定义词（内置词库始终生效，见下方）</Typography.Text>
          )}
          {words.map((word) => (
            <Tag key={word} closable={Boolean(meta)} onClose={() => removeWord(word)} color="orange">
              {word}
            </Tag>
          ))}
        </Space>
        <PermGuard perm="moderation:words">
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 12, marginBottom: 0 }}>
            修改词库直接改变 UGC 拦截边界（删除词条 = 放行一类内容），保存动作会写入操作审计。
          </Typography.Paragraph>
        </PermGuard>
      </Card>

      <Card variant="outlined" title="内置词库（兜底基线，只读）">
        <Space wrap size={6}>
          {(meta?.builtin_words ?? []).map((word) => (
            <Tag key={word} style={{ color: 'inherit' }}>
              {word}
            </Tag>
          ))}
          {!meta && <Typography.Text type="secondary">切到本 Tab 后加载…</Typography.Text>}
        </Space>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 12, marginBottom: 0 }}>
          内置词只覆盖无歧义的通用违规（广告引流 / 黑产交易 / 人身攻击），站点专属规则请用自定义词补充。
        </Typography.Paragraph>
      </Card>

      <Card variant="outlined" title="命中测试">
        <Input.TextArea
          rows={3}
          placeholder="粘贴一段文本，用当前生效词库（内置 + 自定义）检测命中"
          value={testText}
          maxLength={2000}
          showCount
          onChange={(e) => setTestText(e.target.value)}
        />
        <Space style={{ marginTop: 12 }}>
          <Button icon={<ThunderboltOutlined />} onClick={() => void runTest()}>
            检测
          </Button>
          {testHits !== null && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {testHits.length === 0 ? '未命中任何词条' : `命中 ${testHits.length} 个词条`}
            </Typography.Text>
          )}
        </Space>
        {testHits !== null && testHits.length > 0 && (
          <Space wrap size={6} style={{ marginTop: 8 }}>
            {testHits.map((hit) => (
              <Tag key={hit} color="red">
                {hit}
              </Tag>
            ))}
          </Space>
        )}
      </Card>

      <Alert
        type="info"
        showIcon
        title="生效机制"
        description="自定义词保存在系统配置中并即时热重载：当前节点立即生效，其余后端进程最多 60 秒内跟上。"
      />

      <Modal
        title="批量导入词条"
        open={importOpen}
        onCancel={() => setImportOpen(false)}
        onOk={confirmImport}
        okText="导入"
        cancelText="取消"
        width={560}
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          每行一个词条，或用逗号分隔；重复词自动去重。当前词表 {words.length} 词，上限 {meta?.max_words ?? 500}。
        </Typography.Paragraph>
        <Input.TextArea
          rows={10}
          placeholder={'加微信送福利\n免费领取\n赌博,代开发票'}
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
        />
      </Modal>
    </div>
  )
}
