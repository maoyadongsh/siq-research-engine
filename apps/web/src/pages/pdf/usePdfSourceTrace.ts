import { useCallback, useEffect, useRef, useState } from 'react'
import type { ArtifactsMap, BboxExtent, PageBlock, PageContent, PdfCtx, SelectedTrace, SourceCorrection, SourceMeta, SourceTable, SrcCtx } from '../../lib/pdfTypes'
import { fetchPageSourceApi, getPdfUrl as getPdfUrlApi, showTableSourceApi } from '../../lib/pdfApi'
import { escHtml } from '../../lib/pdfFormatting'
import {
  makeEditableHtml,
  normalizeBbox,
  parseBbox,
  parseBboxFromAttr,
  sanitizeReadingHtml,
  sanitizeTableHtml,
} from '../../lib/pdfSanitize'

export interface UsePdfSourceTraceOptions {
  taskIdRef: React.MutableRefObject<string | null>
  corrStatusRef: React.RefObject<HTMLSelectElement | null>
  corrTextRef: React.RefObject<HTMLTextAreaElement | null>
  corrNoteRef: React.RefObject<HTMLTextAreaElement | null>
  focusMarkdownLine: (line: number) => void
  reportError: (msg: string | null) => void
}

export function usePdfSourceTrace(options: UsePdfSourceTraceOptions) {
  const { taskIdRef, corrStatusRef, corrTextRef, corrNoteRef, focusMarkdownLine, reportError } = options

  const pdfCtx = useRef<PdfCtx | null>(null)
  const srcCtx = useRef<SrcCtx | null>(null)
  const editTableRef = useRef<HTMLDivElement | null>(null)

  const [sourceVisible, setSourceVisible] = useState(false)
  const [pdfZoom, setPdfZoom] = useState<string>('fit')
  const [pdfCurPage, setPdfCurPage] = useState(1)
  const [readingMode, setReadingMode] = useState<'table' | 'page'>('page')
  const [readingHtml, setReadingHtml] = useState('')
  const [srcTable, setSrcTable] = useState<SourceTable | null>(null)
  const [srcMeta, setSrcMeta] = useState<SourceMeta | null>(null)

  const getPdfUrl = useCallback(
    (page: number) => {
      const tid = taskIdRef.current
      if (!tid || !page) return ''
      return getPdfUrlApi(tid, page)
    },
    [taskIdRef],
  )

  const renderBlock = useCallback((b: PageBlock): string => {
    const type = b?.type || 'unknown'
    const bb = Array.isArray(b?.bbox) && b.bbox.length === 4 ? `bbox: ${b.bbox.join(', ')}` : ''
    if (type === 'table') {
      const label = b.table_index ? `表 ${b.table_index}` : '表格块'
      const tags = ([] as (string | unknown)[])
        .concat(Array.isArray(b.heading) ? b.heading : [b.heading])
        .concat(Array.isArray(b.matched_financial_names) ? b.matched_financial_names : [])
        .filter(Boolean)
        .slice(0, 3)
        .map((t) => `<span class="pdf-page-block-tag">${escHtml(String(t))}</span>`)
        .join('')
      const act = b.table_index ? `<button class="pdf-trace-btn" data-ptidx="${b.table_index}">打开该表</button>` : ''
      return `<section class="pdf-page-block ${b.is_focus_table ? 'focus-table' : ''}"><div class="pdf-page-block-head"><div><span class="pdf-page-block-type">${escHtml(label)}</span><span class="pdf-page-block-meta">${escHtml(bb || '表格解析块')}</span></div>${act}</div><div class="pdf-page-block-tag-row">${tags}</div><div class="pdf-table-wrap pdf-page-table-wrap">${b.table_html ? sanitizeTableHtml(b.table_html) : '<div style="color:#64748b">表格区域，无可用 HTML。</div>'}</div></section>`
    }
    if (type === 'list') {
      const items = (b.list_items || [])
        .map((i: unknown) => `<li>${escHtml(String(i || ''))}</li>`)
        .join('')
      return `<section class="pdf-page-block"><div class="pdf-page-block-head"><div><span class="pdf-page-block-type">列表</span><span class="pdf-page-block-meta">${escHtml(bb || '列表解析块')}</span></div></div><ul class="pdf-page-block-list">${items}</ul></section>`
    }
    if (type === 'image') {
      return `<section class="pdf-page-block pdf-page-block-muted"><div class="pdf-page-block-head"><div><span class="pdf-page-block-type">图片</span><span class="pdf-page-block-meta">${escHtml(bb || '图片解析块')}</span></div></div><div class="pdf-page-block-text" style="color:#64748b">来源图像：${escHtml(b.image_path || '未提供路径')}</div></section>`
    }
    const hLike = type === 'header' || Number(b.text_level || 0) > 0
    const tLabel = type === 'header' ? '页眉' : type === 'page_number' ? '页码' : hLike ? '标题' : '文本'
    return `<section class="pdf-page-block ${type === 'page_number' || type === 'header' ? 'pdf-page-block-muted' : ''}"><div class="pdf-page-block-head"><div><span class="pdf-page-block-type">${escHtml(tLabel)}</span><span class="pdf-page-block-meta">${escHtml(bb || '文本解析块')}</span></div></div><div class="pdf-page-block-text ${hLike ? 'pdf-page-block-heading' : ''}">${escHtml(b.text || ' ')}</div></section>`
  }, [])

  const renderPageReading = useCallback(
    (pd: PageContent): string => {
      if (!pd) return ''
      const pT = pd.page_tables || []
      const pTH = pT.length
        ? pT
            .map(
              (t) =>
                `<button class="pdf-chip trace-chip" data-ptidx="${t.table_index}">表 ${t.table_index}${(t.matched_financial_names || []).length ? ' · ' + (t.matched_financial_names as string[]).join('、') : ''}</button>`,
            )
            .join('')
        : '<span style="color:#64748b">这一页没有可定位的表格。</span>'
      const blks = (pd.blocks || []).map((b: PageBlock) => renderBlock(b)).join('')
      return `<div class="pdf-page-reading-view"><div class="pdf-page-reading-summary"><div><strong>PDF 第 ${pd.page_number || pdfCtx.current?.currentPage || 1}</strong><span>${pd.block_count || 0} 个解析块 / ${pd.table_count || 0} 张表</span></div><div class="pdf-chip-row">${pTH}</div></div>${blks || '<div style="padding:20px;color:#64748b">没有可展示的解析内容。</div>'}</div>`
    },
    [renderBlock],
  )

  const renderReadingPane = useCallback(async () => {
    const ctx = srcCtx.current
    if (!ctx) return
    if (ctx.readingMode === 'table') {
      const e = makeEditableHtml(ctx.correctionText || ctx.tableHtml || '')
      setReadingHtml(sanitizeReadingHtml(e || ''))
      return
    }
    const pageNum = pdfCtx.current?.currentPage || ctx.sourcePage
    const cached = ctx.pageCache[pageNum]
    if (cached) {
      setReadingHtml(sanitizeReadingHtml(renderPageReading(cached as PageContent)))
      return
    }
    setReadingHtml('')
    try {
      const d = await fetchPageSourceApi(taskIdRef.current!, pageNum, ctx.selectedTableIndex)
      ctx.pageCache[pageNum] = d as PageContent
      if (pdfCtx.current?.currentPage === pageNum && ctx.readingMode === 'page') {
        setReadingHtml(sanitizeReadingHtml(renderPageReading(d as PageContent)))
      }
    } catch {
      setReadingHtml('')
    }
  }, [renderPageReading, taskIdRef])

  const updatePdfViewer = useCallback(
    (page: number) => {
      const ctx = pdfCtx.current
      if (!ctx) return
      const next = Math.max(1, Math.min(ctx.pageCount, page))
      ctx.currentPage = next
      setPdfCurPage(next)
      if (srcCtx.current && srcCtx.current.readingMode === 'page') {
        void renderReadingPane()
      }
    },
    [renderReadingPane],
  )

  const showTableSource = useCallback(
    async (tableIndex: number, line?: number) => {
      const tid = taskIdRef.current
      if (!tid || !tableIndex) return
      if (line) focusMarkdownLine(line)
      try {
        const d = await showTableSourceApi(tid, tableIndex)
        const tbl = (d.table || {}) as SourceTable
        const rendered = sanitizeTableHtml(d.table_html || '')
        const corr = (d.correction || {}) as SourceCorrection
        const corrText = corr.table_markdown || d.table_html || ''
        pdfCtx.current = d.pdf_page_image?.url
          ? {
              sourcePage: Number(d.pdf_page_image.page_number || 1),
              currentPage: Number(d.pdf_page_image.page_number || 1),
              pageCount: Number(d.pdf_page_image.page_count || d.pdf_page_image.page_number || 1),
              bbox: Array.isArray(d.pdf_page_image.bbox) ? (d.pdf_page_image.bbox as number[]) : [],
              bboxExtent: (d.pdf_page_image.bbox_extent || {}) as BboxExtent,
              selectedTrace: null,
            }
          : null
        srcCtx.current = {
          selectedTableIndex: Number(tbl.table_index || tableIndex || 0),
          sourcePage: Number(d.pdf_page_image?.page_number || tbl.pdf_page_number || 1),
          readingMode: 'page',
          tableHtml: rendered,
          correctionText: corrText,
          selectedCell: null,
          pageCache: d.page_content?.page_number ? { [Number(d.page_content.page_number)]: d.page_content as PageContent } : {},
        }
        setSrcTable(tbl)
        setSrcMeta({
          table: tbl,
          correction: corr,
          excerpt: (d.markdown_excerpt || []) as SourceMeta['excerpt'],
          artifacts: (d.artifacts || {}) as ArtifactsMap,
          pdfPageImage: d.pdf_page_image as SourceMeta['pdfPageImage'],
        })
        setReadingMode('page')
        setPdfCurPage(pdfCtx.current?.currentPage || 1)
        setPdfZoom('fit')
        setSourceVisible(true)
        setTimeout(() => {
          if (corrStatusRef.current) corrStatusRef.current.value = corr.review_status || 'unreviewed'
          if (corrTextRef.current) corrTextRef.current.value = corrText
          if (corrNoteRef.current) corrNoteRef.current.value = corr.note || ''
          void renderReadingPane()
        }, 50)
      } catch (e) {
        reportError((e as Error).message)
      }
    },
    [taskIdRef, focusMarkdownLine, reportError, corrStatusRef, corrTextRef, corrNoteRef, renderReadingPane],
  )

  const switchReadingMode = useCallback(
    (mode: 'table' | 'page') => {
      if (srcCtx.current) srcCtx.current.readingMode = mode
      setReadingMode(mode)
      void renderReadingPane()
    },
    [renderReadingPane],
  )

  const traceCell = useCallback((cell: HTMLElement): SelectedTrace | null => {
    const ctx = pdfCtx.current
    if (!ctx) return null
    const direct = parseBbox(parseBboxFromAttr(cell))
    if (!direct) return null
    return {
      pageNumber: ctx.sourcePage,
      bbox: normalizeBbox(direct, ctx.bboxExtent) || direct,
      source: 'cell_bbox',
      confidence: 'high',
    }
  }, [])

  // Auto-render reading pane when table source opens.
  useEffect(() => {
    if (!sourceVisible || !srcMeta) return
    const timer = setTimeout(() => {
      void renderReadingPane()
    }, 60)
    return () => clearTimeout(timer)
  }, [sourceVisible, srcMeta, renderReadingPane])

  return {
    pdfCtx,
    srcCtx,
    editTableRef,
    sourceVisible,
    setSourceVisible,
    pdfZoom,
    setPdfZoom,
    pdfCurPage,
    setPdfCurPage,
    readingMode,
    readingHtml,
    srcTable,
    srcMeta,
    getPdfUrl,
    showTableSource,
    updatePdfViewer,
    renderReadingPane,
    switchReadingMode,
    traceCell,
    setReadingHtml,
  }
}
