/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  REPORT_IFRAME_SANDBOX,
  REPORT_SOURCE_LINK_BRIDGE_SCRIPT,
  REPORT_SOURCE_LINK_MESSAGE_TYPE,
  isReportSourceLinkMessage,
} from './reportFrameSandbox.ts'
import { buildReportSrcDoc } from './buildReportSrcDoc.ts'

test('report iframe sandbox allows chart scripts while keeping srcdoc cross-origin isolated', () => {
  const tokens = REPORT_IFRAME_SANDBOX.split(/\s+/)

  assert.ok(tokens.includes('allow-scripts'))
  assert.ok(tokens.includes('allow-popups'))
  assert.ok(tokens.includes('allow-downloads'))
  assert.equal(tokens.includes('allow-same-origin'), false)
})

test('report srcdoc injects the source-link bridge script for sandboxed reports', () => {
  const srcDoc = buildReportSrcDoc('<html><head></head><body><a href="/api/source/task/page/1">PDF</a></body></html>', '/reports/sample.html')

  assert.match(srcDoc, new RegExp(REPORT_SOURCE_LINK_MESSAGE_TYPE.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  assert.match(srcDoc, /window\.parent\.postMessage/)
  assert.match(srcDoc, /siq-report-light-theme/)
  assert.match(REPORT_SOURCE_LINK_BRIDGE_SCRIPT, /SOURCE_LINK_RE/)
})

test('report source-link message guard accepts only typed href payloads', () => {
  assert.equal(isReportSourceLinkMessage({ type: REPORT_SOURCE_LINK_MESSAGE_TYPE, href: '/api/source/task/page/1' }), true)
  assert.equal(isReportSourceLinkMessage({ type: REPORT_SOURCE_LINK_MESSAGE_TYPE, href: '' }), false)
  assert.equal(isReportSourceLinkMessage({ type: 'other', href: '/api/source/task/page/1' }), false)
  assert.equal(isReportSourceLinkMessage(null), false)
})
