/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { registerHooks } from 'node:module'
import { test } from 'node:test'

import type {
  DocumentBlock,
  DocumentFigure,
  DocumentLayoutPage,
  DocumentTable,
  DocumentTableRelation,
} from '@/lib/documentTypes.ts'
import type { OverlayEntry } from './documentResultWorkbenchUtils.ts'

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('./documentResultWorkbenchUtils')) {
      return nextResolve(`${specifier}.ts`, context)
    }
    return nextResolve(specifier, context)
  },
})

const {
  adjacentDocumentResultPage,
  buildDocumentResultFocusDerivation,
  buildDocumentResultJsonPreview,
  buildDocumentResultPageByNumber,
  buildDocumentResultPageNumbers,
  buildDocumentResultPreviewMarkdownBlocks,
  buildDocumentResultPreviewOverlays,
  buildDocumentResultPreviewPageModels,
  buildDocumentResultPreviewPages,
  buildDocumentResultPreviewRelations,
  buildDocumentResultRelationsByTableId,
  buildDocumentResultSourceLookups,
  buildDocumentResultTableLookups,
  buildDocumentResultVisibleRelations,
} = await import('./documentResultWorkbenchDerivations.ts')

const tables: DocumentTable[] = [
  { table_id: 't1', block_id: 'b1', page_number: 1, bbox: [10, 700, 500, 980], title: 'Revenue A' },
  { table_id: 't2', block_id: 'b2', page_number: 2, bbox: [10, 20, 500, 300], title: 'Revenue B' },
  { table_id: 't3', block_id: 'b3', page_number: 3, bbox: [10, 20, 500, 300], title: 'Revenue C' },
  { table_id: 't4', block_id: 'b4', page_number: 2, bbox: [20, 380, 520, 620], title: 'Same page' },
]

const tableLookups = buildDocumentResultTableLookups(tables)

const relations: DocumentTableRelation[] = [
  {
    relation_id: 'r1',
    source_table_id: 't1',
    target_table_id: 't2',
    relation_type: 'continuation',
    confidence: 0.91,
  },
  {
    relation_id: 'r2',
    source_table_id: 't2',
    target_table_id: 't3',
    relation_type: 'continuation',
    review_status: 'rejected',
  },
  {
    relation_id: 'r3',
    source_table_id: 't1',
    target_table_id: 't3',
    relation_type: 'continuation',
  },
  {
    relation_id: 'r4',
    fragment_table_ids: ['t2', 't4'],
    merge_status: 'candidate',
  },
  {
    relation_id: 'r5',
    fragment_table_ids: ['t2', 't3'],
    merge_status: 'candidate',
    visual_connector: { from_page: 2, to_page: 3 },
  },
]

test('buildDocumentResultJsonPreview preserves direct preview payload boundaries', () => {
  const manifest = { task_id: 'task-1', status: 'completed' }
  const preview = {
    manifest,
    blocks: { blocks: [{ block_id: 'b1', text: 'hello' }] },
    tables: null,
    figures: { figures: [] },
    sourceMap: null,
  }

  const result = buildDocumentResultJsonPreview(preview)

  assert.equal(result, preview)
  assert.equal(result.manifest, manifest)
  assert.equal(result.tables, null)
  assert.deepEqual(result.blocks?.blocks?.map((block) => block.block_id), ['b1'])
})

test('table relation derivations keep only adjacent preview relations and index each table id', () => {
  const previewRelations = buildDocumentResultPreviewRelations(relations, tableLookups.tableById)

  assert.deepEqual(previewRelations.map((relation) => relation.relation_id), ['r1', 'r5'])

  const byTableId = buildDocumentResultRelationsByTableId(previewRelations)
  assert.deepEqual(byTableId.get('t1')?.map((relation) => relation.relation_id), ['r1'])
  assert.deepEqual(byTableId.get('t2')?.map((relation) => relation.relation_id), ['r1', 'r5'])
  assert.deepEqual(byTableId.get('t3')?.map((relation) => relation.relation_id), ['r5'])
  assert.equal(byTableId.has('t4'), false)
})

