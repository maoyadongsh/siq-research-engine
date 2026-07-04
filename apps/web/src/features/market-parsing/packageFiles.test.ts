/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const { groupMarketPackagePaths } = await import('./packageFiles.ts')

test('groupMarketPackagePaths groups HK V2 parser and QA files from dynamic paths', () => {
  const groups = groupMarketPackagePaths({
    manifest: 'manifest.json',
    quality_report: 'qa/quality_report.json',
    source_map: 'qa/source_map.json',
    financial_data: 'metrics/financial_data.json',
    document_full: '/parser/document_full.json',
    content_list_enhanced: 'parser/content_list_enhanced.json',
    report_complete: 'sections/report_complete.md',
    footnotes: 'qa/footnotes.json',
    table_index: 'tables/table_index.json',
  })

  assert.deepEqual(
    groups.map((group) => [group.id, group.entries.map((entry) => `${entry.name}:${entry.file}`)]),
    [
      ['manifest', ['manifest:manifest.json']],
      ['quality', ['quality_report:qa/quality_report.json']],
      ['source', ['source_map:qa/source_map.json']],
      ['financial', ['financial_data:metrics/financial_data.json']],
      ['parser', ['document_full:parser/document_full.json', 'content_list_enhanced:parser/content_list_enhanced.json']],
      ['qa', ['footnotes:qa/footnotes.json']],
      ['sections', ['report_complete:sections/report_complete.md']],
      ['tables', ['table_index:tables/table_index.json']],
    ],
  )
})
