import type {
  DocumentBlock,
  DocumentBlocksPayload,
  DocumentFigure,
  DocumentFiguresPayload,
  DocumentLayoutPage,
  DocumentManifest,
  DocumentSourceMapPayload,
  DocumentTable,
  DocumentTableRelation,
  DocumentTablesPayload,
} from '@/lib/documentTypes'
import {
  buildMarkdownBlocks,
  blockLabel,
  focusKey,
  isPreviewCrossPageTableRelation,
  pageNumber,
  relationPages,
  relationTableIds,
  tableLabel,
  uniqueStrings,
  validBbox,
  type FocusTarget,
  type MarkdownBlock,
  type OverlayEntry,
  type SourceMapEntry,
} from './documentResultWorkbenchUtils'

export type DocumentResultJsonPreview = {
  manifest?: DocumentManifest | null
  blocks: DocumentBlocksPayload | null
  tables: DocumentTablesPayload | null
  figures: DocumentFiguresPayload | null
  sourceMap: DocumentSourceMapPayload | null
}

export type DocumentResultTableLookups = {
  tableById: Map<string, DocumentTable>
  tableByBlockId: Map<string, DocumentTable>
  tableIdByBlockId: Map<string, string>
  blockIdByTableId: Map<string, string>
}

export type DocumentResultPreviewPageModel = {
  pageNumber: number
  overlays: OverlayEntry[]
  relations: DocumentTableRelation[]
  bridgeRelation?: DocumentTableRelation
  bridgeFocusId: string
  bridgePage: number
}

export function buildDocumentResultJsonPreview(preview: DocumentResultJsonPreview) {
  return preview
}

export function buildDocumentResultSourceLookups(sourceMap: DocumentSourceMapPayload | null) {
  const sourceByBlockId = new Map<string, SourceMapEntry>()
  const sourceByTableId = new Map<string, SourceMapEntry>()
  const sourceByFigureId = new Map<string, SourceMapEntry>()

  sourceMap?.sources?.forEach((entry) => {
    if (entry.block_id && !sourceByBlockId.has(entry.block_id)) sourceByBlockId.set(entry.block_id, entry)
    if (entry.table_id && !sourceByTableId.has(entry.table_id)) sourceByTableId.set(entry.table_id, entry)
    if (entry.image_id && !sourceByFigureId.has(entry.image_id)) sourceByFigureId.set(entry.image_id, entry)
  })

  return { sourceByBlockId, sourceByTableId, sourceByFigureId }
}

export function buildDocumentResultPageByNumber(pages?: DocumentLayoutPage[] | null) {
  const lookup = new Map<number, DocumentLayoutPage>()
  pages?.forEach((page) => {
    const pageNum = pageNumber(page.page_number, 0)
    if (pageNum) lookup.set(pageNum, page)
  })
  return lookup
}

export function buildDocumentResultTableLookups(physicalTables: DocumentTable[]): DocumentResultTableLookups {
  const tableById = new Map<string, DocumentTable>()
  const tableByBlockId = new Map<string, DocumentTable>()
  const tableIdByBlockId = new Map<string, string>()
  const blockIdByTableId = new Map<string, string>()

  physicalTables.forEach((table) => {
    if (table.table_id) tableById.set(table.table_id, table)
    if (table.block_id && !tableByBlockId.has(table.block_id)) tableByBlockId.set(table.block_id, table)
    if (table.block_id && table.table_id && !tableIdByBlockId.has(table.block_id)) {
      tableIdByBlockId.set(table.block_id, table.table_id)
    }
    if (table.table_id && table.block_id && !blockIdByTableId.has(table.table_id)) {
      blockIdByTableId.set(table.table_id, table.block_id)
    }
  })

  return { tableById, tableByBlockId, tableIdByBlockId, blockIdByTableId }
}

export function buildDocumentResultPreviewRelations(
  relationItems: DocumentTableRelation[],
  tableById: Map<string, DocumentTable>,
) {
  return relationItems.filter((relation) => isPreviewCrossPageTableRelation(relation, tableById))
}

export function buildDocumentResultRelationsByTableId(previewRelations: DocumentTableRelation[]) {
  const lookup = new Map<string, DocumentTableRelation[]>()
  previewRelations.forEach((relation) => {
    relationTableIds(relation).forEach((tableId: string) => {
      if (!tableId) return
      const existing = lookup.get(tableId) || []
      existing.push(relation)
      lookup.set(tableId, existing)
    })
  })
  return lookup
}

