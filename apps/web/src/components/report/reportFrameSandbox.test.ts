/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  REPORT_FRAME_HEIGHT_MESSAGE_TYPE,
  REPORT_IFRAME_SANDBOX,
  REPORT_SOURCE_LINK_BRIDGE_SCRIPT,
  REPORT_SOURCE_LINK_MESSAGE_TYPE,
  isReportFrameHeightMessage,
  isReportSourceLinkMessage,
} from './reportFrameSandbox.ts'
import { buildReportSrcDoc } from './buildReportSrcDoc.ts'

test('report iframe stays isolated and uses a height bridge', () => {
  const tokens = REPORT_IFRAME_SANDBOX.split(/\s+/)

  assert.ok(tokens.includes('allow-scripts'))
  assert.equal(tokens.includes('allow-same-origin'), false)
  assert.match(REPORT_SOURCE_LINK_BRIDGE_SCRIPT, new RegExp(REPORT_FRAME_HEIGHT_MESSAGE_TYPE))
  assert.match(REPORT_SOURCE_LINK_BRIDGE_SCRIPT, /ResizeObserver/)
  assert.match(REPORT_SOURCE_LINK_BRIDGE_SCRIPT, /MutationObserver/)
})

test('report srcdoc injects source-link and height bridges', () => {
  const srcDoc = buildReportSrcDoc('<html><head></head><body><a href="/api/source/task/page/1">PDF</a></body></html>', '/reports/sample.html')

  assert.match(srcDoc, new RegExp(REPORT_SOURCE_LINK_MESSAGE_TYPE))
  assert.match(srcDoc, new RegExp(REPORT_FRAME_HEIGHT_MESSAGE_TYPE))
  assert.match(srcDoc, /window\.parent\.postMessage/)
  assert.match(srcDoc, /siq-report-light-theme/)
  assert.match(srcDoc, /normalizeInlineMarkdown/)
  assert.match(srcDoc, /siq-md-inline-heading/)
})

test('report message guards accept only valid payloads', () => {
  assert.equal(isReportSourceLinkMessage({ type: REPORT_SOURCE_LINK_MESSAGE_TYPE, href: '/api/source/task/page/1' }), true)
  assert.equal(isReportSourceLinkMessage({ type: REPORT_SOURCE_LINK_MESSAGE_TYPE, href: '' }), false)
  assert.equal(isReportFrameHeightMessage({ type: REPORT_FRAME_HEIGHT_MESSAGE_TYPE, height: 960 }), true)
  assert.equal(isReportFrameHeightMessage({ type: REPORT_FRAME_HEIGHT_MESSAGE_TYPE, height: 0 }), false)
  assert.equal(isReportFrameHeightMessage({ type: REPORT_FRAME_HEIGHT_MESSAGE_TYPE, height: '960' }), false)
})
