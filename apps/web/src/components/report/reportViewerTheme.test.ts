/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { REPORT_VIEWER_THEME } from './reportViewerTheme.ts'

test('report viewer theme preserves legal verdict and status color surfaces', () => {
  const expectedSelectors = [
    '.card,.panel,.verdict-banner,.finding,.finding-card,.check-card,.result-card,.audit-card,.status-card',
    '.header:not(.report-header)',
    '.verdict-badge.approve,.card-icon.green',
    '.verdict-badge.request_changes,.card-icon.yellow',
    '.verdict-badge.block,.card-icon.red',
    '.card-status.pass,.status.pass',
    '.card-status.warn,.status.warn,.card-status.warning,.status.warning',
    '.card-status.fail,.status.fail,.card-status.error,.status.error',
  ]

  for (const selector of expectedSelectors) {
    assert.match(REPORT_VIEWER_THEME, new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
})

test('report viewer theme keeps report header out of generic header override', () => {
  assert.match(REPORT_VIEWER_THEME, /\.header:not\(\.report-header\)/)
  assert.doesNotMatch(REPORT_VIEWER_THEME, /\.header\s*,\s*\.report-header/)
  assert.match(REPORT_VIEWER_THEME, /\.report-header\s*\{/)
})
