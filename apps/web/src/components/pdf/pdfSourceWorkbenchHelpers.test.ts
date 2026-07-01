/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import type { PageBlock, PageContent, SourceMeta, SourceTable } from '../../lib/pdfTypes.ts'
import type { EnhancedTable } from './pdfSourceWorkbenchTypes.ts'
import {
  buildPagePreviewOverlays,
  mergePhysicalTables,
  pageNumber,
  pageTablesForPage,
  relationsFromArtifactForPage,
  renderFallbackPageHtml,
  validBbox,
} from './pdfSourceWorkbenchHelpers.ts'

test('pageNumber and validBbox normalize numeric boundaries', () => {
  assert.equal(pageNumber(3.8), 3)
  assert.equal(pageNumber('6'), 6)
  assert.equal(pageNumber(0, 9), 9)
  assert.equal(pageNumber(-1, 9), 9)
  assert.equal(pageNumber('not-a-number', 9), 9)

  assert.deepEqual(validBbox('1, 2, 30, 40'), [1, 2, 30, 40])
  assert.deepEqual(validBbox([1, 2, 3, 4]), [1, 2, 3, 4])
  assert.deepEqual(validBbox([1, 2, 1, 4]), [])
  assert.deepEqual(validBbox([1, 2, 3]), [])
})

test('pageTablesForPage filters invalid boxes and sorts tables by position', () => {
  const tables: EnhancedTable[] = [
    { table_index: 3, pdf_page_number: 1, bbox: [20, 20, 50, 60] },
    { table_index: 2, pdf_page_number: 1, bbox: [5, 10, 50, 60] },
    { table_index: 4, pdf_page_number: 2, bbox: [1, 1, 2, 2] },
    { table_index: 5, pdf_page_number: 1, bbox: [5, 10, 5, 60] },
    { table_index: 1, pdf_page_number: 1, bbox: [1, 10, 50, 60] },
  ]

  assert.deepEqual(
    pageTablesForPage(tables, 1).map((table) => table.table_index),
    [1, 2, 3],
  )
})

test('mergePhysicalTables merges artifact, page-content and source table records', () => {
  const artifactTables: EnhancedTable[] = [
    {
      table_id: 'artifact-1',
      table_index: 7,
      pdf_page_number: 1,
      bbox: [10, 10, 100, 80],
      heading: 'artifact heading',
      source: 'artifact',
      structure: { expanded_columns: 3 },
      rows: 4,
    },
  ]
  const pageContentCache: Record<number, PageContent> = {
    1: {
      page_number: 1,
      blocks: [
        {
          block_id: 'same-bbox',
          type: 'table',
          table_index: 700,
          bbox: [10, 10, 100, 80],
          table_html: '<table><tr><td>A</td><td>B</td><td>C</td></tr><tr><td>1</td><td>2</td><td>3</td></tr></table>',
          heading: 'page heading',
        },
        {
          block_id: 'page-only',
          type: 'table',
          table_index: 8,
          bbox: [120, 20, 180, 90],
          table_html: '<table><tr><td>X</td></tr></table>',
        },
        {
          block_id: 'invalid',
          type: 'table',
          bbox: [1, 1, 1, 3],
          table_html: '<table><tr><td>ignored</td></tr></table>',
        },
      ],
    },
  }
  const sourceTable: SourceTable = {
    table_index: 9,
    pdf_page_number: 2,
    bbox: [5, 5, 50, 50],
    table_html: '<table><tr><td>S</td><td>T</td></tr></table>',
    heading: 'source heading',
  }
  const sourceMeta = { pdfPageImage: { page_number: 2, printed_page_number: 'ii' } } as SourceMeta

  const merged = mergePhysicalTables(artifactTables, pageContentCache, sourceTable, sourceMeta)

  assert.deepEqual(
    merged.map((table) => table.table_index),
    [7, 8, 9],
  )
  assert.equal(merged[0]?.heading, 'artifact heading')
  assert.equal(merged[0]?.source, 'artifact')
  assert.equal(merged[1]?.source, 'page_block')
  assert.equal(merged[2]?.source, 'source_table')
  assert.equal(merged[2]?.printed_page_number, 'ii')
})

