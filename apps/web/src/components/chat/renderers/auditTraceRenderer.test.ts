/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { strict as assert } from 'node:assert'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'

const rendererDir = dirname(fileURLToPath(import.meta.url))

function source(name: string) {
  return readFileSync(resolve(rendererDir, name), 'utf-8')
}

test('AuditTraceBlock keeps answer audit details compact and collapsible', () => {
  const block = source('AuditTraceBlock.tsx')

  assert.match(block, /<details\b/)
  assert.match(block, /className="chat-audit-block"/)
  assert.match(block, /<summary className="chat-audit-summary">审计详情<\/summary>/)
  assert.match(block, /className="chat-audit-list"/)
  assert.match(block, /className="chat-audit-item"/)
  assert.match(block, /extractAnswerAuditTraceId\(lines\)/)
  assert.match(block, /apiPrefix = '\/api'/)
  assert.match(block, /apiPrefix\.replace\(\/\\\/\$\/, ''\)/)
  assert.match(block, /apiFetch\(`\$\{traceApiPrefix\}\/chat\/audit-traces\/\$\{encodeURIComponent\(traceId\)\}`\)/)
  assert.match(block, /className="chat-audit-action"/)
  assert.match(block, /FileJson/)
  assert.match(block, /JSON\.stringify\(trace, null, 2\)/)
  assert.match(block, /lines\.map\(\(line\) => line\.trim\(\)\)\.filter\(Boolean\)/)
  assert.match(block, /replace\(\^?\//)
  assert.match(block, /暂无可展示的审计详情。/)
})

test('MarkdownBlocks routes audit detail sections before normal headings', () => {
  const markdownBlocks = source('MarkdownBlocks.tsx')
  const citationBranch = markdownBlocks.indexOf('if (isCitationHeading(trimmed))')
  const auditBranch = markdownBlocks.indexOf('if (isAuditHeading(trimmed))')
  const genericHeadingBranch = markdownBlocks.indexOf('const heading = trimmed.match')

  assert.match(markdownBlocks, /import \{ AuditTraceBlock \} from '\.\/AuditTraceBlock'/)
  assert.match(markdownBlocks, /\bisAuditHeading\b/)
  assert.match(markdownBlocks, /collectHeadingSectionLines\(lines, i, isAuditHeading\)/)
  assert.match(markdownBlocks, /apiPrefix=\{auditTraceApiPrefix\}/)
  assert.ok(citationBranch >= 0)
  assert.ok(auditBranch > citationBranch)
  assert.ok(genericHeadingBranch > auditBranch)
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
