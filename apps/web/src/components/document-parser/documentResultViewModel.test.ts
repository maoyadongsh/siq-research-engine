/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import type {
  DocumentBlocksPayload,
  DocumentFiguresPayload,
  DocumentLayoutBlocksPayload,
  DocumentResult,
  DocumentSourceMapPayload,
  DocumentTableRelation,
  DocumentTablesPayload,
} from '@/lib/documentTypes.ts'

const {
  buildDocumentResultBaseViewModel,
  buildDocumentResultViewModel,
} = await import('./documentResultViewModel.ts')

const blocks = {
  blocks: [
    { block_id: 'b1', type: 'text', page_number: 1, bbox: [10, 10, 100, 100], markdown: '# hello' },
    { block_id: 'b2', type: 'text', page_number: 2, bbox: [10, 10, 100, 100], markdown: 'page two' },
  ],
} satisfies DocumentBlocksPayload

const layout = {
  pages: [
    { page_number: 1, width: 1000, height: 1000 },
    { page_number: 2, width: 1000, height: 1000 },
  ],
} satisfies DocumentLayoutBlocksPayload

const tables = {
  physical_tables: [
    { table_id: 't1', block_id: 'b1', page_number: 1, bbox: [1, 1, 2, 2], markdown: '|a|' },
    { table_id: 't2', block_id: 'b2', page_number: 2, bbox: [1, 1, 2, 2], markdown: '|b|' },
  ],
} satisfies DocumentTablesPayload

const figures = {
  figures: [
    { image_id: 'f1', block_id: 'fb1', page_number: 2, bbox: [1, 1, 2, 2], caption: 'chart' },
  ],
} satisfies DocumentFiguresPayload

const tableRelations = {
  relations: [
    { relation_id: 'r1', source_table_id: 't1', target_table_id: 't2', relation_type: 'continuation' },
  ],
} satisfies { relations: DocumentTableRelation[] }

const sourceMap = {
  sources: [
    { block_id: 'b1', page_number: 1, open_source_url: '/source/b1' },
    { table_id: 't1', page_number: 1, open_source_url: '/source/t1' },
    { image_id: 'f1', page_number: 2, open_source_url: '/source/f1' },
  ],
} satisfies DocumentSourceMapPayload

const result = {
  manifest: { task_id: 'task-1' },
  markdown: '[PDF_PAGE: 1]\n\n# hello\n',
  artifacts: { 'quality_report.json': { exists: true } },
} satisfies DocumentResult

test('base view model builds page and markdown derivations', () => {
  const base = buildDocumentResultBaseViewModel({
    taskId: 'task-1',
    result,
    quality: { page_count: 3 } as never,
    blocks,
    layout,
    tables,
    tableRelations,
    figures,
    sourceMap,
  })

  assert.equal(base.taskId, 'task-1')
  assert.deepEqual(base.pageNumbers, [1, 2, 3])
  assert.deepEqual(base.artifactEntries.map(([key]) => key), ['quality_report.json'])
  assert.deepEqual(base.markdownBlocks.map((block) => block.pageNumber), [1, 2])
})

test('base view model keeps sparse payload fallbacks from the workbench derivations', () => {
  const base = buildDocumentResultBaseViewModel({
    taskId: 'task-sparse',
    result: {
      manifest: { task_id: 'task-sparse' },
      markdown: '[PDF_PAGE: 5]\n\nSparse page\n\n[PDF_PAGE: 7]\n\nTail page',
    },
    quality: null,
    blocks: null,
    layout: null,
    tables: {
      tables: [
        { table_id: 'fallback-table', block_id: 'fallback-block', page_number: 6, bbox: [1, 1, 2, 2] },
      ],
    },
    tableRelations: null,
    figures: null,
    sourceMap: null,
  })

  assert.deepEqual(base.physicalTables.map((table) => table.table_id), ['fallback-table'])
  assert.deepEqual(base.markdownBlocks.map((block) => block.pageNumber), [5, 7])
  assert.deepEqual(base.pageNumbers, [5, 6, 7])
  assert.equal(base.tableIdByBlockId.get('fallback-block'), 'fallback-table')
  assert.deepEqual(base.artifactEntries, [])
})

test('full view model adds focus-aware relations and preview pages', () => {
  const base = buildDocumentResultBaseViewModel({
    taskId: 'task-1',
    result,
    quality: { page_count: 3 } as never,
    blocks,
    layout,
    tables,
    tableRelations,
    figures,
    sourceMap,
  })

  const viewModel = buildDocumentResultViewModel({
    base,
    activePage: 2,
    focused: { kind: 'block', id: 'b1', page: 1 },
  })

  assert.deepEqual(viewModel.previewPages, [1, 2])
  assert.equal(viewModel.activeFocusKeys.has('block:b1'), true)
  assert.equal(viewModel.activeFocusKeys.has('table:t1'), true)
  assert.deepEqual(viewModel.focusedRelations.map((relation) => relation.relation_id), ['r1'])
  assert.equal(viewModel.previewMarkdownBlocks.length, 2)
  assert.equal(viewModel.previewPageModels[0]?.bridgeFocusId, 't2')
})