test('focus derivation links blocks and tables without inventing relations for other focus kinds', () => {
  const previewRelations = buildDocumentResultPreviewRelations(relations, tableLookups.tableById)
  const relationsByTableId = buildDocumentResultRelationsByTableId(previewRelations)

  const blockFocus = buildDocumentResultFocusDerivation({
    focused: { kind: 'block', id: 'b2', page: 2 },
    tableIdByBlockId: tableLookups.tableIdByBlockId,
    blockIdByTableId: tableLookups.blockIdByTableId,
    relationsByTableId,
  })

  assert.equal(blockFocus.focusedTableId, 't2')
  assert.equal(blockFocus.activeFocusKeys.has('block:b2'), true)
  assert.equal(blockFocus.activeFocusKeys.has('table:t2'), true)
  assert.deepEqual(blockFocus.focusedRelations.map((relation) => relation.relation_id), ['r1', 'r5'])

  const tableFocus = buildDocumentResultFocusDerivation({
    focused: { kind: 'table', id: 't1', page: 1 },
    tableIdByBlockId: tableLookups.tableIdByBlockId,
    blockIdByTableId: tableLookups.blockIdByTableId,
    relationsByTableId,
  })

  assert.equal(tableFocus.focusedTableId, 't1')
  assert.equal(tableFocus.activeFocusKeys.has('table:t1'), true)
  assert.equal(tableFocus.activeFocusKeys.has('block:b1'), true)
  assert.deepEqual(tableFocus.focusedRelations.map((relation) => relation.relation_id), ['r1'])

  const figureFocus = buildDocumentResultFocusDerivation({
    focused: { kind: 'figure', id: 'fig1', page: 2 },
    tableIdByBlockId: tableLookups.tableIdByBlockId,
    blockIdByTableId: tableLookups.blockIdByTableId,
    relationsByTableId,
  })

  assert.equal(figureFocus.focusedTableId, '')
  assert.equal(figureFocus.activeFocusKeys.has('figure:fig1'), true)
  assert.deepEqual(figureFocus.focusedRelations, [])
})

test('visible relations fall back to active page unless a focus supplies relation context', () => {
  const previewRelations = buildDocumentResultPreviewRelations(relations, tableLookups.tableById)

  assert.deepEqual(
    buildDocumentResultVisibleRelations({
      activePage: 1,
      focusedRelations: [],
      previewRelations,
      tableById: tableLookups.tableById,
    }).map((relation) => relation.relation_id),
    ['r1'],
  )

  assert.deepEqual(
    buildDocumentResultVisibleRelations({
      activePage: 1,
      focusedRelations: [previewRelations[1]],
      previewRelations,
      tableById: tableLookups.tableById,
    }).map((relation) => relation.relation_id),
    ['r5'],
  )
})

test('preview pages and page models include relation pages, overlays, and bridge focus targets', () => {
  const previewRelations = buildDocumentResultPreviewRelations(relations, tableLookups.tableById)
  const visibleRelations = buildDocumentResultVisibleRelations({
    activePage: 2,
    focusedRelations: [],
    previewRelations,
    tableById: tableLookups.tableById,
  })
  const previewPages = buildDocumentResultPreviewPages({
    activePage: 2,
    visibleRelations,
    tableById: tableLookups.tableById,
  })
  const overlays: OverlayEntry[] = [
    { id: 'b1', kind: 'block', pageNumber: 1, bbox: [1, 1, 2, 2], bboxUnit: '', label: '段', detail: 'b1', focusKeys: ['block:b1'] },
    { id: 't2', kind: 'table', pageNumber: 2, bbox: [2, 2, 3, 3], bboxUnit: '', label: '表', detail: 't2', focusKeys: ['table:t2'] },
    { id: 't3', kind: 'table', pageNumber: 3, bbox: [3, 3, 4, 4], bboxUnit: '', label: '表', detail: 't3', focusKeys: ['table:t3'] },
  ]

  const models = buildDocumentResultPreviewPageModels({
    previewPages,
    visibleRelations,
    tableById: tableLookups.tableById,
    overlays,
  })

  assert.deepEqual(previewPages, [1, 2, 3])
  assert.deepEqual(models.map((model) => model.pageNumber), [1, 2, 3])
  assert.deepEqual(models.map((model) => model.overlays.map((overlay) => overlay.id)), [['b1'], ['t2'], ['t3']])
  assert.equal(models[0]?.bridgeRelation?.relation_id, 'r1')
  assert.equal(models[0]?.bridgeFocusId, 't2')
  assert.equal(models[0]?.bridgePage, 2)
  assert.equal(models[1]?.bridgeRelation?.relation_id, 'r5')
  assert.equal(models[1]?.bridgeFocusId, 't3')
  assert.equal(models[2]?.bridgeRelation, undefined)
  assert.equal(models[2]?.bridgeFocusId, '')
  assert.equal(models[2]?.bridgePage, 3)
})

