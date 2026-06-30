import { useEffect, useMemo, useRef, useState } from 'react'
import { FileText, Loader2 } from 'lucide-react'
import type { FocusEvent, MouseEvent, MutableRefObject, RefObject } from 'react'
import type { BboxExtent, PdfCtx, PageBlock, PageContent, SelectedTrace, SourceMeta, SourceTable } from '../../lib/pdfTypes'
import { artifactUrl, PDF_API } from '../../lib/pdfApi'
import { apiJson } from '../../lib/apiClient'
import { parseBbox as parsePdfBbox } from '../../lib/pdfSanitize'
import { PageMergeBridge, PdfPagePreviewCard } from './PdfSourcePagePreview'
import {
  PdfArtifactPane,
  PdfMarkdownContextPane,
  PdfMobileReviewTabs,
  PdfReviewComparePane,
  PdfReviewCorrectionPane,
  PdfReviewPdfPane,
  PdfReviewReadingPane,
  PdfSourceSummary,
} from './PdfSourceWorkbenchPanels'
import { blockFocusKey, type EnhancedTable, type PageOverlayEntry, type PagePlanEntry, type TableRelationCandidate } from './pdfSourceWorkbenchTypes'
import { renderPageContentHtml } from './pdfSourceRendering'

export interface PdfSourceWorkbenchProps {
  sourceVisible: boolean
  srcTable: SourceTable | null
  srcMeta: SourceMeta | null
  readingMode: 'table' | 'page'
  switchReadingMode: (mode: 'table' | 'page') => void | Promise<void>
  readingHtml: string
  pdfCtx: MutableRefObject<PdfCtx | null>
  editTableRef: RefObject<HTMLDivElement | null>
  pdfCurPage: number
  setPdfCurPage: (page: number) => void
  pdfZoom: string
  setPdfZoom: (zoom: string) => void
  getPdfUrl: (page: number) => string
  updatePdfViewer: (page: number) => void
  onTableClick: (e: MouseEvent<HTMLDivElement>) => void
  onTableFocus: (e: FocusEvent<HTMLDivElement>) => void
  onTableInput: () => void
  onReadingClick: (e: MouseEvent<HTMLDivElement>) => void
  corrStatusRef: RefObject<HTMLSelectElement | null>
  corrTextRef: RefObject<HTMLTextAreaElement | null>
  corrNoteRef: RefObject<HTMLTextAreaElement | null>
  saveCorrection: () => Promise<void>
}

type EnhancedArtifact = {
  schema_version?: number | string
  table_count?: number
  tables?: EnhancedTable[]
  pages?: Array<{
    page_number?: number
    pdf_page_number?: number
    printed_page_number?: string
    block_count?: number
    table_count?: number
  }>
}

type TableRelationArtifactEntry = {
  relation_id?: string
  relation_type?: 'continuation' | 'candidate_continuation' | string
  confidence?: number
  merge_confidence?: number
  reasons?: string[]
  merge_reasons?: string[]
  page_numbers?: number[]
  from_table_id?: string
  to_table_id?: string
  source_table_id?: string
  target_table_id?: string
  from_table_index?: number | null
  to_table_index?: number | null
  from_page_number?: number
  to_page_number?: number
  from_bbox?: number[]
  to_bbox?: number[]
}

type TableRelationsArtifact = {
  schema_version?: string
  ruleset_version?: string
  task_id?: string
  relations?: TableRelationArtifactEntry[]
}

type FocusedBlock = {
  key: string
  blockId: string
  blockType: string
  page: number
  bbox: number[]
}

function pageNumber(value: unknown, fallback = 1) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback
}

function validBbox(value: unknown): number[] {
  const bbox = parsePdfBbox(value)
  if (!bbox || bbox.length !== 4) return []
  if (bbox[2] <= bbox[0] || bbox[3] <= bbox[1]) return []
  return bbox
}

function normalizeText(value: unknown) {
  return String(value ?? '')
    .toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[，。；;：:,.、·—_()（）【】[\]{}-]/g, '')
}

function tableColumns(table: EnhancedTable) {
  const cols = Number(table.structure?.expanded_columns || 0)
  return Number.isFinite(cols) && cols > 0 ? cols : 0
}

function tableRows(table: EnhancedTable) {
  const rows = Number(table.rows || table.structure?.expanded_rows || 0)
  return Number.isFinite(rows) && rows > 0 ? rows : 0
}

function cssAttrValue(value: string) {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
}

function countHtmlRows(html: unknown) {
  const matches = String(html || '').match(/<tr\b/gi)
  return matches?.length || 0
}

