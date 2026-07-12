import { useCallback, useEffect, useRef, useState } from 'react'
import type { ArtifactsMap, BboxExtent, PageContent, PdfCtx, SelectedTrace, SourceCorrection, SourceMeta, SourceTable, SrcCtx } from '../../lib/pdfTypes'
import { fetchPageSourceApi, getPdfUrl as getPdfUrlApi, showTableSourceApi } from '../../features/pdf-parsing/api'
import { makeEditableHtml, normalizeBbox, parseBbox, parseBboxFromAttr, sanitizeReadingHtml, sanitizeTableHtml } from '../../lib/pdfSanitize'
import { renderPageContentHtml } from '../../components/pdf/pdfSourceRendering'
import { createTaskRequestScope } from './taskRequestScope'

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
  const sourceTaskIdRef = useRef<string | null>(null)
  const [sourceRequestScope] = useState(createTaskRequestScope)
  const [pageRequestScope] = useState(createTaskRequestScope)

  const [sourceVisible, setSourceVisible] = useState(false)
  const [pdfZoom, setPdfZoom] = useState<string>('100')
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

  const renderPageReading = useCallback(
    (pd: PageContent): string => {
      if (!pd) return ''
      return renderPageContentHtml(pd)
    },
    [],
  )

  const renderReadingPane = useCallback(async () => {
    const tid = taskIdRef.current
    const ctx = srcCtx.current
    if (!tid || !ctx || sourceTaskIdRef.current !== tid) return
    pageRequestScope.invalidate()
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
    const request = pageRequestScope.begin(tid)
    try {
      const d = await fetchPageSourceApi(tid, pageNum, ctx.selectedTableIndex)
      if (!pageRequestScope.isCurrent(request, taskIdRef.current) || sourceTaskIdRef.current !== tid || srcCtx.current !== ctx) return
      ctx.pageCache[pageNum] = d as PageContent
      if (pdfCtx.current?.currentPage === pageNum && ctx.readingMode === 'page') {
        setReadingHtml(sanitizeReadingHtml(renderPageReading(d as PageContent)))
      }
    } catch {
      if (pageRequestScope.isCurrent(request, taskIdRef.current) && sourceTaskIdRef.current === tid && srcCtx.current === ctx) {
        setReadingHtml('')
      }
    }
  }, [pageRequestScope, renderPageReading, taskIdRef])

  const updatePdfViewer = useCallback(
    (page: number) => {
      const ctx = pdfCtx.current
      const tid = taskIdRef.current
      if (!ctx || !tid || sourceTaskIdRef.current !== tid) return
      const next = Math.max(1, Math.min(ctx.pageCount, page))
      ctx.currentPage = next
      setPdfCurPage(next)
      if (srcCtx.current && srcCtx.current.readingMode === 'page') {
        void renderReadingPane()
      }
    },
    [renderReadingPane, taskIdRef],
  )

  const showTableSource = useCallback(
    async (tableIndex: number, line?: number) => {
      const tid = taskIdRef.current
      if (!tid || !tableIndex) return
      const request = sourceRequestScope.begin(tid)
      if (line) focusMarkdownLine(line)
      try {
        const d = await showTableSourceApi(tid, tableIndex)
        if (!sourceRequestScope.isCurrent(request, taskIdRef.current)) return
        const tbl = (d.table || {}) as SourceTable
        const rendered = sanitizeTableHtml(d.table_html || '')
        const corr = (d.correction || {}) as SourceCorrection
        const corrText = corr.table_markdown || d.table_html || ''
        pageRequestScope.invalidate()
        sourceTaskIdRef.current = tid
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
        setPdfZoom('100')
        setSourceVisible(true)
        setTimeout(() => {
          if (!sourceRequestScope.isCurrent(request, taskIdRef.current) || sourceTaskIdRef.current !== tid) return
          if (corrStatusRef.current) corrStatusRef.current.value = corr.review_status || 'unreviewed'
          if (corrTextRef.current) corrTextRef.current.value = corrText
          if (corrNoteRef.current) corrNoteRef.current.value = corr.note || ''
          void renderReadingPane()
        }, 50)
      } catch (e) {
        if (sourceRequestScope.isCurrent(request, taskIdRef.current)) reportError((e as Error).message)
      }
    },
    [taskIdRef, focusMarkdownLine, reportError, corrStatusRef, corrTextRef, corrNoteRef, renderReadingPane, sourceRequestScope, pageRequestScope],
  )

  const switchReadingMode = useCallback(
    (mode: 'table' | 'page') => {
      const tid = taskIdRef.current
      if (!tid || sourceTaskIdRef.current !== tid || !srcCtx.current) return
      srcCtx.current.readingMode = mode
      setReadingMode(mode)
      void renderReadingPane()
    },
    [renderReadingPane, taskIdRef],
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