export function buildDocumentResultFocusDerivation({
  focused,
  tableIdByBlockId,
  blockIdByTableId,
  relationsByTableId,
}: {
  focused: FocusTarget
  tableIdByBlockId: Map<string, string>
  blockIdByTableId: Map<string, string>
  relationsByTableId: Map<string, DocumentTableRelation[]>
}) {
  const activeFocusKeys = new Set<string>()
  let focusedTableId = ''

  if (focused) {
    activeFocusKeys.add(focusKey(focused.kind, focused.id))
    if (focused.kind === 'block') {
      const tableId = tableIdByBlockId.get(focused.id) || ''
      if (tableId) {
        activeFocusKeys.add(focusKey('table', tableId))
        focusedTableId = tableId
      }
    } else if (focused.kind === 'table') {
      focusedTableId = focused.id
      const blockId = blockIdByTableId.get(focused.id)
      if (blockId) activeFocusKeys.add(focusKey('block', blockId))
    }
  }

  return {
    activeFocusKeys,
    focusedTableId,
    focusedRelations: focusedTableId ? relationsByTableId.get(focusedTableId) || [] : [],
  }
}

export function buildDocumentResultVisibleRelations({
  activePage,
  focusedRelations,
  previewRelations,
  tableById,
}: {
  activePage: number
  focusedRelations: DocumentTableRelation[]
  previewRelations: DocumentTableRelation[]
  tableById: Map<string, DocumentTable>
}) {
  if (focusedRelations.length) return focusedRelations
  return previewRelations.filter((relation) => relationPages(relation, tableById).includes(activePage))
}

export function buildDocumentResultMarkdownBlocks(
  sourceBlocks: DocumentBlock[],
  markdown: string,
  tableByBlockId: Map<string, DocumentTable>,
) {
  return buildMarkdownBlocks(sourceBlocks, markdown, tableByBlockId)
}

export function buildDocumentResultPageNumbers({
  sourceBlocks,
  pageByNumber,
  physicalTables,
  figureItems,
  markdownBlocks,
  qualityPageCount,
}: {
  sourceBlocks: DocumentBlock[]
  pageByNumber: Map<number, DocumentLayoutPage>
  physicalTables: DocumentTable[]
  figureItems: DocumentFigure[]
  markdownBlocks: MarkdownBlock[]
  qualityPageCount?: number
}) {
  const pages = new Set<number>()
  sourceBlocks.forEach((block) => pages.add(pageNumber(block.page_number)))
  pageByNumber.forEach((_page, page) => pages.add(page))
  physicalTables.forEach((table) => pages.add(pageNumber(table.page_number)))
  figureItems.forEach((figure) => pages.add(pageNumber(figure.page_number)))
  markdownBlocks.forEach((block) => pages.add(pageNumber(block.pageNumber)))
  const pageCount = pageNumber(qualityPageCount, 0)
  for (let page = 1; page <= pageCount; page += 1) pages.add(page)
  return Array.from(pages).filter(Boolean).sort((a, b) => a - b)
}