function countHtmlColumns(html: unknown) {
  const row = String(html || '').match(/<tr\b[^>]*>([\s\S]*?)<\/tr>/i)?.[1] || ''
  if (!row) return 0
  let count = 0
  const cellRegex = /<(td|th)\b([^>]*)>/gi
  let match: RegExpExecArray | null
  while ((match = cellRegex.exec(row))) {
    const colspan = Number(match[2]?.match(/\bcolspan=["']?(\d+)/i)?.[1] || 1)
    count += Number.isFinite(colspan) && colspan > 0 ? colspan : 1
  }
  return count
}

function pageTablesForPage(tables: EnhancedTable[], page: number) {
  return tables
    .filter((table) => pageNumber(table.pdf_page_number, 0) === page && validBbox(table.bbox).length === 4)
    .slice()
    .sort((a, b) => {
      const ab = validBbox(a.bbox)
      const bb = validBbox(b.bbox)
      if (!ab.length || !bb.length) return 0
      return ab[1] - bb[1] || ab[0] - bb[0]
    })
}

function tableMergeKey(table: EnhancedTable, index = 0) {
  const tablePage = pageNumber(table.pdf_page_number, 0)
  const bbox = validBbox(table.bbox)
  if (tablePage && bbox.length) return `${tablePage}:${bbox.map((value) => Math.round(value)).join(',')}`
  return `${tablePage || 0}:idx:${table.table_index || table.content_table_source_id || table.table_id || index}`
}

function mergeTableData(existing: EnhancedTable, incoming: EnhancedTable): EnhancedTable {
  const incomingColumns = tableColumns(incoming)
  const existingColumns = tableColumns(existing)
  const incomingRows = tableRows(incoming)
  const existingRows = tableRows(existing)
  return {
    ...incoming,
    ...existing,
    table_id: existing.table_id || incoming.table_id,
    table_index: existing.table_index || incoming.table_index,
    content_table_source_id: existing.content_table_source_id || incoming.content_table_source_id,
    source: existing.source || incoming.source,
    confidence: existing.confidence || incoming.confidence,
    heading: existing.heading || incoming.heading,
    preview: existing.preview || incoming.preview,
    unit: existing.unit || incoming.unit,
    rows: existingRows ? existing.rows : incomingRows ? incoming.rows : existing.rows,
    cells: existing.cells || incoming.cells,
    structure: existingColumns
      ? existing.structure
      : incomingColumns
        ? { ...existing.structure, ...incoming.structure }
        : existing.structure || incoming.structure,
    matched_financial_names: existing.matched_financial_names?.length ? existing.matched_financial_names : incoming.matched_financial_names,
    missing_body: Boolean(existing.missing_body && incoming.missing_body),
  }
}

function pageContentPageNumber(pageContent: PageContent | undefined, fallback = 1) {
  return pageNumber(pageContent?.page_number || pageContent?.pdf_page_number || fallback, fallback)
}

function pageBlockTable(block: PageBlock, page: number, index: number): EnhancedTable | null {
  if (String(block.type || '').toLowerCase() !== 'table') return null
  const bbox = validBbox(block.bbox)
  if (!bbox.length) return null
  const columns = countHtmlColumns(block.table_html)
  const rows = countHtmlRows(block.table_html)
  const tableIndex = Number(block.table_index || block.source_table_index || 0)
  return {
    table_id: block.block_id || `page-block-${page}-${index + 1}`,
    table_index: tableIndex || undefined,
    content_table_source_id: Number(block.source_table_index || block.table_index || 0) || undefined,
    pdf_page_number: page,
    printed_page_number: block.printed_page_number,
    bbox,
    rows: rows || undefined,
    cells: rows && columns ? rows * columns : undefined,
    structure: columns || rows ? { expanded_columns: columns || undefined, expanded_rows: rows || undefined } : undefined,
    preview: blockText(block),
    heading: Array.isArray(block.heading) ? block.heading.join(' ') : block.heading,
    source: 'page_block',
    confidence: block.missing_body ? 'medium' : 'high',
    matched_financial_names: block.matched_financial_names,
    missing_body: Boolean(block.missing_body || !block.table_html),
  }
}

function tablesFromPageContentCache(pageContentCache: Record<number, PageContent>) {
  const tables: EnhancedTable[] = []
  for (const [pageKey, pageContent] of Object.entries(pageContentCache)) {
    const fallbackPage = pageNumber(pageKey, 1)
    const page = pageContentPageNumber(pageContent, fallbackPage)
    pageContentBlocks(pageContent).forEach((block, index) => {
      const table = pageBlockTable(block, page, index)
      if (table) tables.push(table)
    })
  }
  return tables
}

function sourceTableAsEnhanced(srcTable: SourceTable | null, srcMeta: SourceMeta | null): EnhancedTable | null {
  if (!srcTable) return null
  const bbox = validBbox(srcTable.bbox)
  if (!bbox.length) return null
  const columns = countHtmlColumns(srcTable.table_html)
  const rows = countHtmlRows(srcTable.table_html) || Number(srcTable.rows || 0)
  return {
    table_id: `source-table-${srcTable.pdf_page_number || srcMeta?.pdfPageImage?.page_number || 1}-${srcTable.table_index || 0}`,
    table_index: srcTable.table_index,
    line: srcTable.line,
    rows: rows || srcTable.rows,
    cells: srcTable.cells,
    structure: columns || rows ? { expanded_columns: columns || undefined, expanded_rows: rows || undefined } : undefined,
    pdf_page_number: pageNumber(srcTable.pdf_page_number || srcMeta?.pdfPageImage?.page_number || 1, 1),
    printed_page_number: srcTable.pdf_page_source === 'markdown_marker_inferred' ? undefined : srcMeta?.pdfPageImage?.printed_page_number,
    heading: srcTable.heading,
    unit: srcTable.unit,
    matched_financial_names: srcTable.matched_financial_names,
    bbox,
    source_image_path: srcTable.source_image_path,
    source: 'source_table',
    confidence: 'high',
    missing_body: !srcTable.table_html,
  }
}

function mergePhysicalTables(
  artifactTables: EnhancedTable[],
  pageContentCache: Record<number, PageContent>,
  srcTable: SourceTable | null,
  srcMeta: SourceMeta | null,
) {
  const merged = new Map<string, EnhancedTable>()
  const add = (table: EnhancedTable | null, index = 0) => {
    if (!table || !validBbox(table.bbox).length) return
    const key = tableMergeKey(table, index)
    const existing = merged.get(key)
    merged.set(key, existing ? mergeTableData(existing, table) : table)
  }
  artifactTables.forEach((table, index) => add(table, index))
  tablesFromPageContentCache(pageContentCache).forEach((table, index) => add(table, artifactTables.length + index))
  add(sourceTableAsEnhanced(srcTable, srcMeta), artifactTables.length + Object.keys(pageContentCache).length)
  return Array.from(merged.values()).sort((a, b) => {
    const ap = pageNumber(a.pdf_page_number, 0)
    const bp = pageNumber(b.pdf_page_number, 0)
    if (ap !== bp) return ap - bp
    const ab = validBbox(a.bbox)
    const bb = validBbox(b.bbox)
    return (ab[1] || 0) - (bb[1] || 0) || (ab[0] || 0) - (bb[0] || 0)
  })
}

function samePhysicalTable(first: EnhancedTable, second: EnhancedTable) {
  const firstBbox = validBbox(first.bbox)
  const secondBbox = validBbox(second.bbox)
  if (!firstBbox.length || !secondBbox.length) return false
  if (pageNumber(first.pdf_page_number, 0) !== pageNumber(second.pdf_page_number, 0)) return false
  return firstBbox.every((value, index) => Math.abs(value - secondBbox[index]) <= 2)
}

function firstTableOnPage(table: EnhancedTable, pageTables: EnhancedTable[]) {
  const bbox = validBbox(table.bbox)
  if (!bbox.length) return false
  const sameTables = pageTables.filter((item) => samePhysicalTable(table, item))
  const firstY = Math.min(...pageTables.map((item) => validBbox(item.bbox)[1] || bbox[1]).filter(Number.isFinite), bbox[1])
  return Math.min(...sameTables.map((item) => validBbox(item.bbox)[1] || bbox[1]).filter(Number.isFinite), bbox[1]) <= firstY + 2
}

function lastTableOnPage(table: EnhancedTable, pageTables: EnhancedTable[]) {
  const bbox = validBbox(table.bbox)
  if (!bbox.length) return false
  const sameTables = pageTables.filter((item) => samePhysicalTable(table, item))
  const lastY = Math.max(...pageTables.map((item) => validBbox(item.bbox)[3] || bbox[3]).filter(Number.isFinite), bbox[3])
  return Math.max(...sameTables.map((item) => validBbox(item.bbox)[3] || bbox[3]).filter(Number.isFinite), bbox[3]) >= lastY - 2
}

function pageExtentForPage(
  page: number,
  tables: EnhancedTable[],
  pageContent: PageContent | undefined,
  currentExtent: BboxExtent | null,
  currentPage: number,
) {
  let maxX = page === currentPage && currentExtent?.width ? currentExtent.width : 0
  let maxY = page === currentPage && currentExtent?.height ? currentExtent.height : 0
  for (const block of pageContentBlocks(pageContent)) {
    const bbox = validBbox(block.bbox)
    if (!bbox.length) continue
    maxX = Math.max(maxX, bbox[2])
    maxY = Math.max(maxY, bbox[3])
  }
  const pageTables = pageTablesForPage(tables, page)
  for (const table of pageTables) {
    const bbox = validBbox(table.bbox)
    if (!bbox.length) continue
    maxX = Math.max(maxX, bbox[2])
    maxY = Math.max(maxY, bbox[3])
  }
  if (!maxX || !maxY) return currentExtent || { width: 1000, height: 1000 }
  return { width: Math.max(1000, maxX * 1.04), height: Math.max(1000, maxY * 1.04) }
}

function tableDetail(table: EnhancedTable, fallback = '') {
  const parts = [table.heading || table.preview || fallback, table.printed_page_number ? `印刷页 ${table.printed_page_number}` : '', table.line ? `line ${table.line}` : '']
    .filter(Boolean)
    .join(' · ')
  return parts || fallback || '表格'
}

function pageBlockId(block: PageBlock, index: number, page: number) {
  return block.block_id || `p${page}-b${index + 1}`
}

function blockText(block: PageBlock) {
  const parts = [
    block.markdown,
    block.text,
    Array.isArray(block.heading) ? block.heading.join(' ') : block.heading,
    Array.isArray(block.caption) ? block.caption.join(' ') : block.caption,
    Array.isArray(block.list_items) ? block.list_items.join(' ') : block.list_items,
  ]
    .map((item) => String(item || '').trim())
    .filter(Boolean)
  return parts.join(' ').replace(/\s+/g, ' ').trim()
}

function blockOverlayLabel(block: PageBlock) {
  const type = String(block.type || '').toLowerCase()
  if (type === 'table') return '表'
  if (type === 'image') return '图'
  if (type === 'list') return '段'
  if (type === 'header' || type === 'title' || Number(block.text_level || 0) > 0 || isHeadingBlock(block)) return '题'
  return '段'
}

function blockY(block: PageBlock) {
  const bbox = validBbox(block.bbox)
  if (bbox.length) return bbox[1]
  return Number(block.line || 0)
}

function normalizeBlockText(value: unknown) {
  return String(value ?? '')
    .toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[，。；;：:,.、·—_()（）【】[\]{}-]/g, '')
}

function isUnitOrNoteLine(text: string) {
  const compact = normalizeBlockText(text)
  if (!compact) return true
  if (/^[-—_·•.\s0-9第页/]+$/.test(compact)) return true
  return /^(单位[:：])?([人民币港币美元欧元万亿元千元百万元亿元股%％,.，/]+)$/.test(compact)
}

function looksLikeTitle(text: string) {
  const compact = normalizeBlockText(text)
  if (!compact || compact.length > 80) return false
  if (isUnitOrNoteLine(text)) return false
  if (/^[一二三四五六七八九十]+[、.．]/.test(text)) return true
  if (/^[(（]?[一二三四五六七八九十0-9]+[)）]/.test(text)) return true
  if (/^第[一二三四五六七八九十0-9]+[章节]/.test(text)) return true
  if (/^[0-9]+[、.．]/.test(text)) return true
  return compact.length <= 32 && !/[，。；;：:]/.test(compact)
}

function isHeadingBlock(block: PageBlock, text = blockText(block)) {
  if (!text) return false
  if (isUnitOrNoteLine(text)) return false
  if (['1', '2', '3', '4', '5', '6'].includes(String(block.sub_type || ''))) return true
  if (['heading', 'section', 'caption'].includes(String(block.type || '').toLowerCase())) return true
  return looksLikeTitle(text)
}

function isIgnorablePageChrome(block: PageBlock) {
  const text = blockText(block)
  const bbox = validBbox(block.bbox)
  if (!text) return true
  const compact = normalizeBlockText(text)
  if (/^\d+$/.test(compact)) return true
  if (isUnitOrNoteLine(text)) return true
  const type = String(block.type || '').toLowerCase()
  if ((type === 'title' || type === 'header' || type === 'page_number') && bbox && (bbox[3] <= 96 || bbox[1] >= 890)) {
    return true
  }
  if (type === 'page_number') return true
  if (bbox && bbox[1] >= 900) return true
  return false
}

function pageBlocksBeforeY(blocks: PageBlock[], y: number) {
  return blocks
    .filter((block) => {
      const bbox = validBbox(block.bbox)
      return bbox.length === 4 && bbox[3] <= y + 2
    })
    .slice()
    .sort((a, b) => blockY(a) - blockY(b))
}

function pageBlocksAfterY(blocks: PageBlock[], y: number) {
  return blocks
    .filter((block) => {
      const bbox = validBbox(block.bbox)
      return bbox.length === 4 && bbox[1] > y + 2
    })
    .slice()
    .sort((a, b) => blockY(a) - blockY(b))
}

function pageContentBlocks(pageContent: PageContent | undefined) {
  return Array.isArray(pageContent?.blocks) ? pageContent.blocks : []
}

function hasTitleBeforeTableOnPage(table: EnhancedTable, pageContent: PageContent | undefined) {
  const tableBBox = validBbox(table.bbox)
  if (!tableBBox.length) return false
  const blocks = pageBlocksBeforeY(pageContentBlocks(pageContent), tableBBox[1])
  for (const block of blocks) {
    if (isIgnorablePageChrome(block)) continue
    if (String(block.type || '').toLowerCase() === 'table') continue
    const text = blockText(block)
    if (isHeadingBlock(block, text)) return true
  }
  return false
}

function hasBodyTextAfterTableOnPage(table: EnhancedTable, pageContent: PageContent | undefined) {
  const tableBBox = validBbox(table.bbox)
  if (!tableBBox.length) return false
  const blocks = pageBlocksAfterY(pageContentBlocks(pageContent), tableBBox[3])
  for (const block of blocks) {
    if (isIgnorablePageChrome(block)) continue
    if (String(block.type || '').toLowerCase() === 'table') continue
    const text = blockText(block)
    if (text && !isUnitOrNoteLine(text)) return true
  }
  return false
}

function passesCrossPageGeometry(fromTable: EnhancedTable, toTable: EnhancedTable, allTables: EnhancedTable[]) {
  const fromBBox = validBbox(fromTable.bbox)
  const toBBox = validBbox(toTable.bbox)
  if (!fromBBox.length || !toBBox.length) return false
  const pageHeight = Math.max(
    1000,
    ...allTables.filter((item) => pageNumber(item.pdf_page_number, 0) === pageNumber(fromTable.pdf_page_number, 0)).map((item) => validBbox(item.bbox)[3] || 0),
    ...allTables.filter((item) => pageNumber(item.pdf_page_number, 0) === pageNumber(toTable.pdf_page_number, 0)).map((item) => validBbox(item.bbox)[3] || 0),
  )
  return fromBBox[3] >= pageHeight * 0.68 && toBBox[1] <= pageHeight * 0.38
}

function compatibleContinuationColumns(fromTable: EnhancedTable, toTable: EnhancedTable) {
  const fromColumns = tableColumns(fromTable)
  const toColumns = tableColumns(toTable)
  if (!toColumns || toTable.missing_body) return true
  return Boolean(fromColumns && toColumns && fromColumns === toColumns)
}

function buildContinuationCandidate(
  fromTable: EnhancedTable,
  toTable: EnhancedTable,
  allTables: EnhancedTable[],
  pageContentCache: Record<number, PageContent>,
): TableRelationCandidate | null {
  const fromPage = pageNumber(fromTable.pdf_page_number, 0)
  const toPage = pageNumber(toTable.pdf_page_number, 0)
  if (!fromPage || !toPage || toPage !== fromPage + 1) return null

  const fromBBox = validBbox(fromTable.bbox)
  const toBBox = validBbox(toTable.bbox)
  if (!fromBBox.length || !toBBox.length) return null

  const fromPageTables = pageTablesForPage(allTables, fromPage)
  const toPageTables = pageTablesForPage(allTables, toPage)
  if (!fromPageTables.length || !toPageTables.length) return null
  if (!lastTableOnPage(fromTable, fromPageTables)) return null
  if (!firstTableOnPage(toTable, toPageTables)) return null

  const fromPageContent = pageContentCache[fromPage]
  const toPageContent = pageContentCache[toPage]
  if (!fromPageContent || !toPageContent) return null
  if (hasBodyTextAfterTableOnPage(fromTable, fromPageContent)) return null
  if (hasTitleBeforeTableOnPage(toTable, toPageContent)) return null
  if (!passesCrossPageGeometry(fromTable, toTable, allTables)) return null

  const fromColumns = tableColumns(fromTable)
  const toColumns = tableColumns(toTable)
  if (!compatibleContinuationColumns(fromTable, toTable)) return null

  const fromRows = tableRows(fromTable)
  const toRows = tableRows(toTable)
  if (fromRows && fromRows < 2) return null
  if (toRows && toRows < 2) return null

  let score = 0
  const reasons: string[] = []

  score += 0.22
  reasons.push('adjacent_pages')

  if (fromColumns && toColumns && fromColumns === toColumns) {
    score += 0.22
    reasons.push('same_column_count')
  } else if (!toColumns || toTable.missing_body) {
    score += 0.08
    reasons.push('target_table_signature_missing')
  }

  if (fromBBox[3] >= Math.max(...fromPageTables.map((item) => validBbox(item.bbox)[3] || 0), 1000) * 0.68) {
    score += 0.16
    reasons.push('first_fragment_near_page_bottom')
  }
  if (toBBox[1] <= Math.max(...toPageTables.map((item) => validBbox(item.bbox)[3] || 0), 1000) * 0.38) {
    score += 0.16
    reasons.push('second_fragment_near_page_top')
  }

  const fromWidth = Math.max(1, fromBBox[2] - fromBBox[0])
  const toWidth = Math.max(1, toBBox[2] - toBBox[0])
  const widthRatio = Math.min(fromWidth, toWidth) / Math.max(fromWidth, toWidth)
  const leftDelta = Math.abs(fromBBox[0] - toBBox[0]) / Math.max(fromWidth, toWidth)
  if (widthRatio >= 0.75) {
    score += 0.12
    reasons.push('similar_table_width')
  }
  if (leftDelta <= 0.2) {
    score += 0.08
    reasons.push('similar_left_edge')
  }

  const fromTitle = normalizeText(fromTable.heading || fromTable.preview || '')
  const toTitle = normalizeText(toTable.heading || toTable.preview || '')
  if (fromTitle && toTitle && fromTitle === toTitle) {
    score += 0.1
    reasons.push('same_caption')
  } else if (fromTitle && !toTitle) {
    score += 0.05
    reasons.push('caption_inherited')
  }

  const sourcePageTables = fromPageTables.slice().sort((a, b) => {
    const ab = validBbox(a.bbox)
    const bb = validBbox(b.bbox)
    return (ab[1] || 0) - (bb[1] || 0) || (ab[0] || 0) - (bb[0] || 0)
  })
  const targetPageTables = toPageTables.slice().sort((a, b) => {
    const ab = validBbox(a.bbox)
    const bb = validBbox(b.bbox)
    return (ab[1] || 0) - (bb[1] || 0) || (ab[0] || 0) - (bb[0] || 0)
  })
  if (sourcePageTables[sourcePageTables.length - 1] && samePhysicalTable(fromTable, sourcePageTables[sourcePageTables.length - 1])) {
    score += 0.12
    reasons.push('last_table_on_source_page')
  }
  if (targetPageTables[0] && samePhysicalTable(toTable, targetPageTables[0])) {
    score += 0.18
    reasons.push('first_table_on_target_page')
  } else {
    score -= 0.22
    reasons.push('not_first_table_on_target_page')
  }

  if (score < 0.58) return null
  const relationType: TableRelationCandidate['relationType'] = score >= 0.82 ? 'continuation' : 'candidate_continuation'
  return {
    relationType,
    confidence: Math.min(0.98, Math.round(score * 100) / 100),
    reasons,
    fromTable,
    toTable,
    pageNumbers: [fromPage, toPage],
  }
}

function findForwardRelation(fromTable: EnhancedTable, tables: EnhancedTable[], pageContentCache: Record<number, PageContent>) {
  const fromPage = pageNumber(fromTable.pdf_page_number, 0)
  if (!fromPage) return null
  const nextTables = pageTablesForPage(tables, fromPage + 1)
  if (!nextTables.length) return null
  let best: TableRelationCandidate | null = null
  for (const toTable of nextTables) {
    const candidate = buildContinuationCandidate(fromTable, toTable, tables, pageContentCache)
    if (!candidate) continue
    if (!best || candidate.confidence > best.confidence) best = candidate
  }
  return best
}

function findBackwardRelation(currentTable: EnhancedTable, tables: EnhancedTable[], pageContentCache: Record<number, PageContent>) {
  const currentPage = pageNumber(currentTable.pdf_page_number, 0)
  if (!currentPage) return null
  const prevTables = pageTablesForPage(tables, currentPage - 1)
  if (!prevTables.length) return null
  const currentPageTables = pageTablesForPage(tables, currentPage)
  if (!firstTableOnPage(currentTable, currentPageTables)) return null
  let best: TableRelationCandidate | null = null
  for (const fromTable of prevTables) {
    const candidate = buildContinuationCandidate(fromTable, currentTable, tables, pageContentCache)
    if (!candidate) continue
    if (!best || candidate.confidence > best.confidence) best = candidate
  }
  return best
}

function chooseFocusTableIndex(page: number, sourcePage: number, sourceTableIndex: number, tables: EnhancedTable[]) {
  if (page === sourcePage && sourceTableIndex) return sourceTableIndex
  return pageTablesForPage(tables, page)[0]?.table_index || sourceTableIndex || 0
}

function relationModeForPage(entryPage: number, relation: TableRelationCandidate): 'from' | 'to' | null {
  if (relation.pageNumbers[0] === entryPage) return 'from'
  if (relation.pageNumbers[1] === entryPage) return 'to'
  return null
}

function relationArtifactToCandidate(entry: TableRelationArtifactEntry, tables: EnhancedTable[]): TableRelationCandidate | null {
  const pages = Array.isArray(entry.page_numbers) ? entry.page_numbers.map((item) => pageNumber(item, 0)).filter(Boolean) : []
  const fromPage = pageNumber(entry.from_page_number || pages[0], 0)
  const toPage = pageNumber(entry.to_page_number || pages[1], 0)
  if (!fromPage || !toPage || toPage !== fromPage + 1) return null
  const fromBBox = validBbox(entry.from_bbox)
  const toBBox = validBbox(entry.to_bbox)
  if (!fromBBox.length || !toBBox.length) return null
  const fromTable =
    tables.find((table) => pageNumber(table.pdf_page_number, 0) === fromPage && Number(table.table_index || 0) === Number(entry.from_table_index || 0) && entry.from_table_index) ||
    tables.find((table) => pageNumber(table.pdf_page_number, 0) === fromPage && sameBbox(validBbox(table.bbox), fromBBox)) ||
    {
      table_id: entry.from_table_id || entry.source_table_id,
      table_index: Number(entry.from_table_index || 0) || undefined,
      pdf_page_number: fromPage,
      bbox: fromBBox,
      source: 'table_relations',
      confidence: 'high',
    }
  const toTable =
    tables.find((table) => pageNumber(table.pdf_page_number, 0) === toPage && Number(table.table_index || 0) === Number(entry.to_table_index || 0) && entry.to_table_index) ||
    tables.find((table) => pageNumber(table.pdf_page_number, 0) === toPage && sameBbox(validBbox(table.bbox), toBBox)) ||
    {
      table_id: entry.to_table_id || entry.target_table_id,
      table_index: Number(entry.to_table_index || 0) || undefined,
      pdf_page_number: toPage,
      bbox: toBBox,
      source: 'table_relations',
      confidence: 'high',
      missing_body: true,
    }
  const relationType = entry.relation_type === 'continuation' ? 'continuation' : 'candidate_continuation'
  return {
    relationType,
    confidence: Number(entry.confidence || entry.merge_confidence || (relationType === 'continuation' ? 0.9 : 0.6)),
    reasons: entry.reasons || entry.merge_reasons || ['table_relations_artifact'],
    fromTable,
    toTable,
    pageNumbers: [fromPage, toPage],
  }
}

function sameBbox(first: number[], second: number[]) {
  if (first.length !== 4 || second.length !== 4) return false
  return first.every((value, index) => Math.abs(value - second[index]) <= 2)
}

function relationsFromArtifactForPage(
  artifact: TableRelationsArtifact | null,
  page: number,
  tables: EnhancedTable[],
) {
  const relations = Array.isArray(artifact?.relations) ? artifact.relations : []
  return relations
    .map((entry) => relationArtifactToCandidate(entry, tables))
    .filter((item): item is TableRelationCandidate => Boolean(item))
    .filter((item) => item.pageNumbers[0] === page || item.pageNumbers[1] === page)
}

function deriveTaskId(urls: string[]) {
  for (const url of urls) {
    if (!url) continue
    const match = url.match(/\/api\/(?:pdf\/)?(?:pdf_page|artifact|source)\/([^/]+)/)
    if (match?.[1]) return decodeURIComponent(match[1])
  }
  return ''
}

function buildPagePreviewOverlays({
  pageNumberValue,
  currentPage,
  focusTableIndex,
  tables,
  blocks,
  currentTrace,
  focusedBlockKey,
}: {
  pageNumberValue: number
  currentPage: number
  focusTableIndex: number
  tables: EnhancedTable[]
  blocks: PageBlock[]
  currentTrace: SelectedTrace | null
  focusedBlockKey: string
}) {
  const pageTables = tables.filter((table) => pageNumber(table.pdf_page_number, 0) === pageNumberValue)
  const overlays: PageOverlayEntry[] = []

  blocks.forEach((block, index) => {
    const type = String(block.type || '').toLowerCase()
    if (type === 'table') return
    const bbox = validBbox(block.bbox)
    if (!bbox.length) return
    if (isIgnorablePageChrome(block)) return
    const blockId = pageBlockId(block, index, pageNumberValue)
    const key = blockFocusKey(pageNumberValue, blockId)
    overlays.push({
      blockId,
      blockType: block.type || 'block',
      pageNumber: pageNumberValue,
      bbox,
      label: blockOverlayLabel(block),
      detail: `${blockId} · ${block.type || 'block'}`,
      tone: key === focusedBlockKey ? 'focused' : 'block',
      source: 'block',
    })
  })

  pageTables.forEach((table) => {
    const bbox = validBbox(table.bbox)
    if (!bbox.length) return
    const isFocused = pageNumberValue === currentPage && Number(table.table_index || 0) === Number(focusTableIndex || 0)
    overlays.push({
      tableIndex: table.table_index,
      bbox,
      label: '表',
      detail: tableDetail(table, `p${pageNumberValue}`),
      tone: isFocused ? 'focused' : 'table',
      source: 'table',
    })
  })

  if (currentTrace && !focusedBlockKey && pageNumberValue === currentTrace.pageNumber && currentTrace.bbox?.length === 4) {
    overlays.push({
      bbox: currentTrace.bbox,
      label: currentTrace.source === 'cell_bbox' ? '单元格' : '文本',
      detail: currentTrace.source === 'cell_bbox' ? '单元格区域' : '文本锚定区域',
      tone: currentTrace.source === 'cell_bbox' ? 'focused' : 'trace',
      source: 'trace',
    })
  }

  return overlays
}

function renderFallbackPageHtml(html: string, pageNumberValue: number, currentPage: number) {
  if (!html) return ''
  return html.includes('pdf-page-reading-view')
    ? html
    : `<div class="pdf-page-reading-view"><div class="pdf-page-reading-summary"><div><strong>PDF 第 ${pageNumberValue || currentPage}</strong><span>0 个解析块 / 0 张表</span></div></div>${html}</div>`
}

export function PdfSourceWorkbench(props: PdfSourceWorkbenchProps) {
  const {
    sourceVisible,
    srcTable,
    srcMeta,
    readingMode,
    switchReadingMode,
    readingHtml,
    editTableRef,
    pdfCtx,
    pdfCurPage,
    setPdfCurPage,
    pdfZoom,
    setPdfZoom,
    getPdfUrl,
    updatePdfViewer,
    onTableClick,
    onTableFocus,
    onTableInput,
    onReadingClick,
    corrStatusRef,
    corrTextRef,
    corrNoteRef,
    saveCorrection,
  } = props

  const workbenchRef = useRef<HTMLDivElement | null>(null)
  const markdownPaneRef = useRef<HTMLDivElement | null>(null)
  const [mobileTab, setMobileTab] = useState<'pdf' | 'md'>('pdf')
  const [enhancedArtifactResult, setEnhancedArtifactResult] = useState<{ url: string; data: EnhancedArtifact } | null>(null)
  const [enhancedArtifactError, setEnhancedArtifactError] = useState<{ url: string; message: string } | null>(null)
  const [relationsArtifactResult, setRelationsArtifactResult] = useState<{ url: string; data: TableRelationsArtifact } | null>(null)
  const [pageContentCache, setPageContentCache] = useState<Record<number, PageContent>>({})
  const [workbenchTrace, setWorkbenchTrace] = useState<{ scopeKey: string; trace: SelectedTrace } | null>(null)
  const [focusedBlock, setFocusedBlock] = useState<FocusedBlock | null>(null)

  const pageImage = srcMeta?.pdfPageImage
  const pageImageUrl = pageImage?.url || ''
  const artifactUrlValue = artifactUrl(srcMeta?.artifacts?.['content_list_enhanced.json'])
  const relationsArtifactUrlValue = artifactUrl(srcMeta?.artifacts?.['table_relations.json'])
  const enhancedArtifact = enhancedArtifactResult?.url === artifactUrlValue ? enhancedArtifactResult.data : null
  const relationsArtifact = relationsArtifactResult?.url === relationsArtifactUrlValue ? relationsArtifactResult.data : null
  const enhancedError = enhancedArtifactError?.url === artifactUrlValue ? enhancedArtifactError.message : ''

  useEffect(() => {
    if (!sourceVisible || !srcTable) return
    const root = workbenchRef.current
    if (!root) return

    const cleanups: Array<() => void> = []
    const wraps = Array.from(root.querySelectorAll<HTMLElement>('.pdf-table-wrap')).filter(
      (wrap) => !wrap.previousElementSibling?.classList.contains('pdf-table-x-scrollbar'),
    )

    wraps.forEach((wrap) => {
      const proxy = document.createElement('div')
      proxy.className = 'pdf-table-x-scrollbar'
      proxy.setAttribute('aria-label', '横向拖动表格')
      proxy.setAttribute('role', 'scrollbar')
      proxy.tabIndex = 0

      const track = document.createElement('div')
      track.className = 'pdf-table-x-scrollbar-track'
      const thumb = document.createElement('div')
      thumb.className = 'pdf-table-x-scrollbar-thumb'
      track.appendChild(thumb)
      proxy.appendChild(track)
      wrap.before(proxy)

      let thumbWidth = 54
      let dragStartX = 0
      let dragStartScroll = 0
      let dragPointerId: number | null = null

      const updateProxy = () => {
        const canScroll = wrap.scrollWidth > wrap.clientWidth + 2
        proxy.classList.toggle('is-hidden', !canScroll)
        const trackWidth = track.clientWidth
        const maxScroll = Math.max(0, wrap.scrollWidth - wrap.clientWidth)
        thumbWidth = Math.max(54, Math.round((wrap.clientWidth / Math.max(wrap.scrollWidth, 1)) * trackWidth))
        const maxThumbLeft = Math.max(0, trackWidth - thumbWidth)
        const thumbLeft = maxScroll > 0 ? Math.round((wrap.scrollLeft / maxScroll) * maxThumbLeft) : 0
        thumb.style.width = `${Math.min(thumbWidth, trackWidth)}px`
        thumb.style.left = `${thumbLeft}px`
        proxy.setAttribute('aria-valuemin', '0')
        proxy.setAttribute('aria-valuemax', String(Math.round(maxScroll)))
        proxy.setAttribute('aria-valuenow', String(Math.round(wrap.scrollLeft)))
      }

      const scrollFromClientX = (clientX: number, centerThumb = false) => {
        const trackRect = track.getBoundingClientRect()
        const maxScroll = Math.max(0, wrap.scrollWidth - wrap.clientWidth)
        const maxThumbLeft = Math.max(1, trackRect.width - thumbWidth)
        const thumbOffset = centerThumb ? thumbWidth / 2 : 0
        const ratio = Math.min(1, Math.max(0, (clientX - trackRect.left - thumbOffset) / maxThumbLeft))
        wrap.scrollLeft = ratio * maxScroll
        updateProxy()
      }

      const onThumbPointerMove = (event: PointerEvent) => {
        if (dragPointerId !== event.pointerId || !thumb.classList.contains('is-dragging')) return
        event.preventDefault()
        const trackWidth = track.clientWidth
        const maxScroll = Math.max(0, wrap.scrollWidth - wrap.clientWidth)
        const maxThumbLeft = Math.max(1, trackWidth - thumbWidth)
        const delta = event.clientX - dragStartX
        wrap.scrollLeft = dragStartScroll + delta * (maxScroll / maxThumbLeft)
        updateProxy()
      }

      const stopThumbDrag = (event: PointerEvent) => {
        if (dragPointerId !== event.pointerId) return
        try {
          if (thumb.hasPointerCapture(event.pointerId)) thumb.releasePointerCapture(event.pointerId)
        } catch {
          // Firefox can throw if capture was already released.
        }
        dragPointerId = null
        thumb.classList.remove('is-dragging')
        document.body.classList.remove('pdf-table-x-dragging')
        document.removeEventListener('pointermove', onThumbPointerMove, true)
        document.removeEventListener('pointerup', stopThumbDrag, true)
        document.removeEventListener('pointercancel', stopThumbDrag, true)
      }

      const onThumbPointerDown = (event: PointerEvent) => {
        event.preventDefault()
        event.stopPropagation()
        dragStartX = event.clientX
        dragStartScroll = wrap.scrollLeft
        dragPointerId = event.pointerId
        thumb.classList.add('is-dragging')
        document.body.classList.add('pdf-table-x-dragging')
        try {
          thumb.setPointerCapture(event.pointerId)
        } catch {
          // Pointer capture is best-effort across embedded browsers.
        }
        document.addEventListener('pointermove', onThumbPointerMove, true)
        document.addEventListener('pointerup', stopThumbDrag, true)
        document.addEventListener('pointercancel', stopThumbDrag, true)
      }

      const onTrackPointerDown = (event: PointerEvent) => {
        if (event.target === thumb) return
        event.preventDefault()
        scrollFromClientX(event.clientX, true)
      }

      const onProxyKeyDown = (event: KeyboardEvent) => {
        const step = event.shiftKey ? wrap.clientWidth * 0.75 : 80
        if (event.key === 'ArrowLeft') {
          event.preventDefault()
          wrap.scrollLeft -= step
        } else if (event.key === 'ArrowRight') {
          event.preventDefault()
          wrap.scrollLeft += step
        } else if (event.key === 'Home') {
          event.preventDefault()
          wrap.scrollLeft = 0
        } else if (event.key === 'End') {
          event.preventDefault()
          wrap.scrollLeft = wrap.scrollWidth
        }
        updateProxy()
      }

      wrap.addEventListener('scroll', updateProxy, { passive: true })
      track.addEventListener('pointerdown', onTrackPointerDown)
      thumb.addEventListener('pointerdown', onThumbPointerDown)
      proxy.addEventListener('keydown', onProxyKeyDown)
      window.addEventListener('resize', updateProxy)

      const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(updateProxy) : null
      observer?.observe(wrap)
      const table = wrap.querySelector('table')
      if (table) observer?.observe(table)

      const raf = window.requestAnimationFrame(updateProxy)
      cleanups.push(() => {
        window.cancelAnimationFrame(raf)
        observer?.disconnect()
        window.removeEventListener('resize', updateProxy)
        wrap.removeEventListener('scroll', updateProxy)
        track.removeEventListener('pointerdown', onTrackPointerDown)
        thumb.removeEventListener('pointerdown', onThumbPointerDown)
        proxy.removeEventListener('keydown', onProxyKeyDown)
        document.removeEventListener('pointermove', onThumbPointerMove, true)
        document.removeEventListener('pointerup', stopThumbDrag, true)
        document.removeEventListener('pointercancel', stopThumbDrag, true)
        document.body.classList.remove('pdf-table-x-dragging')
        proxy.remove()
      })
    })

    return () => cleanups.forEach((cleanup) => cleanup())
  }, [sourceVisible, srcTable, readingMode, readingHtml, pdfCurPage, pageContentCache, enhancedArtifact])

  const ctx = pdfCtx.current
  const pageCount = ctx?.pageCount || srcMeta?.pdfPageImage?.page_count || pdfCurPage
  const sourcePage = pageNumber(srcMeta?.pdfPageImage?.page_number || srcTable?.pdf_page_number || pdfCurPage)
  const sourceTableIndex = Number(srcTable?.table_index || 0)
  const currentPage = pageNumber(pdfCurPage, sourcePage)
  const pageUrl = sourceVisible && srcTable ? getPdfUrl(currentPage) : ''
  const currentExtent = ctx?.bboxExtent?.width && ctx?.bboxExtent?.height ? ctx.bboxExtent : (pageImage?.bbox_extent || null)
  const taskId = useMemo(() => {
    return deriveTaskId([artifactUrlValue, pageImageUrl, pageUrl, getPdfUrl(currentPage)])
  }, [artifactUrlValue, pageImageUrl, pageUrl, getPdfUrl, currentPage])
  const enhancedLoading = Boolean(sourceVisible && taskId && artifactUrlValue && !enhancedArtifact && !enhancedError)
  const traceScopeKey = `${taskId || ''}:${sourcePage}:${sourceTableIndex}:${srcTable?.line || ''}`
  const focusedBlockKey = focusedBlock?.key || ''

  const currentTables = useMemo(
    () => mergePhysicalTables(enhancedArtifact?.tables || [], pageContentCache, srcTable, srcMeta),
    [enhancedArtifact, pageContentCache, srcMeta, srcTable],
  )
  const focusTableIndex = useMemo(() => {
    if (currentPage === sourcePage && sourceTableIndex) return sourceTableIndex
    return chooseFocusTableIndex(currentPage, sourcePage, sourceTableIndex, currentTables)
  }, [currentPage, currentTables, sourcePage, sourceTableIndex])

  const currentFocusTable = useMemo(() => {
    if (!currentTables.length || !focusTableIndex) return null
    return currentTables.find((table) => Number(table.table_index || 0) === focusTableIndex) || null
  }, [currentTables, focusTableIndex])

  const forwardRelation = useMemo(() => {
    const artifactRelation = relationsFromArtifactForPage(relationsArtifact, currentPage, currentTables).find((relation) => relation.pageNumbers[0] === currentPage) || null
    if (artifactRelation) return artifactRelation
    if (!currentFocusTable) return null
    const currentPageTables = pageTablesForPage(currentTables, currentPage)
    if (!lastTableOnPage(currentFocusTable, currentPageTables)) return null
    return findForwardRelation(currentFocusTable, currentTables, pageContentCache)
  }, [currentFocusTable, currentPage, currentTables, pageContentCache, relationsArtifact])

  const backwardRelation = useMemo(() => {
    const artifactRelation = relationsFromArtifactForPage(relationsArtifact, currentPage, currentTables).find((relation) => relation.pageNumbers[1] === currentPage) || null
    if (artifactRelation) return artifactRelation
    if (!currentFocusTable) return null
    const currentPageTables = pageTablesForPage(currentTables, currentPage)
    if (!firstTableOnPage(currentFocusTable, currentPageTables)) return null
    return findBackwardRelation(currentFocusTable, currentTables, pageContentCache)
  }, [currentFocusTable, currentPage, currentTables, pageContentCache, relationsArtifact])

  const pagePlan = useMemo<PagePlanEntry[]>(() => {
    const plan: PagePlanEntry[] = []
    if (backwardRelation) {
      plan.push({
        pageNumber: backwardRelation.pageNumbers[0],
        focusTableIndex: Number(backwardRelation.fromTable.table_index || 0),
        relation: backwardRelation,
      })
    }
    plan.push({
      pageNumber: currentPage,
      focusTableIndex,
    })
    if (forwardRelation) {
      plan.push({
        pageNumber: forwardRelation.pageNumbers[1],
        focusTableIndex: Number(forwardRelation.toTable.table_index || 0),
        relation: forwardRelation,
      })
    }
    const seen = new Set<number>()
    return plan
      .filter((item) => {
        if (seen.has(item.pageNumber)) return false
        seen.add(item.pageNumber)
        return true
      })
      .sort((a, b) => a.pageNumber - b.pageNumber)
  }, [backwardRelation, currentPage, focusTableIndex, forwardRelation])

  const probePagePlan = useMemo<PagePlanEntry[]>(() => {
    const plan = new Map<number, PagePlanEntry>()
    const addEntry = (entry: PagePlanEntry) => {
      if (!entry.pageNumber || entry.pageNumber < 1 || entry.pageNumber > pageCount) return
      const existing = plan.get(entry.pageNumber)
      if (!existing || (entry.relation && !existing.relation)) {
        plan.set(entry.pageNumber, entry)
      }
    }

    addEntry({ pageNumber: Math.max(1, currentPage - 1), focusTableIndex: chooseFocusTableIndex(Math.max(1, currentPage - 1), sourcePage, sourceTableIndex, currentTables) })
    addEntry({ pageNumber: currentPage, focusTableIndex })
    addEntry({ pageNumber: Math.min(pageCount, currentPage + 1), focusTableIndex: chooseFocusTableIndex(Math.min(pageCount, currentPage + 1), sourcePage, sourceTableIndex, currentTables) })

    for (const entry of pagePlan) addEntry(entry)

    return Array.from(plan.values()).sort((a, b) => a.pageNumber - b.pageNumber)
  }, [currentPage, currentTables, focusTableIndex, pageCount, pagePlan, sourcePage, sourceTableIndex])

  useEffect(() => {
    if (!sourceVisible) return
    const url = artifactUrlValue
    if (!taskId || !url) return

    let cancelled = false
    const controller = new AbortController()
    void apiJson<EnhancedArtifact>(url, { signal: controller.signal })
      .then((data) => {
        if (!cancelled) {
          setEnhancedArtifactError(null)
          setEnhancedArtifactResult({ url, data })
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setEnhancedArtifactResult(null)
          setEnhancedArtifactError({ url, message: error instanceof Error ? error.message : '读取增强产物失败' })
        }
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [artifactUrlValue, sourceVisible, taskId])

  useEffect(() => {
    if (!sourceVisible) return
    if (!relationsArtifactUrlValue) return

    let cancelled = false
    const controller = new AbortController()
    void apiJson<TableRelationsArtifact>(relationsArtifactUrlValue, { signal: controller.signal })
      .then((data) => {
        if (!cancelled) setRelationsArtifactResult({ url: relationsArtifactUrlValue, data })
      })
      .catch((error) => {
        if (!cancelled) {
          setRelationsArtifactResult(null)
          console.warn('Failed to load table_relations.json', error)
        }
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [relationsArtifactUrlValue, sourceVisible])

  useEffect(() => {
    if (!sourceVisible || !taskId || !probePagePlan.length) return
    const controller = new AbortController()
    let cancelled = false

    const loadPage = async (entry: PagePlanEntry) => {
      const data = await apiJson<PageContent>(
        `${PDF_API}/source/${encodeURIComponent(taskId)}/page/${encodeURIComponent(entry.pageNumber)}?focus_table=${encodeURIComponent(String(entry.focusTableIndex || ''))}`,
        { signal: controller.signal },
      )
      if (!cancelled) {
        setPageContentCache((prev) => (prev[entry.pageNumber] ? prev : { ...prev, [entry.pageNumber]: data }))
      }
    }

    void Promise.all(probePagePlan.map((entry) => loadPage(entry).catch((error) => {
      if (!cancelled) console.warn('Failed to load page content', entry.pageNumber, error)
    })))

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [probePagePlan, sourceVisible, taskId])

  useEffect(() => {
    const key = focusedBlock?.key
    const root = markdownPaneRef.current
    if (!root) return
    root.querySelectorAll('.pdf-page-block.is-trace-focused').forEach((item) => item.classList.remove('is-trace-focused'))
    if (!key) return
    const selector = `[data-focus-key="${cssAttrValue(key)}"]`
    window.requestAnimationFrame(() => {
      const markdownTarget = root.querySelector<HTMLElement>(selector)
      markdownTarget?.classList.add('is-trace-focused')
      markdownTarget?.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' })
      const pdfTarget = workbenchRef.current?.querySelector<HTMLElement>(selector)
      pdfTarget?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' })
    })
  }, [focusedBlock?.key, pagePlan])

  if (!sourceVisible || !srcTable) return null

  const corr = srcMeta?.correction || {}
  const excerpt = srcMeta?.excerpt || []
  const sArt = srcMeta?.artifacts || {}
  const currentTrace = workbenchTrace?.scopeKey === traceScopeKey ? workbenchTrace.trace : ctx?.selectedTrace || null

  const statusOpts = [
    ['unreviewed', '未复核'],
    ['correct', '确认无误'],
    ['needs_fix', '需要修正'],
    ['fixed', '已修正'],
    ['ignored', '忽略'],
  ] as const

  const changePage = (page: number) => {
    const next = Math.max(1, Math.min(pageCount || 1, page))
    setPdfCurPage(next)
    updatePdfViewer(next)
  }

  const focusMarkdownBlockElement = (element: HTMLElement | null) => {
    const root = markdownPaneRef.current
    root?.querySelectorAll('.pdf-page-block.is-trace-focused').forEach((item) => item.classList.remove('is-trace-focused'))
    element?.classList.add('is-trace-focused')
    element?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }

  const focusBlockByPayload = (payload: FocusedBlock) => {
    setFocusedBlock(payload)
    setWorkbenchTrace({
      scopeKey: traceScopeKey,
      trace: {
        pageNumber: payload.page,
        bbox: payload.bbox,
        source: payload.blockType || 'text_anchor',
        confidence: 'high',
      },
    })
    if (payload.page !== currentPage) changePage(payload.page)
  }

  const focusBlockFromElement = (block: HTMLElement) => {
    const bbox = validBbox(block.dataset.bbox)
    const page = pageNumber(block.dataset.pageNumber, currentPage)
    const blockId = block.dataset.blockId || ''
    if (!bbox.length || !page || !blockId) return false
    focusMarkdownBlockElement(block)
    focusBlockByPayload({
      key: blockFocusKey(page, blockId),
      blockId,
      blockType: String(block.dataset.blockType || 'text_anchor'),
      page,
      bbox,
    })
    return true
  }

  const focusBlockFromOverlay = (entry: PageOverlayEntry) => {
    const page = pageNumber(entry.pageNumber, currentPage)
    if (!entry.blockId || !entry.bbox.length) return
    focusBlockByPayload({
      key: blockFocusKey(page, entry.blockId),
      blockId: entry.blockId,
      blockType: entry.blockType || 'block',
      page,
      bbox: entry.bbox,
    })
  }

  const handleTableClickWithTrace = (event: MouseEvent<HTMLDivElement>) => {
    onTableClick(event)
    const trace = pdfCtx.current?.selectedTrace || null
    if (trace) {
      setFocusedBlock(null)
      setWorkbenchTrace({ scopeKey: traceScopeKey, trace })
    }
  }

  const handleWorkbenchReadingClick = (event: MouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement | null
    const tableTarget = target?.closest<HTMLElement>('[data-ptidx]')
    if (tableTarget?.dataset.ptidx) {
      onReadingClick(event)
      return
    }
    const block = target?.closest<HTMLElement>('.pdf-page-block[data-bbox][data-page-number]')
    if (!block) {
      onReadingClick(event)
      return
    }
    if (!focusBlockFromElement(block)) {
      onReadingClick(event)
      return
    }
    event.preventDefault()
    event.stopPropagation()
  }

  return (
    <div ref={workbenchRef} className="apple-card rounded-[24px] p-4 sm:p-6">
      <h3 className="mb-3 text-base font-semibold text-text">可视化溯源</h3>

      <PdfSourceSummary srcTable={srcTable} />
      <PdfMobileReviewTabs mobileTab={mobileTab} onChange={setMobileTab} />

      <PdfReviewComparePane>
        <PdfReviewPdfPane
          mobileTab={mobileTab}
          currentPage={currentPage}
          pageCount={pageCount}
          pdfZoom={pdfZoom}
          setPdfZoom={setPdfZoom}
          onPageChange={changePage}
        >
          {enhancedLoading && !enhancedArtifact ? (
            <div className="pdf-workbench-empty">
              <Loader2 className="h-5 w-5 animate-spin" />
              <span>正在加载跨页关系...</span>
            </div>
          ) : null}
          {enhancedError ? <div className="pdf-workbench-empty">{enhancedError}</div> : null}
          {pagePlan.length ? (
            pagePlan.map((entry, index) => {
              const relationEntries = [backwardRelation, forwardRelation]
                .filter((relation): relation is TableRelationCandidate => Boolean(relation))
                .map((relation) => {
                  const mode = relationModeForPage(entry.pageNumber, relation)
                  return mode ? { relation, mode } : null
                })
                .filter((item): item is { relation: TableRelationCandidate; mode: 'from' | 'to' } => Boolean(item))
              const nextEntry = pagePlan[index + 1]
              const bridgeRelation =
                [backwardRelation, forwardRelation].find((relation): relation is TableRelationCandidate =>
                  Boolean(relation && relation.pageNumbers[0] === entry.pageNumber && nextEntry && relation.pageNumbers[1] === nextEntry.pageNumber),
                ) || null
              const overlayTables =
                entry.pageNumber === sourcePage && srcTable
                  ? currentTables.some((table) => Number(table.table_index || 0) === Number(srcTable.table_index || 0))
                    ? currentTables
                    : currentTables.concat([
                        {
                          table_index: srcTable.table_index,
                          line: srcTable.line,
                          rows: srcTable.rows,
                          cells: srcTable.cells,
                          pdf_page_number: srcTable.pdf_page_number,
                          printed_page_number: srcTable.pdf_page_source === 'markdown_marker_inferred' ? undefined : srcMeta?.pdfPageImage?.printed_page_number,
                          heading: srcTable.heading,
                          unit: srcTable.unit,
                          matched_financial_names: srcTable.matched_financial_names,
                          bbox: srcTable.bbox,
                          source_image_path: srcTable.source_image_path,
                          source: 'source_table',
                          confidence: 'high',
                        } as EnhancedTable,
                      ])
                  : currentTables
              const pageBlocks = pageContentBlocks(pageContentCache[entry.pageNumber])
              const pageOverlays = buildPagePreviewOverlays({
                pageNumberValue: entry.pageNumber,
                currentPage,
                focusTableIndex: Number(entry.focusTableIndex || 0),
                tables: overlayTables,
                blocks: pageBlocks,
                currentTrace,
                focusedBlockKey,
              })

              return (
                <div key={entry.pageNumber} className="pdf-pdf-page-stack-item">
                  <PdfPagePreviewCard
                    pageNumberValue={entry.pageNumber}
                    pageUrl={getPdfUrl(entry.pageNumber)}
                    pageExtent={pageExtentForPage(entry.pageNumber, currentTables, pageContentCache[entry.pageNumber], currentExtent, currentPage)}
                    overlays={pageOverlays}
                    relationEntries={relationEntries}
                    onReadingClick={handleWorkbenchReadingClick}
                    onBlockFocus={focusBlockFromOverlay}
                  />
                  {bridgeRelation ? (
                    <PageMergeBridge
                      relation={bridgeRelation}
                      onClick={() => changePage(bridgeRelation.pageNumbers[1])}
                    />
                  ) : index < pagePlan.length - 1 ? null : null}
                </div>
              )
            })
          ) : (
            <div className="pdf-workbench-empty">
              <FileText className="h-5 w-5" />
              <span>未识别 PDF 页码，无法展示原页。</span>
            </div>
          )}
        </PdfReviewPdfPane>

        <PdfReviewReadingPane
          mobileTab={mobileTab}
          readingMode={readingMode}
          switchReadingMode={switchReadingMode}
          currentPage={currentPage}
          readingHtml={readingHtml}
          editTableRef={editTableRef}
          markdownPaneRef={markdownPaneRef}
          onTableClick={handleTableClickWithTrace}
          onTableFocus={onTableFocus}
          onTableInput={onTableInput}
          onReadingClick={handleWorkbenchReadingClick}
        >
          {pagePlan.map((entry) => {
            const pageData = pageContentCache[entry.pageNumber]
            if (!pageData) {
              if (entry.pageNumber === currentPage && readingHtml) {
                return (
                  <article key={entry.pageNumber} className="pdf-md-block">
                    <span className="pdf-md-block-meta">p{entry.pageNumber} · 加载中</span>
                    <div className="pdf-md-html" dangerouslySetInnerHTML={{ __html: renderFallbackPageHtml(readingHtml, entry.pageNumber, currentPage) }} />
                  </article>
                )
              }
              return (
                <article key={entry.pageNumber} className="pdf-md-block pdf-page-block-muted">
                  <span className="pdf-md-block-meta">p{entry.pageNumber} · 加载中</span>
                  <div className="pdf-md-html" style={{ color: '#64748b' }}>
                    正在加载页面内容...
                  </div>
                </article>
              )
            }
            return (
              <article key={entry.pageNumber} className={`pdf-md-block ${pageNumber(pageData.page_number, entry.pageNumber) === currentPage ? 'is-focused' : ''}`}>
                <span className="pdf-md-block-meta">p{entry.pageNumber} · 页面块</span>
                <div className="pdf-md-html" dangerouslySetInnerHTML={{ __html: renderPageContentHtml(pageData) }} />
              </article>
            )
          })}
        </PdfReviewReadingPane>
      </PdfReviewComparePane>

      <PdfReviewCorrectionPane
        corr={corr}
        srcTable={srcTable}
        statusOptions={statusOpts}
        corrStatusRef={corrStatusRef}
        corrTextRef={corrTextRef}
        corrNoteRef={corrNoteRef}
        saveCorrection={saveCorrection}
      />
      <PdfMarkdownContextPane excerpt={excerpt} />
      <PdfArtifactPane artifacts={sArt} />
    </div>
  )
}
