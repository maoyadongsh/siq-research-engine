import type {
  DocumentBlock,
  DocumentFigure,
  DocumentLayoutPage,
  DocumentSourceMapPayload,
  DocumentTable,
  DocumentTableRelation,
} from '@/lib/documentTypes'
import {
  buildMarkdownBlocks,
  blockLabel,
  focusKey,
  isPreviewCrossPageTableRelation,
  pageNumber,
  relationPages,
  tableLabel,
  uniqueStrings,
  validBbox,
  type MarkdownBlock,
  type OverlayEntry,
  type SourceMapEntry,
} from './documentResultWorkbenchUtils'

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

export function buildDocumentResultPreviewRelations(
  relationItems: DocumentTableRelation[],
  tableById: Map<string, DocumentTable>,
) {
  return relationItems.filter((relation) => isPreviewCrossPageTableRelation(relation, tableById))
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
