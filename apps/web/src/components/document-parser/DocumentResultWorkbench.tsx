import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import DOMPurify from 'dompurify'
import { Archive, Brain, ChevronLeft, ChevronRight, Database, Download, ExternalLink, Eye, FileJson, FileText, Image, ListChecks, Loader2, RefreshCw, Table2 } from 'lucide-react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/page'
import {
  documentArtifactUrl,
  documentDownloadUrl,
  documentSourcePageImageUrl,
  openDocumentResource,
} from '@/features/document-parser/api'
import { apiBlob } from '@/lib/apiClient'
import type {
  DocumentArtifactsMap,
  DocumentBlock,
  DocumentBlocksPayload,
  DocumentExtractionTemplate,
  DocumentFigure,
  DocumentFiguresPayload,
  DocumentLayoutBlocksPayload,
  DocumentLayoutPage,
  DocumentQualityReport,
  DocumentResult,
  DocumentSourceMapPayload,
  DocumentTable,
  DocumentTableRelation,
  DocumentTableRelationsPayload,
  DocumentTablesPayload,
  DocumentTaskItem,
  DocumentWikiImportResult,
  DocumentWorkflowStatus,
} from '@/lib/documentTypes'
import { workflowStateClass, workflowStateLabel } from '@/lib/pdfFormatting'

type SourceMapEntry = NonNullable<DocumentSourceMapPayload['sources']>[number]
type FocusTarget = { kind: 'page' | 'block' | 'table' | 'figure'; id: string; page: number } | null

type OverlayEntry = {
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

type MarkdownBlock = {
  id: string
  pageNumber: number
  type: string
  title: string
  html: string
  textPreview: string
  focusKeys: string[]
}

function statusLabel(status?: string) {
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

function statusTone(status?: string) {
  const value = String(status || '').toLowerCase()
  if (value === 'completed') return 'done'
  if (value === 'completed_with_warnings') return 'warn'
  if (value === 'failed' || value === 'cancelled') return 'fail'
  return 'run'
}

function workflowReady(status?: string) {
  return ['ready', 'stale', 'chunks_ready', 'completed'].includes(String(status || ''))
}

function stringify(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2)
}

function relationId(relation: DocumentTableRelation, index: number) {
  return relation.relation_id || relation.id || `relation-${index + 1}`
}

function relationTables(relation: DocumentTableRelation) {
  const fragments = relationTableIds(relation)
  if (fragments?.length) return fragments.join(' -> ')
  return [relation.source_table_id || relation.table_id, relation.target_table_id || relation.next_table_id]
    .filter(Boolean)
    .join(' -> ') || '未标注表格'
}

function relationTableIds(relation: DocumentTableRelation) {
  const fragments = relation.fragment_table_ids?.filter(Boolean) || []
  if (fragments.length) return fragments
  return [relation.source_table_id || relation.table_id, relation.target_table_id || relation.next_table_id]
    .filter(Boolean) as string[]
}

function relationConfidence(relation: DocumentTableRelation) {
  const value = relation.confidence ?? relation.merge_confidence
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '-'
}

function relationFlowTone(relation: DocumentTableRelation) {
  const status = String(relation.review_status || relation.merge_status || relation.relation_type || '').toLowerCase()
  if (status.includes('reject') || status.includes('not_continuation')) return 'is-rejected'
  if (status.includes('accept') || status.includes('auto_merged') || status === 'continuation') return 'is-accepted'
  return 'is-candidate'
}

function tableLabel(table?: DocumentTable, fallbackId = '') {
  return table?.title || table?.caption || fallbackId || '表格片段'
}

function blockLabel(type?: string) {
  const value = String(type || '').toLowerCase()
  if (value.includes('table')) return '表'
  if (value.includes('title') || value.includes('heading')) return '题'
  if (value.includes('image') || value.includes('figure')) return '图'
  if (value.includes('formula') || value.includes('equation')) return '式'
  return '段'
}

function focusKey(kind: NonNullable<FocusTarget>['kind'], id: string) {
  return id ? `${kind}:${id}` : ''
}

function uniqueStrings(values: Array<string | undefined | null>) {
  return Array.from(new Set(values.filter(Boolean).map(String)))
}

function hasFocusedKey(keys: string[], activeKeys: Set<string>) {
  return keys.some((key) => activeKeys.has(key))
}

function cssAttrValue(value: string) {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
}

function sourceEntriesFor(sourceMap: DocumentSourceMapPayload | null, match: (entry: SourceMapEntry) => boolean) {
  return (sourceMap?.sources || []).filter(match)
}

function firstSourceUrl(sourceMap: DocumentSourceMapPayload | null, table: DocumentTable) {
  const tableId = table.table_id || ''
  const blockId = table.block_id || ''
  return sourceEntriesFor(sourceMap, (entry) => Boolean(
    (tableId && entry.table_id === tableId) ||
    (blockId && entry.block_id === blockId),
  ))[0]?.open_source_url
}

function pageNumber(value: unknown, fallback = 1) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback
}

