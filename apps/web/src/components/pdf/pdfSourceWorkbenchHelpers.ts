import type { BboxExtent, PageBlock, PageContent, SelectedTrace, SourceMeta, SourceTable } from '../../lib/pdfTypes'
import { parseBbox as parsePdfBbox } from '../../lib/pdfSanitize.ts'
import { blockFocusKey, type EnhancedTable, type PageOverlayEntry, type TableRelationCandidate } from './pdfSourceWorkbenchTypes.ts'

export type EnhancedArtifact = {
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

export type TableRelationsArtifact = {
  schema_version?: string
  ruleset_version?: string
  task_id?: string
  relations?: TableRelationArtifactEntry[]
}

export function pageNumber(value: unknown, fallback = 1) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback
}

export function validBbox(value: unknown): number[] {
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

export function cssAttrValue(value: string) {
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

export function pageTablesForPage(tables: EnhancedTable[], page: number) {
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

export function mergePhysicalTables(
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

export function firstTableOnPage(table: EnhancedTable, pageTables: EnhancedTable[]) {
  const bbox = validBbox(table.bbox)
  if (!bbox.length) return false
  const sameTables = pageTables.filter((item) => samePhysicalTable(table, item))
  const firstY = Math.min(...pageTables.map((item) => validBbox(item.bbox)[1] || bbox[1]).filter(Number.isFinite), bbox[1])
  return Math.min(...sameTables.map((item) => validBbox(item.bbox)[1] || bbox[1]).filter(Number.isFinite), bbox[1]) <= firstY + 2
}

export function lastTableOnPage(table: EnhancedTable, pageTables: EnhancedTable[]) {
  const bbox = validBbox(table.bbox)
  if (!bbox.length) return false
  const sameTables = pageTables.filter((item) => samePhysicalTable(table, item))
  const lastY = Math.max(...pageTables.map((item) => validBbox(item.bbox)[3] || bbox[3]).filter(Number.isFinite), bbox[3])
  return Math.max(...sameTables.map((item) => validBbox(item.bbox)[3] || bbox[3]).filter(Number.isFinite), bbox[3]) >= lastY - 2
}

export function pageExtentForPage(
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

export function pageContentBlocks(pageContent: PageContent | undefined) {
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

export function findForwardRelation(fromTable: EnhancedTable, tables: EnhancedTable[], pageContentCache: Record<number, PageContent>) {
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

export function findBackwardRelation(currentTable: EnhancedTable, tables: EnhancedTable[], pageContentCache: Record<number, PageContent>) {
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

export function chooseFocusTableIndex(page: number, sourcePage: number, sourceTableIndex: number, tables: EnhancedTable[]) {
  if (page === sourcePage && sourceTableIndex) return sourceTableIndex
  return pageTablesForPage(tables, page)[0]?.table_index || sourceTableIndex || 0
}

export function relationModeForPage(entryPage: number, relation: TableRelationCandidate): 'from' | 'to' | null {
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

export function relationsFromArtifactForPage(
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

export function deriveTaskId(urls: string[]) {
  for (const url of urls) {
    if (!url) continue
    const match = url.match(/\/api\/(?:pdf\/)?(?:pdf_page|artifact|source)\/([^/]+)/)
    if (match?.[1]) return decodeURIComponent(match[1])
  }
  return ''
}

export function buildPagePreviewOverlays({
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

export function renderFallbackPageHtml(html: string, pageNumberValue: number, currentPage: number) {
  if (!html) return ''
  return html.includes('pdf-page-reading-view')
    ? html
    : `<div class="pdf-page-reading-view"><div class="pdf-page-reading-summary"><div><strong>PDF 第 ${pageNumberValue || currentPage}</strong><span>0 个解析块 / 0 张表</span></div></div>${html}</div>`
}
