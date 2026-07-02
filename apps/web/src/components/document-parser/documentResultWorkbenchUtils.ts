import type { CSSProperties } from 'react'
import DOMPurify from 'dompurify'
import type {
  DocumentBlock,
  DocumentLayoutPage,
  DocumentSourceMapPayload,
  DocumentTable,
  DocumentTableRelation,
} from '@/lib/documentTypes'

export type SourceMapEntry = NonNullable<DocumentSourceMapPayload['sources']>[number]
export type FocusTarget = { kind: 'page' | 'block' | 'table' | 'figure'; id: string; page: number } | null

export type OverlayEntry = {
  id: string
  kind: 'block' | 'table' | 'figure'
  pageNumber: number
  bbox: number[]
  bboxUnit: string
  label: string
  detail: string
  sourceUrl?: string
  focusKeys: string[]
}

export type MarkdownBlock = {
  id: string
  pageNumber: number
  type: string
  title: string
  html: string
  textPreview: string
  focusKeys: string[]
}

export function statusLabel(status?: string) {
  return ({
    queued: '排队',
    uploaded: '已上传',
    detecting_type: '识别类型',
    running: '解析中',
    postprocessing: '后处理',
    completed: '完成',
    completed_with_warnings: '有警告',
    failed: '失败',
    cancelled: '已取消',
  } as Record<string, string>)[String(status || '')] || status || '未选择'
}

export function statusTone(status?: string) {
  const value = String(status || '').toLowerCase()
  if (value === 'completed') return 'done'
  if (value === 'completed_with_warnings') return 'warn'
  if (value === 'failed' || value === 'cancelled') return 'fail'
  return 'run'
}

export function workflowReady(status?: string) {
  return ['ready', 'stale', 'chunks_ready', 'completed'].includes(String(status || ''))
}

export function stringify(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2)
}

export function relationId(relation: DocumentTableRelation, index: number) {
  return relation.relation_id || relation.id || `relation-${index + 1}`
}

export function relationTables(relation: DocumentTableRelation) {
  const fragments = relationTableIds(relation)
  if (fragments?.length) return fragments.join(' -> ')
  return [relation.source_table_id || relation.table_id, relation.target_table_id || relation.next_table_id]
    .filter(Boolean)
    .join(' -> ') || '未标注表格'
}

export function relationTableIds(relation: DocumentTableRelation) {
  const fragments = relation.fragment_table_ids?.filter(Boolean) || []
  if (fragments.length) return fragments
  return [relation.source_table_id || relation.table_id, relation.target_table_id || relation.next_table_id]
    .filter(Boolean) as string[]
}

export function relationConfidence(relation: DocumentTableRelation) {
  const value = relation.confidence ?? relation.merge_confidence
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '-'
}

export function relationFlowTone(relation: DocumentTableRelation) {
  const status = String(relation.review_status || relation.merge_status || relation.relation_type || '').toLowerCase()
  if (status.includes('reject') || status.includes('not_continuation')) return 'is-rejected'
  if (status.includes('accept') || status.includes('auto_merged') || status === 'continuation') return 'is-accepted'
  return 'is-candidate'
}

export function tableLabel(table?: DocumentTable, fallbackId = '') {
  return table?.title || table?.caption || fallbackId || '表格片段'
}

export function blockLabel(type?: string) {
  const value = String(type || '').toLowerCase()
  if (value.includes('table')) return '表'
  if (value.includes('title') || value.includes('heading')) return '题'
  if (value.includes('image') || value.includes('figure')) return '图'
  if (value.includes('formula') || value.includes('equation')) return '式'
  return '段'
}

export function focusKey(kind: NonNullable<FocusTarget>['kind'], id: string) {
  return id ? `${kind}:${id}` : ''
}

export function uniqueStrings(values: Array<string | undefined | null>) {
  return Array.from(new Set(values.filter(Boolean).map(String)))
}

export function hasFocusedKey(keys: string[], activeKeys: Set<string>) {
  return keys.some((key) => activeKeys.has(key))
}

export function cssAttrValue(value: string) {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
}

export function sourceEntriesFor(sourceMap: DocumentSourceMapPayload | null, match: (entry: SourceMapEntry) => boolean) {
  return (sourceMap?.sources || []).filter(match)
}

export function firstSourceUrl(sourceMap: DocumentSourceMapPayload | null, table: DocumentTable) {
  const tableId = table.table_id || ''
  const blockId = table.block_id || ''
  return sourceEntriesFor(sourceMap, (entry) => Boolean(
    (tableId && entry.table_id === tableId) ||
    (blockId && entry.block_id === blockId),
  ))[0]?.open_source_url
}

