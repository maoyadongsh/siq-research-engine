/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { strict as assert } from 'node:assert'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { validationRunSummary, validationRunsForTitle } from './auditTraceUtils.ts'

const rendererDir = dirname(fileURLToPath(import.meta.url))

function source(name: string) {
  return readFileSync(resolve(rendererDir, name), 'utf-8')
}

test('AuditTraceBlock keeps answer audit details compact and collapsible', () => {
  const block = source('AuditTraceBlock.tsx')

  assert.match(block, /<details\b/)
  assert.match(block, /className=\{`chat-audit-block\$\{toneClass\}`\}/)
  assert.match(block, /title = '证据链审计详情'/)
  assert.match(block, /<summary className="chat-audit-summary">/)
  assert.match(block, /<span>\{title\}<\/span>/)
  assert.match(block, /className="chat-audit-list"/)
  assert.match(block, /className="chat-audit-item"/)
  assert.match(block, /extractAnswerAuditTraceId\(lines\)/)
  assert.match(block, /extractAnswerAuditTraceId\(lines\) \|\| auditTraceId/)
  assert.match(block, /apiPrefix = '\/api'/)
  assert.match(block, /apiPrefix\.replace\(\/\\\/\$\/, ''\)/)
  assert.match(block, /apiFetch\(`\$\{traceApiPrefix\}\/chat\/audit-traces\/\$\{encodeURIComponent\(traceId\)\}`\)/)
  assert.match(block, /className="chat-audit-action"/)
  assert.match(block, /FileJson/)
  assert.match(block, /JSON\.stringify\(trace, null, 2\)/)
  assert.match(block, /lines\.map\(\(line\) => line\.trim\(\)\)\.filter\(Boolean\)/)
  assert.match(block, /replace\(\^?\//)
  assert.match(block, /暂无可展示的审计详情。/)
  assert.match(block, /onToggle=\{handleToggle\}/)
  assert.match(block, /完整校验记录/)
  assert.match(block, /validationRunsForTitle\(trace, title\)/)
  assert.match(block, /run\.status === '已验证'/)
})

test('AuditTraceBlock exposes validation status tones', () => {
  const block = source('AuditTraceBlock.tsx')
  assert.match(block, /全部通过/)
  assert.match(block, /chat-audit-block-success/)
  assert.match(block, /chat-audit-block-warning/)
  assert.match(block, /CheckCircle2/)
  assert.match(block, /TriangleAlert/)
})

test('validation records keep structured runs and only unmatched warnings', () => {
  const trace = {
    calculator_runs: [
      { source: 'backend_evidence_recompute', tool: 'financial_calculator.py', operation: 'yoy', metric: 'revenue', validated: true },
      { source: 'reply_marker', section: '计算器校验（存在待核对项）', line: '- ✅ revenue：12.0' },
      { source: 'reply_marker', section: '计算器校验（存在待核对项）', line: '- ⚠️ 正文值与确定性重算结果不一致' },
      { source: 'reply_marker', section: '计算器校验（存在待核对项）', line: '- 状态：1 项运行记录已检测' },
      { source: 'backend_evidence_recompute', tool: 'financial_reconciliation_validator.py', operation: 'reconcile', validated: true },
    ],
  }

  const calculator = validationRunsForTitle(trace, '计算器校验（存在待核对项）')
  const reconciliation = validationRunsForTitle(trace, '勾稽校验（存在待核对项）')

  assert.equal(calculator.length, 2)
  assert.equal(validationRunSummary(calculator[0], 0).status, '已验证')
  assert.equal(validationRunSummary(calculator[1], 1).status, '待核对')
  assert.equal(reconciliation.length, 1)
})

test('MarkdownBlocks routes audit detail sections before normal headings', () => {
  const markdownBlocks = source('MarkdownBlocks.tsx')
  const citationBranch = markdownBlocks.indexOf('if (isCitationHeading(trimmed))')
  const auditBranch = markdownBlocks.indexOf('if (isAuditHeading(trimmed))')
  const genericHeadingBranch = markdownBlocks.indexOf('const heading = trimmed.match')

  assert.match(markdownBlocks, /import \{ AuditTraceBlock \} from '\.\/AuditTraceBlock'/)
  assert.match(markdownBlocks, /\bisAuditHeading\b/)
  assert.match(markdownBlocks, /collectHeadingSectionLines\(lines, i, isAuditHeading\)/)
  assert.match(markdownBlocks, /title=\{auditHeadingTitle\(trimmed\)\}/)
  assert.match(markdownBlocks, /apiPrefix=\{auditTraceApiPrefix\}/)
  assert.match(markdownBlocks, /auditTraceId=\{auditTraceId\}/)
  assert.ok(citationBranch >= 0)
  assert.ok(auditBranch > citationBranch)
  assert.ok(genericHeadingBranch > auditBranch)
})

test('MarkdownBlocks uses the shared heading renderer for compact bold-only labels', () => {
  const markdownBlocks = source('MarkdownBlocks.tsx')

  assert.match(markdownBlocks, /matchBoldHeading/)
  assert.match(markdownBlocks, /<h3 key=\{`bold-heading-/)
  assert.match(markdownBlocks, /className=\{`chat-heading chat-heading-3/)
  assert.match(markdownBlocks, /!matchBoldHeading\(lines\[i\]\.trim\(\)\)/)
})

test('ChatMessageList exposes structured audit trace ids without audit detail body text', () => {
  const chatList = readFileSync(resolve(rendererDir, '../ChatMessageList.tsx'), 'utf-8')
  const structuredTraceBranch = chatList.indexOf('const structuredAuditTraceId')
  const rendererCall = chatList.indexOf('<MessageRenderer', structuredTraceBranch)
  const structuredBlock = chatList.indexOf('<AuditTraceBlock', rendererCall)

  assert.ok(structuredTraceBranch >= 0)
  assert.ok(rendererCall > structuredTraceBranch)
  assert.ok(structuredBlock > rendererCall)
  assert.match(chatList, /msg\.auditTraceId/)
  assert.match(chatList, /auditTraceId=\{!isUser \? msg\.auditTraceId : undefined\}/)
  assert.match(chatList, /!msg\.content\.includes\(msg\.auditTraceId\)/)
  assert.ok(chatList.includes('lines={[`- trace_id: \\`${structuredAuditTraceId}\\``]}'))
})

test('ChatMessageList renders structured auditTraceId even when answer text omits it', () => {
  const list = readFileSync(resolve(rendererDir, '..', 'ChatMessageList.tsx'), 'utf-8')

  assert.match(list, /msg\.auditTraceId && !msg\.content\.includes\(msg\.auditTraceId\)/)
  assert.match(list, /structuredAuditTraceId \? \(/)
  assert.match(list, /lines=\{\[/)
  assert.match(list, /trace_id/)
  assert.match(list, /\$\{structuredAuditTraceId\}/)
  assert.match(list, /apiPrefix=\{auditTraceApiPrefix\}/)
})
