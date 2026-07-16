/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  collectHeadingSectionLines,
  extractAnswerAuditTraceId,
  INLINE_URL_RE,
  isAuditHeading,
  matchBoldHeading,
  parseCitationActions,
} from './rendererUtils.ts'

test('bare citation URL excludes trailing field punctuation', () => {
  const line = 'source_url=https://www.sec.gov/Archives/report.htm, source_anchor=f-152'
  const match = INLINE_URL_RE.exec(line)

  assert.equal(match?.[0], 'https://www.sec.gov/Archives/report.htm')
})

test('matchBoldHeading promotes compact section labels but keeps bold prose as prose', () => {
  assert.equal(matchBoldHeading('**核心结论**'), '核心结论')
  assert.equal(matchBoldHeading('**一、项目风险：**'), '一、项目风险')
  assert.equal(matchBoldHeading('**下一步动作**：'), '下一步动作')
  assert.equal(matchBoldHeading('**Key risks**'), 'Key risks')
  assert.equal(matchBoldHeading('**这是一个完整判断，不应被当成标题。**'), null)
  assert.equal(matchBoldHeading('正文中的 **局部强调**'), null)
  assert.equal(matchBoldHeading(`**${'很长的标题'.repeat(20)}**`), null)
})

test('parseCitationActions keeps source task page and table text while extracting source links', () => {
  const parsed = parseCitationActions(
    '[1] source annual-report.pdf task task_20260630 page 12 table 3 [page source](/api/source/task_20260630/page/12), [table](/api/source/task_20260630/table/3)',
  )

  assert.equal(parsed.text, '[1] source annual-report.pdf task task_20260630 page 12 table 3')
  assert.deepEqual(parsed.actions, [
    { label: 'page source', href: '/api/source/task_20260630/page/12', kind: 'source' },
    { label: 'table', href: '/api/source/task_20260630/table/3', kind: 'table' },
  ])
})

test('parseCitationActions preserves normal markdown links and tolerates long citation lines', () => {
  const longTitle = Array.from({ length: 80 }, (_, index) => `segment-${index}`).join(' ')
  const parsed = parseCitationActions(
    `[2] source ${longTitle} task task_long page 88 table 9 see [filing](https://example.com/report.pdf) [source](/api/documents/source/task_long/page/88)`,
  )

  assert.match(parsed.text, /^\[2\] source segment-0/)
  assert.match(parsed.text, /task task_long page 88 table 9/)
  assert.match(parsed.text, /\[filing\]\(https:\/\/example.com\/report.pdf\)$/)
  assert.deepEqual(parsed.actions, [
    { label: 'source', href: '/api/documents/source/task_long/page/88', kind: 'source' },
  ])
})

test('parseCitationActions removes bare source link helpers and dedupes generated buttons', () => {
  const parsed = parseCitationActions(
    '[1] source_type=wiki_document_links, task_id=task_a, pdf_page=137, table_index=165，打开PDF页(/api/pdf_page/task_a/137)，查看页来源(/api/source/task_a/page/137)，查看表格(/api/source/task_a/table/165)，[打开PDF定位页137](https://public.example/api/pdf_page/task_a/137?format=html)，[查看定位页137来源](https://public.example/api/source/task_a/page/137?format=html)，[查看可读表格165](https://public.example/api/source/task_a/table/165?format=html)',
  )

  assert.equal(parsed.text, '[1] source_type=wiki_document_links, task_id=task_a, pdf_page=137, table_index=165')
  assert.deepEqual(parsed.actions, [
    { label: '打开PDF定位页137', href: 'https://public.example/api/pdf_page/task_a/137?format=html', kind: 'pdf' },
    { label: '查看定位页137来源', href: 'https://public.example/api/source/task_a/page/137?format=html', kind: 'source' },
    { label: '查看可读表格165', href: 'https://public.example/api/source/task_a/table/165?format=html', kind: 'table' },
  ])
})

test('isAuditHeading recognizes compact audit detail headings only', () => {
  assert.equal(isAuditHeading('审计详情'), true)
  assert.equal(isAuditHeading('审计详情:'), true)
  assert.equal(isAuditHeading('## 审计详情'), true)
  assert.equal(isAuditHeading('审计详情：'), true)
  assert.equal(isAuditHeading('# 审计详情'), true)
  assert.equal(isAuditHeading('#### 审计详情：'), true)
  assert.equal(isAuditHeading('### 审计详情:'), true)
  assert.equal(isAuditHeading('##### 审计详情'), false)
  assert.equal(isAuditHeading('审计明细'), false)
  assert.equal(isAuditHeading('审计详情 extra'), false)
  assert.equal(isAuditHeading('## 审计详情 123'), false)
  assert.equal(isAuditHeading('## 审计详情与引用来源'), false)
})

test('collectHeadingSectionLines stops audit details before the next normal heading', () => {
  const lines = [
    '回答正文',
    '## 审计详情',
    '- trace_schema: `siq_answer_audit_trace_v1`',
    '- fallback_reason: `market_view_hit`',
    '',
    '### 下一步',
    '继续分析',
  ]

  const section = collectHeadingSectionLines(lines, 1, isAuditHeading)

  assert.deepEqual(section.lines, [
    '- trace_schema: `siq_answer_audit_trace_v1`',
    '- fallback_reason: `market_view_hit`',
    '',
  ])
  assert.equal(section.nextIndex, 5)
})

test('extractAnswerAuditTraceId reads stable answer audit ids from summary lines', () => {
  assert.equal(
    extractAnswerAuditTraceId([
      '- trace_schema: `siq_answer_audit_trace_v1`',
      '- trace_id: `aat_0123456789abcdef0123456789abcdef`',
    ]),
    'aat_0123456789abcdef0123456789abcdef',
  )
  assert.equal(extractAnswerAuditTraceId(['- trace_id: `bad`']), '')
})