test('relationsFromArtifactForPage converts valid adjacent artifact relations only', () => {
  const tables: EnhancedTable[] = [
    { table_index: 1, pdf_page_number: 1, bbox: [10, 700, 500, 980], heading: 'part one' },
    { table_index: 2, pdf_page_number: 2, bbox: [10, 20, 500, 300], heading: 'part two' },
  ]
  const relations = relationsFromArtifactForPage(
    {
      relations: [
        {
          relation_type: 'candidate_continuation',
          merge_confidence: 0.72,
          merge_reasons: ['artifact_match'],
          from_page_number: 1,
          to_page_number: 2,
          from_table_index: 1,
          to_table_index: 2,
          from_bbox: [10, 700, 500, 980],
          to_bbox: [10, 20, 500, 300],
        },
        {
          relation_type: 'continuation',
          from_page_number: 1,
          to_page_number: 3,
          from_bbox: [10, 700, 500, 980],
          to_bbox: [10, 20, 500, 300],
        },
        {
          relation_type: 'continuation',
          from_page_number: 2,
          to_page_number: 3,
          from_bbox: [1, 1, 1, 4],
          to_bbox: [10, 20, 500, 300],
        },
      ],
    },
    2,
    tables,
  )

  assert.equal(relations.length, 1)
  assert.equal(relations[0]?.relationType, 'candidate_continuation')
  assert.equal(relations[0]?.confidence, 0.72)
  assert.deepEqual(relations[0]?.reasons, ['artifact_match'])
  assert.deepEqual(relations[0]?.pageNumbers, [1, 2])
  assert.equal(relationsFromArtifactForPage({ relations: [] }, 2, tables).length, 0)
})

test('buildPagePreviewOverlays ignores chrome blocks and keeps focus state stable', () => {
  const blocks: PageBlock[] = [
    { block_id: 'page-header', type: 'header', bbox: [10, 10, 300, 40], text: '1' },
    { block_id: 'body', type: 'text', bbox: [20, 120, 320, 160], text: 'Management discussion' },
    { block_id: 'table-block', type: 'table', bbox: [20, 180, 320, 260], table_html: '<table><tr><td>x</td></tr></table>' },
  ]
  const tables: EnhancedTable[] = [{ table_index: 10, pdf_page_number: 5, bbox: [20, 180, 320, 260], heading: 'main table' }]

  const overlays = buildPagePreviewOverlays({
    pageNumberValue: 5,
    currentPage: 5,
    focusTableIndex: 10,
    tables,
    blocks,
    currentTrace: { pageNumber: 5, bbox: [1, 2, 3, 4], source: 'cell_bbox', confidence: 'high' },
    focusedBlockKey: '5:body',
  })

  assert.deepEqual(
    overlays.map((overlay) => overlay.source),
    ['block', 'table'],
  )
  assert.equal(overlays.find((overlay) => overlay.blockId === 'body')?.tone, 'focused')
  assert.equal(overlays.find((overlay) => overlay.tableIndex === 10)?.tone, 'focused')
})

test('renderFallbackPageHtml wraps bare html once and preserves existing wrapper', () => {
  assert.equal(renderFallbackPageHtml('', 2, 1), '')

  const wrapped = renderFallbackPageHtml('<p>plain</p>', 2, 1)
  assert.match(wrapped, /pdf-page-reading-view/)
  assert.match(wrapped, /PDF 第 2/)
  assert.match(wrapped, /<p>plain<\/p>/)

  const existing = '<div class="pdf-page-reading-view">ready</div>'
  assert.equal(renderFallbackPageHtml(existing, 2, 1), existing)
})
