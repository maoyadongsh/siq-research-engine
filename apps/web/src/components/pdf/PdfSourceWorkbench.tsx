import { useEffect, useMemo, useRef, useState } from 'react'
import { FileText, Loader2 } from 'lucide-react'
import type { FocusEvent, MouseEvent, MutableRefObject, RefObject } from 'react'
import type { PdfCtx, PageContent, SelectedTrace, SourceMeta, SourceTable } from '../../lib/pdfTypes'
import { artifactUrl, fetchPdfArtifactJson, fetchPdfPageContentApi } from '../../features/pdf-parsing/api'
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
import {
  buildPagePreviewOverlays,
  chooseFocusTableIndex,
  cssAttrValue,
  deriveTaskId,
  findBackwardRelation,
  findForwardRelation,
  firstTableOnPage,
  lastTableOnPage,
  mergePhysicalTables,
  pageContentBlocks,
  pageExtentForPage,
  pageNumber,
  pageTablesForPage,
  relationModeForPage,
  relationsFromArtifactForPage,
  renderFallbackPageHtml,
  validBbox,
  type EnhancedArtifact,
  type TableRelationsArtifact,
} from './pdfSourceWorkbenchHelpers'

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

type FocusedBlock = {
  key: string
  blockId: string
  blockType: string
  page: number
  bbox: number[]
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
    void fetchPdfArtifactJson<EnhancedArtifact>(url, { signal: controller.signal })
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
    void fetchPdfArtifactJson<TableRelationsArtifact>(relationsArtifactUrlValue, { signal: controller.signal })
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
      const data = await fetchPdfPageContentApi(taskId, entry.pageNumber, entry.focusTableIndex || 0, { signal: controller.signal })
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
