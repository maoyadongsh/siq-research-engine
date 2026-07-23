/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  collectHeadingSectionLines,
  extractAnswerAuditTraceId,
  hasRuntimeCitationLines,
  INLINE_URL_RE,
  isAuditHeading,
  matchBoldHeading,
  isLikelyAlignedTableStart,
  parseAlignedTable,
  parseCitationActions,
  splitFencedCode,
} from './rendererUtils.ts'

test('parses space-aligned financial tables returned by repair runs', () => {
  const lines = [
    '项目                    2024-12-31          2025-12-31          变动',
    '主表商誉净额             11.98 亿元           11.83 亿元           -0.15 亿元',
    '商誉账面原值             13.03 亿元           12.82 亿元           -0.21 亿元',
    '',
  ]

  assert.equal(isLikelyAlignedTableStart(lines, 0), true)
  assert.deepEqual(parseAlignedTable(lines, 0), {
    header: ['项目', '2024-12-31', '2025-12-31', '变动'],
    alignments: ['left', 'right', 'right', 'right'],
    rows: [
      ['主表商誉净额', '11.98 亿元', '11.83 亿元', '-0.15 亿元'],
      ['商誉账面原值', '13.03 亿元', '12.82 亿元', '-0.21 亿元'],
    ],
    lineIndex: 3,
  })
})

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

test('parseCitationActions exposes SEC filing anchors as source actions', () => {
  const parsed = parseCitationActions(
    '[1] source_type=wiki_metrics, source_anchor=f-72, xbrl_tag=us-gaap:Revenues，[打开披露原文](https://www.sec.gov/Archives/edgar/data/1045810/filing.htm#f-72)',
  )

  assert.equal(parsed.text, '[1] source_type=wiki_metrics, source_anchor=f-72, xbrl_tag=us-gaap:Revenues')
  assert.deepEqual(parsed.actions, [
    {
      label: '打开披露原文',
      href: 'https://www.sec.gov/Archives/edgar/data/1045810/filing.htm#f-72',
      kind: 'source',
    },
  ])
})

test('fenced runtime citations are recognized for citation-card rendering', () => {
  const citation = [
    '[S1] source_type=wiki_metrics, evidence_source_type=sec_xbrl_fact,',
    'source_url=https://www.sec.gov/Archives/edgar/data/1045810/filing.htm, source_anchor=f-72',
  ].join('\n')

  assert.equal(hasRuntimeCitationLines(citation), true)
  assert.equal(hasRuntimeCitationLines('const source_type = "example"'), false)
})

test('splitFencedCode unwraps runtime citations into the surrounding citation section', () => {
  const blocks = splitFencedCode([
    '## 引用来源',
    '',
    '```',
    '[S1] source_type=wiki_metrics, evidence_source_type=sec_xbrl_fact, source_url=https://www.sec.gov/Archives/edgar/data/1045810/filing.htm, source_anchor=f-72',
    '```',
  ].join('\n'))

  assert.deepEqual(blocks, [
    {
      type: 'markdown',
      lines: [
        '## 引用来源',
        '',
        '[S1] source_type=wiki_metrics, evidence_source_type=sec_xbrl_fact, source_url=https://www.sec.gov/Archives/edgar/data/1045810/filing.htm, source_anchor=f-72',
      ],
    },
  ])
})

test('parseCitationActions derives SEC source actions from locator fields', () => {
  const parsed = parseCitationActions(
    '[S1] source_type=wiki_metrics, source_url=https://www.sec.gov/Archives/edgar/data/320193/filing.htm, source_anchor=f-78',
  )

  assert.deepEqual(parsed.actions, [
    {
      label: '打开披露原文',
      href: 'https://www.sec.gov/Archives/edgar/data/320193/filing.htm#f-78',
      kind: 'source',
    },
  ])
})

test('parseCitationActions accepts legacy bare SEC disclosure targets', () => {
  const parsed = parseCitationActions(
    '[S1] source_type=wiki_metrics, 打开披露原文=https://www.sec.gov/Archives/edgar/data/320193/filing.htm#f-78',
  )

  assert.deepEqual(parsed.actions, [
    {
      label: '打开披露原文',
      href: 'https://www.sec.gov/Archives/edgar/data/320193/filing.htm#f-78',
      kind: 'source',
    },
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

test('parseCitationActions converts colon-delimited absolute source URLs into actions', () => {
  const parsed = parseCitationActions(
    '[1] source_type=wiki_metrics, metric=资产负债表核心数据, period=2025-annual，打开PDF页：https://arthurmao.synology.me:9391/api/pdf_page/task-a/65?format=html，查看页来源：https://arthurmao.synology.me:9391/api/source/task-a/page/65?format=html，查看表格：https://arthurmao.synology.me:9391/api/source/task-a/table/84?format=html, printed_page=65 / 205',
  )

  assert.equal(
    parsed.text,
    '[1] source_type=wiki_metrics, metric=资产负债表核心数据, period=2025-annual， printed_page=65 / 205',
  )
  assert.deepEqual(parsed.actions, [
    { label: '打开PDF页', href: 'https://arthurmao.synology.me:9391/api/pdf_page/task-a/65?format=html', kind: 'pdf' },
    { label: '查看页来源', href: 'https://arthurmao.synology.me:9391/api/source/task-a/page/65?format=html', kind: 'source' },
    { label: '查看表格', href: 'https://arthurmao.synology.me:9391/api/source/task-a/table/84?format=html', kind: 'table' },
  ])
})

test('isAuditHeading recognizes audit and financial validation headings', () => {
  assert.equal(isAuditHeading('审计详情'), true)
  assert.equal(isAuditHeading('审计详情:'), true)
  assert.equal(isAuditHeading('## 审计详情'), true)
  assert.equal(isAuditHeading('审计详情：'), true)
  assert.equal(isAuditHeading('# 审计详情'), true)
  assert.equal(isAuditHeading('#### 审计详情：'), true)
  assert.equal(isAuditHeading('### 审计详情:'), true)
  assert.equal(isAuditHeading('## 证据链审计详情'), true)
  assert.equal(isAuditHeading('## 计算器校验'), true)
  assert.equal(isAuditHeading('## 计算器校验(全部通过 financial_calculator.py)'), true)
  assert.equal(isAuditHeading('## 勾稽校验（全部通过）'), true)
  assert.equal(isAuditHeading('## 勾稽校验'), true)
  assert.equal(isAuditHeading('## 校验失败详情'), true)
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
