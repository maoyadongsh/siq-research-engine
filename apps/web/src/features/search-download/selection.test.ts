/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import type { ReportItem } from './model.ts'

const {
  reportSelectionKeys,
  toggleSearchDownloadReportGroupSelection,
  toggleSearchDownloadSelection,
} = await import('./selection.ts')

function report(documentUrl: string, title = 'Report') {
  return {
    title,
    report_type: 'annual',
    report_end: '2025-12-31',
    published_at: '2026-03-01',
    document_url: documentUrl,
  } satisfies ReportItem
}

test('toggleSearchDownloadSelection adds and removes one key immutably', () => {
  const current = new Set(['a'])
  const added = toggleSearchDownloadSelection(current, 'b')
  const removed = toggleSearchDownloadSelection(added, 'a')

  assert.deepEqual(Array.from(current), ['a'])
  assert.deepEqual(Array.from(added).sort(), ['a', 'b'])
  assert.deepEqual(Array.from(removed), ['b'])
})

test('reportSelectionKeys ignores reports without document urls', () => {
  assert.deepEqual(reportSelectionKeys([report('a'), report(''), report('b')]), ['a', 'b'])
})

test('toggleSearchDownloadReportGroupSelection selects missing group keys and clears fully selected group', () => {
  const reports = [report('a'), report('b')]
  const partiallySelected = toggleSearchDownloadReportGroupSelection(new Set(['a', 'outside']), reports)
  const cleared = toggleSearchDownloadReportGroupSelection(partiallySelected, reports)

  assert.deepEqual(Array.from(partiallySelected).sort(), ['a', 'b', 'outside'])
  assert.deepEqual(Array.from(cleared), ['outside'])
})

test('toggleSearchDownloadReportGroupSelection leaves empty groups untouched', () => {
  const current = new Set(['outside'])
  const next = toggleSearchDownloadReportGroupSelection(current, [])

  assert.deepEqual(Array.from(next), ['outside'])
  assert.notEqual(next, current)
})