export function pageNumber(value: unknown, fallback = 1) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback
}

export function validBbox(value: unknown): number[] {
  if (!Array.isArray(value) || value.length !== 4) return []
  const bbox = value.map(Number)
  if (!bbox.every(Number.isFinite)) return []
  if (bbox[2] <= bbox[0] || bbox[3] <= bbox[1]) return []
  return bbox
}

function escapeHtml(value: unknown) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function renderInlineMarkdown(value: string) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
}

function splitTableRow(line: string) {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

function isMarkdownTableLine(line: string) {
  const trimmed = line.trim()
  return trimmed.startsWith('|') && trimmed.endsWith('|') && trimmed.includes('|')
}

function isMarkdownTableSeparator(line: string) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line)
}

function renderMarkdownTable(lines: string[]) {
  const rows = lines.filter((line) => !isMarkdownTableSeparator(line)).map(splitTableRow)
  if (!rows.length) return ''
  const [head, ...body] = rows
  return [
    '<table>',
    '<thead><tr>',
    ...head.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`),
    '</tr></thead>',
    body.length ? '<tbody>' : '',
    ...body.map((row) => `<tr>${row.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join('')}</tr>`),
    body.length ? '</tbody>' : '',
    '</table>',
  ].join('')
}

function sanitizeMarkdownHtml(html: string) {
  const sanitize = (DOMPurify as { sanitize?: (value: string, options: Record<string, unknown>) => string }).sanitize
  if (typeof sanitize !== 'function') return escapeHtml(html)
  return sanitize(html, {
    ALLOWED_TAGS: [
      'section',
      'article',
      'div',
      'span',
      'p',
      'br',
      'strong',
      'b',
      'em',
      'i',
      'u',
      'small',
      'sup',
      'sub',
      'h1',
      'h2',
      'h3',
      'h4',
      'h5',
      'h6',
      'blockquote',
      'pre',
      'code',
      'table',
      'thead',
      'tbody',
      'tfoot',
      'tr',
      'th',
      'td',
      'caption',
      'colgroup',
      'col',
      'ul',
      'ol',
      'li',
      'details',
      'summary',
      'figure',
      'figcaption',
    ],
    ALLOWED_ATTR: ['class', 'data-block-id', 'data-page', 'rowspan', 'colspan', 'data-bbox', 'data-cell-bbox', 'bbox'],
  })
}

function renderMarkdownToHtml(markdown: string) {
  const text = String(markdown || '').replace(/\r\n?/g, '\n')
  if (!text.trim()) return '<p class="doc-md-empty">空块</p>'
  const lines = text.split('\n')
  const out: string[] = []
  let listType: 'ul' | 'ol' | '' = ''
  let codeFence = false
  let codeLines: string[] = []

  const closeList = () => {
    if (!listType) return
    out.push(`</${listType}>`)
    listType = ''
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    const trimmed = line.trim()

    if (trimmed.startsWith('```')) {
      if (codeFence) {
        out.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
        codeLines = []
        codeFence = false
      } else {
        closeList()
        codeFence = true
      }
      continue
    }
    if (codeFence) {
      codeLines.push(line)
      continue
    }

    if (!trimmed) {
      closeList()
      continue
    }

    if (/^\[PDF_PAGE:\s*\d+\]/i.test(trimmed)) {
      closeList()
      out.push(`<div class="doc-md-page-marker">${escapeHtml(trimmed.replace(/^\[|\]$/g, ''))}</div>`)
      continue
    }

    const imageMatch = trimmed.match(/^!\[([^\]]*)\]\(([^)]+)\)/)
    if (imageMatch) {
      closeList()
      out.push(`<figure class="doc-md-image-ref"><figcaption>${escapeHtml(imageMatch[1] || '图片')} · ${escapeHtml(imageMatch[2])}</figcaption></figure>`)
      continue
    }

    if (/^<(table|thead|tbody|tfoot|tr|td|th|details|summary|\/table|\/thead|\/tbody|\/tfoot|\/tr|\/td|\/th|\/details|\/summary)\b/i.test(trimmed)) {
      closeList()
      out.push(line)
      continue
    }

    if (isMarkdownTableLine(line)) {
      closeList()
      const tableLines = [line]
      while (index + 1 < lines.length && isMarkdownTableLine(lines[index + 1])) {
        index += 1
        tableLines.push(lines[index])
      }
      out.push(renderMarkdownTable(tableLines))
      continue
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/)
    if (heading) {
      closeList()
      const level = Math.min(6, heading[1].length)
      out.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`)
      continue
    }

    const ordered = trimmed.match(/^\d+[.)]\s+(.+)$/)
    if (ordered) {
      if (listType !== 'ol') {
        closeList()
        listType = 'ol'
        out.push('<ol>')
      }
      out.push(`<li>${renderInlineMarkdown(ordered[1])}</li>`)
      continue
    }

    const unordered = trimmed.match(/^[-*+]\s+(.+)$/)
    if (unordered) {
      if (listType !== 'ul') {
        closeList()
        listType = 'ul'
        out.push('<ul>')
      }
      out.push(`<li>${renderInlineMarkdown(unordered[1])}</li>`)
      continue
    }

    closeList()
    out.push(`<p>${renderInlineMarkdown(line)}</p>`)
  }

  closeList()
  if (codeFence) out.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
  return sanitizeMarkdownHtml(out.join('\n'))
}

function splitMarkdownByPage(markdown: string): MarkdownBlock[] {
  const lines = String(markdown || '').replace(/\r\n?/g, '\n').split('\n')
  const blocks: MarkdownBlock[] = []
  let currentPage = 1
  let chunk: string[] = []

  const flush = () => {
    const markdownText = chunk.join('\n').trim()
    if (!markdownText) return
    blocks.push({
      id: `md-page-${currentPage}`,
      pageNumber: currentPage,
      type: 'markdown_page',
      title: `PDF p${currentPage}`,
      html: renderMarkdownToHtml(markdownText),
      textPreview: markdownText.replace(/\s+/g, ' ').slice(0, 160),
      focusKeys: uniqueStrings([focusKey('page', `page-${currentPage}`)]),
    })
  }

  for (const line of lines) {
    const match = line.match(/^\[PDF_PAGE:\s*(\d+)\]/i)
    if (match) {
      flush()
      currentPage = pageNumber(match[1], currentPage)
      chunk = []
      continue
    }
    chunk.push(line)
  }
  flush()
  return blocks
}

export function buildMarkdownBlocks(blocks: DocumentBlock[], markdown: string, tableByBlockId: Map<string, DocumentTable>): MarkdownBlock[] {
  if (!blocks.length) return splitMarkdownByPage(markdown)
  return blocks.map((block, index) => {
    const blockId = block.block_id || `md-block-${index + 1}`
    const table = tableByBlockId.get(blockId)
    const tableId = table?.table_id || ''
    const markdownText = String(block.markdown || block.text || '')
    return {
      id: blockId,
      pageNumber: pageNumber(block.page_number),
      type: block.type || 'block',
      title: `${tableId || blockId} · ${block.type || 'block'}`,
      html: renderMarkdownToHtml(markdownText),
      textPreview: markdownText.replace(/\s+/g, ' ').slice(0, 160),
      focusKeys: uniqueStrings([
        focusKey('block', blockId),
        tableId ? focusKey('table', tableId) : '',
      ]),
    }
  })
}

export function relationPages(relation: DocumentTableRelation, tableById: Map<string, DocumentTable>) {
  const explicit = (relation.page_numbers || []).map((item) => pageNumber(item)).filter(Boolean)
  if (explicit.length) return Array.from(new Set(explicit)).sort((a, b) => a - b)
  const visual = [relation.visual_connector?.from_page, relation.visual_connector?.to_page]
    .map((item) => pageNumber(item, 0))
    .filter(Boolean)
  if (visual.length) return Array.from(new Set(visual)).sort((a, b) => a - b)
  return Array.from(new Set(relationTableIds(relation).map((tableId) => pageNumber(tableById.get(tableId)?.page_number, 0)).filter(Boolean))).sort((a, b) => a - b)
}

export function isPreviewCrossPageTableRelation(relation: DocumentTableRelation, tableById: Map<string, DocumentTable>) {
  const reviewStatus = String(relation.review_status || '').toLowerCase()
  if (reviewStatus === 'rejected') return false
  const relationType = String(relation.relation_type || '').toLowerCase()
  const mergeStatus = String(relation.merge_status || '').toLowerCase()
  const pages = relationPages(relation, tableById)
  const isAdjacentCrossPage = pages.length === 2 && pages[1] === pages[0] + 1
  if (!isAdjacentCrossPage) return false
  if (reviewStatus === 'accepted') return true
  if (relationType.includes('continuation')) return true
  return mergeStatus === 'auto_merged' || mergeStatus === 'candidate'
}

function coordinateExtent(bboxes: number[][], imageSize: { width: number; height: number } | null) {
  const maxX = Math.max(1, ...bboxes.map((bbox) => bbox[2]))
  const maxY = Math.max(1, ...bboxes.map((bbox) => bbox[3]))
  if (maxX <= 1 && maxY <= 1) return { width: 1, height: 1 }
  if (!imageSize?.width || !imageSize.height) return { width: maxX * 1.08, height: maxY * 1.08 }
  if (maxX > imageSize.width * 0.72 && maxY > imageSize.height * 0.72) return imageSize
  const ratio = imageSize.width / imageSize.height
  const width = Math.max(maxX * 1.04, maxY * ratio * 1.04)
  const height = Math.max(maxY * 1.04, width / ratio)
  return { width, height }
}

function pdfPointExtent(page: DocumentLayoutPage | undefined, bboxes: number[][], imageSize: { width: number; height: number } | null) {
  const width = Number(page?.width || page?.page_size?.[0] || 0)
  const height = Number(page?.height || page?.page_size?.[1] || 0)
  if (Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0) {
    return { width, height }
  }
  return coordinateExtent(bboxes, imageSize)
}

function normalized1000Extent() {
  return { width: 1000, height: 1000 }
}

function looksLikeLegacyMineruNormalized1000(
  bbox: number[],
  page: DocumentLayoutPage | undefined,
  imageSize: { width: number; height: number } | null,
) {
  const maxCoord = Math.max(...bbox)
  if (maxCoord <= 0 || maxCoord > 1050) return false
  const pageWidth = Number(page?.width || page?.page_size?.[0] || 0)
  const pageHeight = Number(page?.height || page?.page_size?.[1] || 0)
  if (!pageWidth || !pageHeight || !imageSize?.width || !imageSize.height) return false
  const xScale = imageSize.width / pageWidth
  const yScale = imageSize.height / pageHeight
  return Math.abs(xScale - yScale) < 0.08 && xScale > 1.2
}

export function bboxExtent(
  bbox: number[],
  bboxUnit: string,
  page: DocumentLayoutPage | undefined,
  imageSize: { width: number; height: number } | null,
) {
  const unit = String(bboxUnit || '').toLowerCase()
  if (unit === 'normalized_1000' || unit === 'relative_1000') return normalized1000Extent()
  if (unit === 'pdf_point' || unit === 'pdf_points') return pdfPointExtent(page, [bbox], imageSize)
  if (unit === 'pixel' && !looksLikeLegacyMineruNormalized1000(bbox, page, imageSize)) {
    return imageSize?.width && imageSize.height ? imageSize : coordinateExtent([bbox], imageSize)
  }
  if (unit === 'pixel' || looksLikeLegacyMineruNormalized1000(bbox, page, imageSize)) return normalized1000Extent()
  return pdfPointExtent(page, [bbox], imageSize)
}

function clampPercent(value: number) {
  if (!Number.isFinite(value)) return 0
  return Math.max(0, Math.min(100, value))
}

export function bboxStyle(bbox: number[], extent: { width: number; height: number }): CSSProperties {
  const left = clampPercent((bbox[0] / extent.width) * 100)
  const top = clampPercent((bbox[1] / extent.height) * 100)
  const right = clampPercent((bbox[2] / extent.width) * 100)
  const bottom = clampPercent((bbox[3] / extent.height) * 100)
  return {
    left: `${left}%`,
    top: `${top}%`,
    width: `${Math.max(0.8, right - left)}%`,
    height: `${Math.max(0.8, bottom - top)}%`,
  }
}

export function mergeStemStyle(bbox: number[], extent: { width: number; height: number }, mode: 'from' | 'to'): CSSProperties {
  const x = clampPercent((((bbox[0] + bbox[2]) / 2) / extent.width) * 100)
  if (mode === 'from') {
    const top = clampPercent((bbox[3] / extent.height) * 100)
    return { left: `${x}%`, top: `${top}%`, height: `${Math.max(5, 100 - top)}%` }
  }
  const height = clampPercent((bbox[1] / extent.height) * 100)
  return { left: `${x}%`, top: 0, height: `${Math.max(5, height)}%` }
}

export function relationLabel(relation: DocumentTableRelation) {
  const confidence = relationConfidence(relation)
  const status = relation.merge_status || relation.relation_type || 'continuation'
  const label = relationFlowTone(relation) === 'is-candidate' ? '候选' : '合并'
  return `${label} · ${status}${confidence !== '-' ? ` · ${confidence}` : ''}`
}