function validBbox(value: unknown): number[] {
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
  return DOMPurify.sanitize(html, {
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

function buildMarkdownBlocks(blocks: DocumentBlock[], markdown: string, tableByBlockId: Map<string, DocumentTable>): MarkdownBlock[] {
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

function relationPages(relation: DocumentTableRelation, tableById: Map<string, DocumentTable>) {
  const explicit = (relation.page_numbers || []).map((item) => pageNumber(item)).filter(Boolean)
  if (explicit.length) return Array.from(new Set(explicit)).sort((a, b) => a - b)
  const visual = [relation.visual_connector?.from_page, relation.visual_connector?.to_page]
    .map((item) => pageNumber(item, 0))
    .filter(Boolean)
  if (visual.length) return Array.from(new Set(visual)).sort((a, b) => a - b)
  return Array.from(new Set(relationTableIds(relation).map((tableId) => pageNumber(tableById.get(tableId)?.page_number, 0)).filter(Boolean))).sort((a, b) => a - b)
}

function isPreviewCrossPageTableRelation(relation: DocumentTableRelation, tableById: Map<string, DocumentTable>) {
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

function bboxExtent(
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

function bboxStyle(bbox: number[], extent: { width: number; height: number }): CSSProperties {
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

function mergeStemStyle(bbox: number[], extent: { width: number; height: number }, mode: 'from' | 'to'): CSSProperties {
  const x = clampPercent((((bbox[0] + bbox[2]) / 2) / extent.width) * 100)
  if (mode === 'from') {
    const top = clampPercent((bbox[3] / extent.height) * 100)
    return { left: `${x}%`, top: `${top}%`, height: `${Math.max(5, 100 - top)}%` }
  }
  const height = clampPercent((bbox[1] / extent.height) * 100)
  return { left: `${x}%`, top: 0, height: `${Math.max(5, height)}%` }
}

function relationLabel(relation: DocumentTableRelation) {
  const confidence = relationConfidence(relation)
  const status = relation.merge_status || relation.relation_type || 'continuation'
  const label = relationFlowTone(relation) === 'is-candidate' ? '候选' : '合并'
  return `${label} · ${status}${confidence !== '-' ? ` · ${confidence}` : ''}`
}

function MergePageBridge({
  relation,
  onClick,
}: {
  relation: DocumentTableRelation
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className={`doc-page-merge-bridge ${relationFlowTone(relation)}`}
      title={relationLabel(relation)}
      onClick={onClick}
    >
      <span>合并</span>
    </button>
  )
}

function AuthenticatedImage({
  src,
  alt,
  className,
  onLoadSize,
}: {
  src: string
  alt: string
  className?: string
  onLoadSize?: (size: { width: number; height: number }) => void
}) {
  const [objectUrl, setObjectUrl] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    let localUrl = ''
    queueMicrotask(() => {
      if (cancelled) return
      setObjectUrl('')
      setError('')
    })
    if (!src) {
      return () => {
        cancelled = true
      }
    }

    async function load() {
      try {
        const blob = await apiBlob(src)
        if (cancelled) return
        localUrl = window.URL.createObjectURL(blob)
        setObjectUrl(localUrl)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : '图片加载失败')
      }
    }

    void load()
    return () => {
      cancelled = true
      if (localUrl) window.URL.revokeObjectURL(localUrl)
    }
  }, [src])

  if (error) return <div className="doc-auth-image-state">页图暂不可用：{error}</div>
  if (!objectUrl) return <div className="doc-auth-image-state"><Loader2 className="h-4 w-4 animate-spin" />加载页图...</div>
  return (
    <img
      src={objectUrl}
      alt={alt}
      className={className}
      onLoad={(event) => onLoadSize?.({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
    />
  )
}

function PdfPagePreview({
  taskId,
  pageNumberValue,
  page,
  overlays,
  relations,
  tableById,
  activeFocusKeys,
  onFocus,
  onOpenResource,
}: {
  taskId: string
  pageNumberValue: number
  page?: DocumentLayoutPage
  overlays: OverlayEntry[]
  relations: DocumentTableRelation[]
  tableById: Map<string, DocumentTable>
  activeFocusKeys: Set<string>
  onFocus: (focus: FocusTarget) => void
  onOpenResource: (url: string, filename?: string) => void
}) {
  const [imageSize, setImageSize] = useState<{ width: number; height: number } | null>(null)
  const pageSrc = documentSourcePageImageUrl(taskId, pageNumberValue)

  return (
    <article className="doc-pdf-page-card">
      <div className="doc-pdf-page-title">
        <span>PDF p{pageNumberValue}</span>
        <Button
          type="button"
          variant="secondary"
          size="xs"
          leftIcon={<ExternalLink className="h-3 w-3" />}
          onClick={() => onOpenResource(pageSrc, `page-${pageNumberValue}.png`)}
        >
          打开页图
        </Button>
      </div>
      <div className="doc-pdf-page-canvas">
        <AuthenticatedImage
          src={pageSrc}
          alt={`PDF page ${pageNumberValue}`}
          className="doc-pdf-page-image"
          onLoadSize={setImageSize}
        />
        <div className="doc-pdf-overlay-layer" aria-hidden={!overlays.length && !relations.length}>
          {overlays.map((entry) => {
            const isFocused = hasFocusedKey(entry.focusKeys, activeFocusKeys)
            const extent = bboxExtent(entry.bbox, entry.bboxUnit, page, imageSize)
            return (
              <button
                type="button"
                key={`${entry.kind}-${entry.id}`}
                className={`doc-pdf-bbox is-${entry.kind} ${isFocused ? 'is-focused' : ''}`}
                style={bboxStyle(entry.bbox, extent)}
                title={entry.detail}
                aria-label={`定位 ${entry.detail}`}
                data-focus-keys={entry.focusKeys.join(' ')}
                onClick={() => onFocus({ kind: entry.kind, id: entry.id, page: entry.pageNumber })}
              >
                <span>{entry.label}</span>
              </button>
            )
          })}
          {relations.map((relation, index) => {
            const tableIds = relationTableIds(relation)
            const fromTable = tableById.get(tableIds[0] || '')
            const toTable = tableById.get(tableIds[1] || '')
            const isFrom = pageNumber(fromTable?.page_number, 0) === pageNumberValue
            const isTo = pageNumber(toTable?.page_number, 0) === pageNumberValue
            const table = isFrom ? fromTable : isTo ? toTable : undefined
            const bbox = validBbox(table?.bbox)
            if (!bbox.length) return null
            const extent = bboxExtent(bbox, table?.bbox_unit || '', page, imageSize)
            return (
              <button
                type="button"
                key={`${relationId(relation, index)}-${pageNumberValue}`}
                className={`doc-merge-stem ${isFrom ? 'is-from' : 'is-to'} ${relationFlowTone(relation)}`}
                style={mergeStemStyle(bbox, extent, isFrom ? 'from' : 'to')}
                title={`${relationTables(relation)} · 合并`}
                onClick={() => onFocus({ kind: 'table', id: table?.table_id || tableIds[0] || relationId(relation, index), page: pageNumberValue })}
              >
                <span>合并</span>
              </button>
            )
          })}
        </div>
      </div>
    </article>
  )
}

export function DocumentResultWorkbench({
  selectedTask,
  result,
  quality,
  blocks,
  layout,
  tables,
  tableRelations,
  figures,
  sourceMap,
  loading,
  extractionResult,
  extractionTemplates,
  workflowStatus,
  workflowBusy,
  wikiImportResult,
  onRunExtraction,
  onImportWiki,
  onImportDatabase,
  onBuildSemanticChunks,
  onRefreshWorkflow,
  onReviewTableRelation,
}: {
  selectedTask?: DocumentTaskItem
  result: DocumentResult | null
  quality: DocumentQualityReport | null
  blocks: DocumentBlocksPayload | null
  layout: DocumentLayoutBlocksPayload | null
  tables: DocumentTablesPayload | null
  tableRelations: DocumentTableRelationsPayload | null
  figures: DocumentFiguresPayload | null
  sourceMap: DocumentSourceMapPayload | null
  loading: boolean
  extractionResult: Record<string, unknown> | null
  extractionTemplates: DocumentExtractionTemplate[]
  workflowStatus: DocumentWorkflowStatus | null
  workflowBusy: string
  wikiImportResult: DocumentWikiImportResult | null
  onRunExtraction: (schemaText: string, instructions: string, templateId?: string) => Promise<void>
  onImportWiki: () => Promise<void>
  onImportDatabase: () => Promise<void>
  onBuildSemanticChunks: (milvus?: boolean) => Promise<void>
  onRefreshWorkflow: () => Promise<unknown>
  onReviewTableRelation: (relationId: string, reviewStatus: string, note?: string) => Promise<void>
}) {
  const pdfPaneRef = useRef<HTMLDivElement | null>(null)
  const markdownPaneRef = useRef<HTMLDivElement | null>(null)
  const [schemaText, setSchemaText] = useState('{\n  "type": "object",\n  "properties": {\n    "title": { "type": "string" }\n  }\n}')
  const [instructions, setInstructions] = useState('只从原文抽取，不确定则返回 null。')
  const [templateId, setTemplateId] = useState('')
  const [activePage, setActivePage] = useState(1)
  const [focused, setFocused] = useState<FocusTarget>(null)
  const [resourceError, setResourceError] = useState('')
  const [activeTab, setActiveTab] = useState('preview')
  const tabListRef = useRef<HTMLDivElement | null>(null)
  const scrollTabs = useCallback((direction: number) => {
    const el = tabListRef.current
    if (!el) return
    el.scrollBy({ left: direction * 160, behavior: 'smooth' })
  }, [])

  const taskId = selectedTask?.task_id || result?.manifest?.task_id || ''
  const sourceBlocks = useMemo(() => blocks?.blocks || [], [blocks?.blocks])
  const pageByNumber = useMemo(() => {
    const lookup = new Map<number, DocumentLayoutPage>()
    layout?.pages?.forEach((page) => {
      const pageNum = pageNumber(page.page_number, 0)
      if (pageNum) lookup.set(pageNum, page)
    })
    return lookup
  }, [layout?.pages])
  const artifactEntries = useMemo(() => Object.entries((result?.artifacts || {}) as DocumentArtifactsMap), [result?.artifacts])
  const physicalTables = useMemo(() => tables?.physical_tables || tables?.tables || [], [tables?.physical_tables, tables?.tables])
  const figureItems = useMemo(() => figures?.figures || [], [figures?.figures])
  const relationItems = useMemo(() => tableRelations?.relations || [], [tableRelations?.relations])
  const tableById = useMemo(() => {
    const lookup = new Map<string, DocumentTable>()
    physicalTables.forEach((table) => {
      if (table.table_id) lookup.set(table.table_id, table)
    })
    return lookup
  }, [physicalTables])
  const tableByBlockId = useMemo(() => {
    const lookup = new Map<string, DocumentTable>()
    physicalTables.forEach((table) => {
      if (table.block_id && !lookup.has(table.block_id)) lookup.set(table.block_id, table)
    })
    return lookup
  }, [physicalTables])
  const previewRelations = useMemo(() => {
    return relationItems.filter((relation) => isPreviewCrossPageTableRelation(relation, tableById))
  }, [relationItems, tableById])
  const sourceByBlockId = useMemo(() => {
    const lookup = new Map<string, SourceMapEntry>()
    sourceMap?.sources?.forEach((entry) => {
      if (entry.block_id && !lookup.has(entry.block_id)) lookup.set(entry.block_id, entry)
    })
    return lookup
  }, [sourceMap])
  const sourceByTableId = useMemo(() => {
    const lookup = new Map<string, SourceMapEntry>()
    sourceMap?.sources?.forEach((entry) => {
      if (entry.table_id && !lookup.has(entry.table_id)) lookup.set(entry.table_id, entry)
    })
    return lookup
  }, [sourceMap])
  const sourceByFigureId = useMemo(() => {
    const lookup = new Map<string, SourceMapEntry>()
    sourceMap?.sources?.forEach((entry) => {
      if (entry.image_id && !lookup.has(entry.image_id)) lookup.set(entry.image_id, entry)
    })
    return lookup
  }, [sourceMap])
  const workflowIsBusy = Boolean(workflowBusy)
  const wikiPackageReady = workflowReady(workflowStatus?.targets?.wiki?.status)
  const validationReport = extractionResult?.validation_report as Record<string, unknown> | undefined
  const evidenceMap = (extractionResult?.evidence_map || {}) as Record<string, Array<Record<string, unknown>>>
  const missingFields = Array.isArray(validationReport?.missing_fields) ? validationReport.missing_fields : []

  const markdownBlocks = useMemo(() => buildMarkdownBlocks(sourceBlocks, result?.markdown || '', tableByBlockId), [sourceBlocks, result?.markdown, tableByBlockId])
  const tableIdByBlockId = useMemo(() => {
    const lookup = new Map<string, string>()
    tableByBlockId.forEach((table, blockId) => {
      if (table.table_id) lookup.set(blockId, table.table_id)
    })
    return lookup
  }, [tableByBlockId])
  const blockIdByTableId = useMemo(() => {
    const lookup = new Map<string, string>()
    physicalTables.forEach((table) => {
      if (table.table_id && table.block_id && !lookup.has(table.table_id)) lookup.set(table.table_id, table.block_id)
    })
    return lookup
  }, [physicalTables])
  const relationsByTableId = useMemo(() => {
    const lookup = new Map<string, DocumentTableRelation[]>()
    previewRelations.forEach((relation) => {
      relationTableIds(relation).forEach((tableId) => {
        if (!tableId) return
        const existing = lookup.get(tableId) || []
        existing.push(relation)
        lookup.set(tableId, existing)
      })
    })
    return lookup
  }, [previewRelations])
  const activeFocusKeys = useMemo(() => {
    const keys = new Set<string>()
    if (!focused) return keys
    keys.add(focusKey(focused.kind, focused.id))
    if (focused.kind === 'block') {
      const tableId = tableIdByBlockId.get(focused.id)
      if (tableId) keys.add(focusKey('table', tableId))
    }
    if (focused.kind === 'table') {
      const blockId = blockIdByTableId.get(focused.id)
      if (blockId) keys.add(focusKey('block', blockId))
    }
    return keys
  }, [blockIdByTableId, focused, tableIdByBlockId])
  const focusedTableId = useMemo(() => {
    if (!focused) return ''
    if (focused.kind === 'table') return focused.id
    if (focused.kind === 'block') return tableIdByBlockId.get(focused.id) || ''
    return ''
  }, [focused, tableIdByBlockId])
  const focusedRelations = useMemo(() => {
    if (!focusedTableId) return []
    return relationsByTableId.get(focusedTableId) || []
  }, [focusedTableId, relationsByTableId])
  const activePageRelations = useMemo(() => {
    return previewRelations.filter((relation) => relationPages(relation, tableById).includes(activePage))
  }, [activePage, previewRelations, tableById])
  const visibleRelations = focusedRelations.length ? focusedRelations : activePageRelations

  const pageNumbers = useMemo(() => {
    const pages = new Set<number>()
    sourceBlocks.forEach((block) => pages.add(pageNumber(block.page_number)))
    pageByNumber.forEach((_page, page) => pages.add(page))
    physicalTables.forEach((table) => pages.add(pageNumber(table.page_number)))
    figureItems.forEach((figure) => pages.add(pageNumber(figure.page_number)))
    markdownBlocks.forEach((block) => pages.add(pageNumber(block.pageNumber)))
    const pageCount = pageNumber(quality?.page_count, 0)
    for (let page = 1; page <= pageCount; page += 1) pages.add(page)
    return Array.from(pages).filter(Boolean).sort((a, b) => a - b)
  }, [figureItems, markdownBlocks, pageByNumber, physicalTables, quality?.page_count, sourceBlocks])

  useEffect(() => {
    let cancelled = false
    const nextPage = pageNumbers[0] || 1
    queueMicrotask(() => {
      if (cancelled) return
      setActivePage(nextPage)
      setFocused(null)
      setResourceError('')
    })
    return () => {
      cancelled = true
    }
  }, [taskId, pageNumbers])

  const overlays = useMemo<OverlayEntry[]>(() => {
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
  }, [figureItems, physicalTables, sourceBlocks, sourceByBlockId, sourceByFigureId, sourceByTableId, tableIdByBlockId])

  const previewPages = useMemo(() => {
    const pages = new Set<number>([activePage])
    visibleRelations.forEach((relation) => {
      relationPages(relation, tableById).forEach((page) => pages.add(page))
    })
    return Array.from(pages).filter(Boolean).sort((a, b) => a - b).slice(0, 3)
  }, [activePage, tableById, visibleRelations])
  const previewMarkdownBlocks = useMemo(() => {
    const visible = new Set(previewPages)
    return markdownBlocks.filter((block) => visible.has(pageNumber(block.pageNumber)))
  }, [markdownBlocks, previewPages])

  useEffect(() => {
    if (!focused || !activeFocusKeys.size) return
    const selector = Array.from(activeFocusKeys)
      .map((key) => `[data-focus-keys~="${cssAttrValue(key)}"]`)
      .join(',')
    if (!selector) return

    window.requestAnimationFrame(() => {
      const pdfTarget = pdfPaneRef.current?.querySelector<HTMLElement>(selector)
      pdfTarget?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' })

      const markdownTarget = markdownPaneRef.current?.querySelector<HTMLElement>(selector)
      markdownTarget?.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' })
    })
  }, [activeFocusKeys, focused, previewPages])

  const openResource = useCallback(async (url: string, filename?: string) => {
    if (!url) return
    setResourceError('')
    try {
      await openDocumentResource(url, filename)
    } catch (err) {
      setResourceError(err instanceof Error ? err.message : '产物打开失败')
    }
  }, [])

  const applyTemplate = (nextTemplateId: string) => {
    setTemplateId(nextTemplateId)
    const template = extractionTemplates.find((item) => item.template_id === nextTemplateId)
    if (!template) return
    setSchemaText(JSON.stringify(template.schema || {}, null, 2))
    setInstructions(template.instructions || '只从原文抽取，不确定则返回 null。')
  }

  const focusTarget = (nextFocus: FocusTarget) => {
    setFocused(nextFocus)
    if (nextFocus?.page) setActivePage(nextFocus.page)
  }

  const selectPage = (page: number) => {
    setActivePage(page)
    setFocused({ kind: 'page', id: `page-${page}`, page })
  }

  if (!selectedTask) {
    return (
      <section className="doc-panel">
        <EmptyState
          icon={FileText}
          title="选择或上传一份文档"
          description="选择左侧任务或上传新文档后查看解析结果。"
          size="lg"
          className="min-h-[360px]"
        />
      </section>
    )
  }

  return (
    <section className="doc-panel min-w-0">
      <div className="doc-result-head">
        <div className="doc-result-title">
          <h2>{selectedTask.filename || selectedTask.task_id}</h2>
          <p>
            {selectedTask.document_kind || 'document'} · {selectedTask.parser_provider || 'provider pending'}
          </p>
        </div>
        <div className="doc-action-row">
          <span className={`doc-badge ${statusTone(selectedTask.status)}`}>{statusLabel(selectedTask.status)}</span>
          {taskId ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              leftIcon={<Download className="h-4 w-4" />}
              onClick={() => void openResource(documentDownloadUrl(taskId), `${taskId}.zip`)}
            >
              完整 ZIP
            </Button>
          ) : null}
        </div>
      </div>

      {resourceError ? <div className="doc-error mx-4 mt-4">{resourceError}</div> : null}

      {loading ? (
        <div className="doc-empty">
          <div>
            <Loader2 className="mx-auto mb-3 h-8 w-8 animate-spin text-primary" />
            <p>正在加载解析产物...</p>
          </div>
        </div>
      ) : null}

      {!loading && result ? (
        <Tabs value={activeTab} onValueChange={setActiveTab} className="p-0">
          <div className="border-b border-border px-3 pt-3">
            <div className="relative">
              <TabsList ref={tabListRef} variant="default" className="scroll-hint w-full overflow-x-auto px-7 md:px-9">
                <TabsTrigger value="preview" className="flex-none gap-1.5"><Eye className="h-4 w-4" /><span className="hidden md:inline">预览</span></TabsTrigger>
                <TabsTrigger value="markdown" className="flex-none gap-1.5"><FileText className="h-4 w-4" /><span className="hidden md:inline">Markdown</span></TabsTrigger>
                <TabsTrigger value="json" className="flex-none gap-1.5"><FileJson className="h-4 w-4" /><span className="hidden md:inline">JSON</span></TabsTrigger>
                <TabsTrigger value="tables" className="flex-none gap-1.5"><Table2 className="h-4 w-4" /><span className="hidden md:inline">表格</span></TabsTrigger>
                <TabsTrigger value="figures" className="flex-none gap-1.5"><Image className="h-4 w-4" /><span className="hidden md:inline">图片</span></TabsTrigger>
                <TabsTrigger value="extract" className="flex-none gap-1.5"><Brain className="h-4 w-4" /><span className="hidden md:inline">抽取</span></TabsTrigger>
                <TabsTrigger value="workflow" className="flex-none gap-1.5"><Database className="h-4 w-4" /><span className="hidden md:inline">入库</span></TabsTrigger>
                <TabsTrigger value="quality" className="flex-none gap-1.5"><ListChecks className="h-4 w-4" /><span className="hidden md:inline">质量</span></TabsTrigger>
                <TabsTrigger value="artifacts" className="flex-none gap-1.5"><Archive className="h-4 w-4" /><span className="hidden md:inline">产物</span></TabsTrigger>
              </TabsList>
              <button
                type="button"
                onClick={() => scrollTabs(-1)}
                className="absolute left-0 top-1/2 hidden h-10 w-8 -translate-y-1/2 items-center justify-center rounded-r-lg border border-border bg-white/90 text-text shadow-sm hover:bg-bg md:flex"
                aria-label="向左滚动标签"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => scrollTabs(1)}
                className="absolute right-0 top-1/2 hidden h-10 w-8 -translate-y-1/2 items-center justify-center rounded-l-lg border border-border bg-white/90 text-text shadow-sm hover:bg-bg md:flex"
                aria-label="向右滚动标签"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
            <select
              className="md:hidden doc-select mt-2 w-full"
              value={activeTab}
              onChange={(event) => setActiveTab(event.target.value)}
              aria-label="切换结果标签"
            >
              <option value="preview">预览</option>
              <option value="markdown">Markdown</option>
              <option value="json">JSON</option>
              <option value="tables">表格</option>
              <option value="figures">图片</option>
              <option value="extract">抽取</option>
              <option value="workflow">入库</option>
              <option value="quality">质量</option>
              <option value="artifacts">产物</option>
            </select>
          </div>

          <TabsContent value="preview" className="m-0">
            <div className="doc-preview-grid">
              <div className="doc-source-pane">
                <div className="doc-panel-head">
                  <div>
                    <h3>PDF 原页</h3>
                    <p>页面截图中的 bbox 与右侧 document.md 块同步高亮。</p>
                  </div>
                  <div className="doc-page-controls">
                    <Button
                      type="button"
                      variant="secondary"
                      size="icon-xs"
                      aria-label="上一页"
                      disabled={!pageNumbers.length || activePage <= pageNumbers[0]}
                      onClick={() => selectPage(pageNumbers[Math.max(0, pageNumbers.indexOf(activePage) - 1)] || activePage)}
                    >
                      <ChevronLeft className="h-3 w-3" />
                    </Button>
                    <select className="doc-page-select" value={activePage} onChange={(event) => selectPage(Number(event.target.value))}>
                      {(pageNumbers.length ? pageNumbers : [1]).map((page) => (
                        <option key={page} value={page}>p{page}</option>
                      ))}
                    </select>
                    <Button
                      type="button"
                      variant="secondary"
                      size="icon-xs"
                      aria-label="下一页"
                      disabled={!pageNumbers.length || activePage >= pageNumbers[pageNumbers.length - 1]}
                      onClick={() => selectPage(pageNumbers[Math.min(pageNumbers.length - 1, pageNumbers.indexOf(activePage) + 1)] || activePage)}
                    >
                      <ChevronRight className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
                <div className="doc-source-page" ref={pdfPaneRef}>
                  {taskId ? previewPages.map((page, index) => {
                    const nextPage = previewPages[index + 1]
                    const bridgeRelation = nextPage
                      ? visibleRelations.find((relation) => {
                        const pages = relationPages(relation, tableById)
                        return pages.includes(page) && pages.includes(nextPage)
                      })
                      : undefined
                    const bridgeTableIds = bridgeRelation ? relationTableIds(bridgeRelation) : []
                    const bridgeFocusId = bridgeTableIds[1] || bridgeTableIds[0] || (bridgeRelation ? relationId(bridgeRelation, index) : '')
                    return (
                      <div className="doc-pdf-page-stack" key={page}>
                        <PdfPagePreview
                          taskId={taskId}
                          pageNumberValue={page}
                          page={pageByNumber.get(page)}
                          overlays={overlays.filter((entry) => entry.pageNumber === page)}
                          relations={visibleRelations.filter((relation) => relationPages(relation, tableById).includes(page))}
                          tableById={tableById}
                          activeFocusKeys={activeFocusKeys}
                          onFocus={focusTarget}
                          onOpenResource={(url, filename) => void openResource(url, filename)}
                        />
                        {bridgeRelation ? (
                          <MergePageBridge
                            relation={bridgeRelation}
                            onClick={() => focusTarget({ kind: 'table', id: bridgeFocusId, page: nextPage || page })}
                          />
                        ) : null}
                      </div>
                    )
                  }) : <EmptyState icon={Image} title="暂无页图" description="当前任务未返回页面截图。" size="sm" className="min-h-[240px]" />}
                </div>
              </div>
              <div className="doc-content-pane">
                <div className="doc-panel-head">
                  <div>
                    <h3>document.md</h3>
                    <p>渲染为可读 HTML，点击块会定位到对应 PDF 页。</p>
                  </div>
                </div>
                <div className="doc-md-render doc-md-preview" ref={markdownPaneRef}>
                  {previewMarkdownBlocks.length ? previewMarkdownBlocks.map((block) => {
                    const isFocused = hasFocusedKey(block.focusKeys, activeFocusKeys)
                    return (
                      <article
                        role="button"
                        tabIndex={0}
                        className={`doc-md-block ${isFocused ? 'is-focused' : ''}`}
                        key={block.id}
                        data-focus-keys={block.focusKeys.join(' ')}
                        onClick={() => focusTarget({ kind: 'block', id: block.id, page: block.pageNumber })}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            focusTarget({ kind: 'block', id: block.id, page: block.pageNumber })
                          }
                        }}
                      >
                        <span className="doc-md-block-meta">p{block.pageNumber} · {block.title}</span>
                        <div className="doc-md-html" dangerouslySetInnerHTML={{ __html: block.html }} />
                      </article>
                    )
                  }) : <EmptyState icon={FileText} title="暂无 Markdown 块" description="当前页没有可渲染的 Markdown 内容。" size="sm" className="min-h-[240px]" />}
                </div>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="markdown" className="m-0">
            <div className="doc-md-render is-full">
              {markdownBlocks.length ? markdownBlocks.map((block) => (
                <article
                  role="button"
                  tabIndex={0}
                  className={`doc-md-block ${hasFocusedKey(block.focusKeys, activeFocusKeys) ? 'is-focused' : ''}`}
                  key={block.id}
                  data-focus-keys={block.focusKeys.join(' ')}
                  onClick={() => focusTarget({ kind: 'block', id: block.id, page: block.pageNumber })}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      focusTarget({ kind: 'block', id: block.id, page: block.pageNumber })
                    }
                  }}
                >
                  <span className="doc-md-block-meta">p{block.pageNumber} · {block.title}</span>
                  <div className="doc-md-html" dangerouslySetInnerHTML={{ __html: block.html }} />
                </article>
              )) : <EmptyState icon={FileText} title="暂无 Markdown 内容" description="当前任务没有返回 Markdown 产物。" size="sm" className="min-h-[240px]" />}
            </div>
          </TabsContent>

          <TabsContent value="json" className="m-0">
            <pre className="doc-json">{stringify({ manifest: result.manifest, blocks, tables, figures, sourceMap })}</pre>
          </TabsContent>

          <TabsContent value="tables" className="m-0">
            <div className="doc-table-list">
              {physicalTables.length ? physicalTables.map((table, index) => {
                const sourceUrl = firstSourceUrl(sourceMap, table)
                return (
                  <div className="doc-data-row" key={table.table_id || index}>
                    <h3><Table2 className="mr-2 inline h-4 w-4" />{table.title || table.caption || table.table_id || `表格 ${index + 1}`}</h3>
                    <p>页码 {table.page_number || 1}{table.sheet_name ? ` · ${table.sheet_name}` : ''} · {table.quality?.row_count || 0} 行 · {table.quality?.column_count || 0} 列</p>
                    <div className="doc-action-row mt-2 justify-start">
                      <Button type="button" size="sm" variant="secondary" onClick={() => focusTarget({ kind: 'table', id: table.table_id || String(index), page: pageNumber(table.page_number) })}>
                        定位原页
                      </Button>
                      {sourceUrl ? (
                        <Button type="button" size="sm" variant="secondary" leftIcon={<ExternalLink className="h-4 w-4" />} onClick={() => void openResource(sourceUrl, `${table.table_id || 'table'}.json`)}>
                          打开来源
                        </Button>
                      ) : null}
                    </div>
                    {table.markdown ? <pre className="doc-table-markdown">{table.markdown}</pre> : null}
                  </div>
                )
              }) : <EmptyState icon={Table2} title="暂无表格产物" description="当前任务没有识别到表格。" size="sm" className="min-h-[240px]" />}
              <div className="doc-data-row">
                <h3>表格关系复核</h3>
                <p>跨页断表候选、逻辑合并关系和人工复核结果。</p>
              </div>
              {relationItems.length ? relationItems.map((relation, index) => {
                const id = relationId(relation, index)
                const tableIds = relationTableIds(relation)
                const pages = relationPages(relation, tableById)
                return (
                  <div className="doc-data-row" key={id}>
                    <h3>{relationTables(relation)}</h3>
                    <p>
                      {relation.relation_type || relation.merge_status || 'relation'} · 置信度 {relationConfidence(relation)}
                      {relation.review_status ? ` · ${relation.review_status}` : ''}
                    </p>
                    {tableIds.length && isPreviewCrossPageTableRelation(relation, tableById) ? (
                      <div className={`doc-relation-flow ${relationFlowTone(relation)}`}>
                        {tableIds.map((tableId, nodeIndex) => {
                          const table = tableById.get(tableId)
                          const tablePage = table?.page_number || pages[nodeIndex] || pages[0] || 1
                          return (
                            <div className="doc-relation-step" key={`${id}-${tableId}-${nodeIndex}`}>
                              <button
                                type="button"
                                className="doc-relation-node"
                                onClick={() => focusTarget({ kind: 'table', id: tableId, page: pageNumber(tablePage) })}
                              >
                                <span className="doc-relation-page">p{tablePage}</span>
                                <strong>{tableId}</strong>
                                <span>{tableLabel(table, tableId)}</span>
                                <em>{table?.quality?.row_count || 0} 行 · {table?.quality?.column_count || 0} 列</em>
                              </button>
                              {nodeIndex < tableIds.length - 1 ? (
                                <div className="doc-relation-connector" aria-hidden="true">
                                  <span />
                                </div>
                              ) : null}
                            </div>
                          )
                        })}
                      </div>
                    ) : null}
                    {relation.reasons?.length || relation.merge_reasons?.length ? (
                      <p>{[...(relation.reasons || []), ...(relation.merge_reasons || [])].join('；')}</p>
                    ) : null}
                    <div className="doc-action-row mt-3 justify-start">
                      <Button type="button" size="sm" variant="secondary" onClick={() => onReviewTableRelation(id, 'accepted')}>
                        接受合并
                      </Button>
                      <Button type="button" size="sm" variant="secondary" onClick={() => onReviewTableRelation(id, 'rejected')}>
                        拒绝合并
                      </Button>
                    </div>
                  </div>
                )
              }) : <EmptyState icon={Table2} title="暂无跨页断表候选" description="当前产物没有返回 table_relations。" size="sm" className="min-h-[160px]" />}
            </div>
          </TabsContent>

          <TabsContent value="figures" className="m-0">
            <div className="doc-figure-list">
              {figureItems.length ? figureItems.map((figure: DocumentFigure, index) => {
                const imageId = figure.image_id || figure.block_id || `figure-${index + 1}`
                const sourceUrl = sourceEntriesFor(sourceMap, (entry) => entry.image_id === figure.image_id)[0]?.open_source_url
                return (
                  <div className="doc-data-row" key={imageId}>
                    <h3><Image className="mr-2 inline h-4 w-4" />{figure.caption || imageId}</h3>
                    <p>页码 {figure.page_number || 1} · {figure.type || 'image'} · {figure.evidence_id || ''}</p>
                    {figure.bbox?.length ? <p>bbox: {figure.bbox.join(', ')} {figure.bbox_unit || ''}</p> : null}
                    <div className="doc-action-row mt-2 justify-start">
                      <Button type="button" size="sm" variant="secondary" onClick={() => focusTarget({ kind: 'figure', id: imageId, page: pageNumber(figure.page_number) })}>
                        定位原页
                      </Button>
                      {sourceUrl ? (
                        <Button type="button" size="sm" variant="secondary" leftIcon={<ExternalLink className="h-4 w-4" />} onClick={() => void openResource(sourceUrl, `${imageId}.json`)}>
                          打开来源
                        </Button>
                      ) : null}
                    </div>
                    {figure.image_path && taskId ? (
                      <AuthenticatedImage
                        src={documentArtifactUrl(taskId, figure.image_path)}
                        alt={figure.alt_text || figure.caption || imageId || 'document figure'}
                        className="doc-figure-image"
                      />
                    ) : null}
                    {figure.ocr_text ? <p>{figure.ocr_text}</p> : null}
                  </div>
                )
              }) : <EmptyState icon={Image} title="暂无图片产物" description="当前任务没有识别到图片。" size="sm" className="min-h-[240px]" />}
            </div>
          </TabsContent>

          <TabsContent value="extract" className="m-0">
            <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,.95fr)_minmax(0,1.05fr)]">
              <div className="grid gap-3">
                <label className="doc-field">
                  <span className="doc-label">抽取模板</span>
                  <select className="doc-select" value={templateId} onChange={(event) => applyTemplate(event.target.value)}>
                    <option value="">自定义 JSON Schema</option>
                    {extractionTemplates.map((template) => (
                      <option key={template.template_id} value={template.template_id}>
                        {template.name || template.template_id}
                      </option>
                    ))}
                  </select>
                </label>
                {templateId ? (
                  <div className="doc-data-row">
                    <h3>{extractionTemplates.find((item) => item.template_id === templateId)?.name || templateId}</h3>
                    <p>{extractionTemplates.find((item) => item.template_id === templateId)?.description || '模板 schema 已载入，可直接运行抽取。'}</p>
                  </div>
                ) : null}
                <label className="doc-field">
                  <span className="doc-label">JSON Schema</span>
                  <textarea className="doc-textarea" value={schemaText} onChange={(event) => setSchemaText(event.target.value)} />
                </label>
                <label className="doc-field">
                  <span className="doc-label">抽取指令</span>
                  <input className="doc-input" value={instructions} onChange={(event) => setInstructions(event.target.value)} />
                </label>
                <Button type="button" onClick={() => onRunExtraction(schemaText, instructions, templateId)}>运行抽取</Button>
                {validationReport ? (
                  <div className="doc-data-row">
                    <h3>{validationReport.schema_valid ? 'Schema 有效' : 'Schema 需检查'}</h3>
                    <p>
                      evidence coverage {String(validationReport.evidence_coverage_ratio ?? 0)}
                      {missingFields.length ? ` · 缺失 ${missingFields.map(String).join(', ')}` : ''}
                    </p>
                  </div>
                ) : null}
              </div>
              <div className="grid gap-3">
                <pre className="doc-json">{stringify(extractionResult || { status: 'not_run' })}</pre>
                {Object.keys(evidenceMap).length ? (
                  <div className="doc-table-list">
                    <div className="doc-data-row">
                      <h3>字段证据</h3>
                      <p>每个非空字段会保留 evidence id、页码和原文片段。</p>
                    </div>
                    {Object.entries(evidenceMap).map(([field, evidences]) => (
                      <div className="doc-data-row" key={field}>
                        <h3>{field}</h3>
                        {evidences.length ? evidences.map((evidence, index) => (
                          <p key={`${field}-${index}`}>
                            p{String(evidence.page_number || 1)} · {String(evidence.quote || '')}
                            {evidence.open_source_url ? (
                              <>
                                {' · '}
                                <button type="button" className="doc-source-link" onClick={() => void openResource(String(evidence.open_source_url), `${field}-evidence.json`)}>打开证据</button>
                              </>
                            ) : null}
                          </p>
                        )) : <p>未找到证据，结果保持 null 或需人工复核。</p>}
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
          </TabsContent>

          <TabsContent value="quality" className="m-0">
            <div className="doc-quality-list">
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {[
                  ['总体状态', quality?.overall_status || result.manifest?.quality_status || '-'],
                  ['页数', quality?.page_count ?? '-'],
                  ['块数', quality?.block_count ?? '-'],
                  ['表格', quality?.table_count ?? '-'],
                  ['图片', quality?.image_count ?? '-'],
                  ['可入库', quality?.ready_for_knowledge_base ? '是' : '待检查'],
                ].map(([label, value]) => (
                  <div className="doc-data-row" key={label}>
                    <h3>{value}</h3>
                    <p>{label}</p>
                  </div>
                ))}
              </div>
              {quality?.warnings?.length ? quality.warnings.map((warning, index) => (
                <div className="doc-data-row" key={`${warning.code}-${index}`}>
                  <h3>{warning.code || 'warning'}</h3>
                  <p>{warning.message}</p>
                </div>
              )) : <div className="doc-data-row"><h3>无阻塞警告</h3><p>当前质量报告未返回 warning。</p></div>}
            </div>
          </TabsContent>

          <TabsContent value="workflow" className="m-0">
            <div className="doc-quality-list">
              <div className="doc-workflow-head">
                <div>
                  <h3>
                    <Database className="h-4 w-4 text-primary" />
                    数据管线
                  </h3>
                  <p>PostgreSQL 与 results 目录保存全量解析信息；Wiki 保留文档入口和轻量产物清单。</p>
                </div>
                <div className="doc-action-row">
                  <Button type="button" variant="secondary" size="sm" onClick={() => onRefreshWorkflow()} leftIcon={<RefreshCw className="h-4 w-4" />}>
                    刷新状态
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    onClick={() => onImportWiki()}
                    disabled={workflowIsBusy || workflowStatus?.artifacts?.ready === false}
                    leftIcon={workflowBusy === 'wiki' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Archive className="h-4 w-4" />}
                  >
                    {workflowBusy === 'wiki' ? '导入中...' : '继续入库'}
                  </Button>
                </div>
              </div>

              <div className="doc-pipeline-note">
                <Database className="h-4 w-4" />
                <div>
                  Wiki 不复制全量解析包；<code>artifact_manifest.json</code> 只记录核心文件路径、hash 和版本，用于判断是否过期。完整文档、结构化块、表格、图片和证据页码默认直接从 results 目录读取并进入 <code>document_parser</code> schema。
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {[
                  ['解析产物包', workflowStatus?.artifacts?.status || 'unknown', workflowStatus?.artifacts?.message || '等待解析产物'],
                  ['Wiki 入库', workflowStatus?.targets?.wiki?.status || 'unknown', workflowStatus?.targets?.wiki?.message || workflowStatus?.targets?.wiki?.path || '未归档'],
                  ['语义层', workflowStatus?.targets?.milvus?.status || 'unknown', workflowStatus?.targets?.milvus?.message || '未生成语义 chunks'],
                  ['PostgreSQL', workflowStatus?.targets?.postgres?.status || 'unknown', workflowStatus?.targets?.postgres?.message || '未入库'],
                ].map(([label, status, desc]) => (
                  <div className="doc-data-row" key={label}>
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm font-semibold text-text">{label}</span>
                      <span className={`secondary-status ${workflowStateClass(status)}`}>{workflowStateLabel(status)}</span>
                    </div>
                    <p>{desc}</p>
                  </div>
                ))}
              </div>

              <div className="doc-data-row">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h3>核心解析产物清单</h3>
                    <p>{workflowStatus?.artifacts?.readyCount ?? 0}/{workflowStatus?.artifacts?.total ?? 0} 个核心文件已生成</p>
                  </div>
                  <span className={`secondary-status ${workflowStatus?.artifacts?.ready ? 'secondary-status-success' : 'secondary-status-warning'}`}>
                    {workflowStatus?.artifacts?.ready ? '已就绪' : '待补齐'}
                  </span>
                </div>
                {workflowStatus?.artifacts?.missing?.length ? (
                  <p>缺少: {workflowStatus.artifacts.missing.join('、')}</p>
                ) : null}
              </div>

              <div className="doc-action-row justify-start">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => onImportWiki()}
                  disabled={workflowIsBusy || workflowStatus?.artifacts?.ready === false}
                  leftIcon={workflowBusy === 'wiki' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Archive className="h-4 w-4" />}
                >
                  {workflowBusy === 'wiki' ? '导入中...' : '导入 Wiki'}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => onImportDatabase()}
                  disabled={!wikiPackageReady || workflowIsBusy}
                  leftIcon={workflowBusy === 'postgres' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
                >
                  {workflowBusy === 'postgres' ? '入库中...' : '导入 PostgreSQL'}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => onBuildSemanticChunks(false)}
                  disabled={!wikiPackageReady || workflowIsBusy}
                  leftIcon={workflowBusy === 'milvus' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Brain className="h-4 w-4" />}
                >
                  {workflowBusy === 'milvus' ? '生成中...' : '生成语义 chunks'}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => onBuildSemanticChunks(true)}
                  disabled={!wikiPackageReady || workflowIsBusy}
                  leftIcon={workflowBusy === 'milvus-ingest' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Brain className="h-4 w-4" />}
                >
                  {workflowBusy === 'milvus-ingest' ? '写入中...' : '写入 Milvus'}
                </Button>
              </div>

              {wikiImportResult?.packageDir ? (
                <div className="doc-data-row">
                  <h3>{wikiImportResult.documentKey || 'Wiki package'}</h3>
                  <p>{wikiImportResult.packageDir}</p>
                </div>
              ) : null}
            </div>
          </TabsContent>

          <TabsContent value="artifacts" className="m-0">
            <div className="doc-artifact-list">
              {artifactEntries.map(([name, info]) => (
                <div className="doc-data-row" key={name}>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h3><FileJson className="mr-2 inline h-4 w-4" />{name}</h3>
                      <p>{info.exists ? `${info.size || 0} bytes` : '缺失'}</p>
                    </div>
                    {info.exists && taskId ? (
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        leftIcon={<ExternalLink className="h-4 w-4" />}
                        onClick={() => void openResource(documentArtifactUrl(taskId, info.path || name), info.path || name)}
                      >
                        打开
                      </Button>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </TabsContent>
        </Tabs>
      ) : null}

      {!loading && !result ? (
        <EmptyState
          icon={FileText}
          title="任务尚未生成可展示结果"
          description="解析完成后会自动刷新，也可以点击右上角刷新任务。"
          size="md"
          className="min-h-[320px]"
        />
      ) : null}
    </section>
  )
}