test('preview page model bridge falls back when relation has no table identifiers', () => {
  const bridgeOnly: DocumentTableRelation = {
    relation_id: 'visual-only',
    relation_type: 'continuation',
    page_numbers: [4, 5],
  }

  const [model] = buildDocumentResultPreviewPageModels({
    previewPages: [4, 5],
    visibleRelations: [bridgeOnly],
    tableById: tableLookups.tableById,
    overlays: [],
  })

  assert.equal(model?.bridgeRelation?.relation_id, 'visual-only')
  assert.equal(model?.bridgeFocusId, 'relation-1')
  assert.equal(model?.bridgePage, 5)
})

test('page and overlay derivations normalize invalid pages and keep table blocks out of generic overlays', () => {
  const pageByNumber = buildDocumentResultPageByNumber([
    { page_number: 3, width: 612, height: 792 },
    { page_number: 0, width: 1, height: 1 },
  ] satisfies DocumentLayoutPage[])
  const blocks: DocumentBlock[] = [
    { block_id: 'body-1', type: 'text', page_number: 1, bbox: [10, 20, 100, 120] },
    { block_id: 'b2', type: 'table', page_number: 2, bbox: [10, 20, 100, 120] },
    { block_id: 'invalid', type: 'text', page_number: 9, bbox: [1, 2, 1, 4] },
  ]
  const figures: DocumentFigure[] = [
    { image_id: 'fig1', block_id: 'fb1', page_number: 4, bbox: [5, 5, 50, 80], caption: 'Chart' },
  ]
  const sourceLookups = buildDocumentResultSourceLookups({
    sources: [
      { block_id: 'body-1', open_source_url: '/source/body-1' },
      { table_id: 't2', open_source_url: '/source/t2' },
      { image_id: 'fig1', open_source_url: '/source/fig1' },
    ],
  })

  assert.deepEqual(
    buildDocumentResultPageNumbers({
      sourceBlocks: blocks,
      pageByNumber,
      physicalTables: tables.slice(0, 2),
      figureItems: figures,
      markdownBlocks: [{ id: 'm6', pageNumber: 6, type: 'markdown', title: 'm6', html: '', textPreview: '', focusKeys: [] }],
      qualityPageCount: 5,
    }),
    [1, 2, 3, 4, 5, 6, 9],
  )

  const overlays = buildDocumentResultPreviewOverlays({
    sourceBlocks: blocks,
    physicalTables: tables.slice(0, 2),
    figureItems: figures,
    sourceByBlockId: sourceLookups.sourceByBlockId,
    sourceByTableId: sourceLookups.sourceByTableId,
    sourceByFigureId: sourceLookups.sourceByFigureId,
    tableIdByBlockId: tableLookups.tableIdByBlockId,
  })

  assert.deepEqual(overlays.map((overlay) => `${overlay.kind}:${overlay.id}`), ['block:body-1', 'table:t1', 'table:t2', 'figure:fig1'])
  assert.equal(overlays.find((overlay) => overlay.id === 'body-1')?.sourceUrl, '/source/body-1')
  assert.deepEqual(overlays.find((overlay) => overlay.id === 't2')?.focusKeys, ['table:t2', 'block:b2'])
  assert.deepEqual(overlays.find((overlay) => overlay.id === 'fig1')?.focusKeys, ['figure:fig1', 'block:fb1'])
})

test('preview markdown blocks and adjacent page navigation handle empty and missing-active boundaries', () => {
  const markdownBlocks = [
    { id: 'm1', pageNumber: 1, type: 'markdown', title: 'p1', html: '', textPreview: '', focusKeys: [] },
    { id: 'm2', pageNumber: 2, type: 'markdown', title: 'p2', html: '', textPreview: '', focusKeys: [] },
    { id: 'm3', pageNumber: 3, type: 'markdown', title: 'p3', html: '', textPreview: '', focusKeys: [] },
  ]

  assert.deepEqual(
    buildDocumentResultPreviewMarkdownBlocks(markdownBlocks, [2, 3]).map((block) => block.id),
    ['m2', 'm3'],
  )
  assert.equal(adjacentDocumentResultPage([], 7, 1), 7)
  assert.equal(adjacentDocumentResultPage([1, 2, 4], 2, 1), 4)
  assert.equal(adjacentDocumentResultPage([1, 2, 4], 2, -1), 1)
  assert.equal(adjacentDocumentResultPage([1, 2, 4], 9, 1), 4)
  assert.equal(adjacentDocumentResultPage([1, 2, 4], 9, -1), 1)
})