export function buildDocumentResultPreviewOverlays({
  sourceBlocks,
  physicalTables,
  figureItems,
  sourceByBlockId,
  sourceByTableId,
  sourceByFigureId,
  tableIdByBlockId,
}: {
  sourceBlocks: DocumentBlock[]
  physicalTables: DocumentTable[]
  figureItems: DocumentFigure[]
  sourceByBlockId: Map<string, SourceMapEntry>
  sourceByTableId: Map<string, SourceMapEntry>
  sourceByFigureId: Map<string, SourceMapEntry>
  tableIdByBlockId: Map<string, string>
}) {
  const entries: OverlayEntry[] = []

  sourceBlocks.forEach((block) => {
    const bbox = validBbox(block.bbox)
    if (!bbox.length) return
    const id = block.block_id || `block-${entries.length + 1}`
    if (tableIdByBlockId.has(id)) return
    const source = sourceByBlockId.get(id)
    entries.push({
      id,
      kind: 'block',
      pageNumber: pageNumber(block.page_number),
      bbox,
      bboxUnit: block.bbox_unit || '',
      label: blockLabel(block.type),
      detail: `${id} · ${block.type || 'block'}`,
      sourceUrl: source?.open_source_url,
      focusKeys: uniqueStrings([
        focusKey('block', id),
        tableIdByBlockId.get(id) ? focusKey('table', tableIdByBlockId.get(id) || '') : '',
      ]),
    })
  })

  physicalTables.forEach((table, index) => {
    const bbox = validBbox(table.bbox)
    if (!bbox.length) return
    const id = table.table_id || `table-${index + 1}`
    const source = sourceByTableId.get(id)
    entries.push({
      id,
      kind: 'table',
      pageNumber: pageNumber(table.page_number),
      bbox,
      bboxUnit: table.bbox_unit || '',
      label: '表',
      detail: `${id} · ${tableLabel(table, id)}`,
      sourceUrl: source?.open_source_url,
      focusKeys: uniqueStrings([
        focusKey('table', id),
        table.block_id ? focusKey('block', table.block_id) : '',
      ]),
    })
  })

  figureItems.forEach((figure, index) => {
    const bbox = validBbox(figure.bbox)
    if (!bbox.length) return
    const id = figure.image_id || figure.block_id || `figure-${index + 1}`
    const source = sourceByFigureId.get(id)
    entries.push({
      id,
      kind: 'figure',
      pageNumber: pageNumber(figure.page_number),
      bbox,
      bboxUnit: figure.bbox_unit || '',
      label: '图',
      detail: `${id} · ${figure.caption || figure.type || 'figure'}`,
      sourceUrl: source?.open_source_url,
      focusKeys: uniqueStrings([
        focusKey('figure', id),
        figure.block_id ? focusKey('block', figure.block_id) : '',
      ]),
    })
  })

  return entries
}

export function buildDocumentResultPreviewPages({
  activePage,
  visibleRelations,
  tableById,
  limit = 3,
}: {
  activePage: number
  visibleRelations: DocumentTableRelation[]
  tableById: Map<string, DocumentTable>
  limit?: number
}) {
  const pages = new Set<number>([activePage])
  visibleRelations.forEach((relation) => {
    relationPages(relation, tableById).forEach((page) => pages.add(page))
  })
  return Array.from(pages).filter(Boolean).sort((a, b) => a - b).slice(0, limit)
}

export function buildDocumentResultPreviewMarkdownBlocks(
  markdownBlocks: MarkdownBlock[],
  previewPages: number[],
) {
  const visible = new Set(previewPages)
  return markdownBlocks.filter((block) => visible.has(pageNumber(block.pageNumber)))
}

export function buildDocumentResultPreviewPageModels({
  previewPages,
  visibleRelations,
  tableById,
  overlays,
}: {
  previewPages: number[]
  visibleRelations: DocumentTableRelation[]
  tableById: Map<string, DocumentTable>
  overlays: OverlayEntry[]
}): DocumentResultPreviewPageModel[] {
  return previewPages.map((page, index) => {
    const nextPage = previewPages[index + 1]
    const bridgeRelation = nextPage
      ? visibleRelations.find((relation) => {
        const pages = relationPages(relation, tableById)
        return pages.includes(page) && pages.includes(nextPage)
      })
      : undefined
    const bridgeTableIds = bridgeRelation ? relationTableIds(bridgeRelation) : []
    const bridgeFocusId = bridgeTableIds[1] || bridgeTableIds[0] || (bridgeRelation ? `relation-${index + 1}` : '')

    return {
      pageNumber: page,
      overlays: overlays.filter((entry) => entry.pageNumber === page),
      relations: visibleRelations.filter((relation) => relationPages(relation, tableById).includes(page)),
      bridgeRelation,
      bridgeFocusId,
      bridgePage: nextPage || page,
    }
  })
}

export function adjacentDocumentResultPage(pageNumbers: number[], activePage: number, direction: -1 | 1) {
  if (!pageNumbers.length) return activePage
  const currentIndex = pageNumbers.indexOf(activePage)
  const fallbackIndex = direction < 0 ? 0 : pageNumbers.length - 1
  const nextIndex = currentIndex === -1 ? fallbackIndex : currentIndex + direction
  return pageNumbers[Math.min(pageNumbers.length - 1, Math.max(0, nextIndex))] || activePage
}
