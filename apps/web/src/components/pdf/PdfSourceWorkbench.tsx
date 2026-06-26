import { useEffect, useRef } from 'react'
import { ExternalLink, Save } from 'lucide-react'
import type { FocusEvent, MouseEvent, MutableRefObject, RefObject } from 'react'
import type { PdfCtx, SourceMeta, SourceTable } from '../../lib/pdfTypes'
import { artifactUrl } from '../../lib/pdfApi'
import { handleAuthenticatedSourceClick } from '../../lib/authenticatedSourceLinks'
import { useAuthenticatedBlobUrl } from '../../lib/authenticatedFiles'

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
  }, [sourceVisible, srcTable, readingMode, readingHtml, pdfCurPage])

  const ctx = pdfCtx.current
  const pageCount = ctx?.pageCount || srcMeta?.pdfPageImage?.page_count || pdfCurPage
  const img = srcMeta?.pdfPageImage
  const pageUrl = sourceVisible && srcTable ? getPdfUrl(pdfCurPage) : ''
  const pageBlobUrl = useAuthenticatedBlobUrl(pageUrl)

  if (!sourceVisible || !srcTable) return null

  const corr = srcMeta?.correction || {}
  const excerpt = srcMeta?.excerpt || []
  const sArt = srcMeta?.artifacts || {}

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

  const overlays: React.ReactElement[] = []
  if (ctx?.bboxExtent?.width) {
    const ext = ctx.bboxExtent
    if (pdfCurPage === ctx.sourcePage && ctx.bbox?.length === 4) {
      const b = ctx.bbox
      const l = Math.max(0, Math.min(100, (b[0] / ext.width) * 100))
      const t = Math.max(0, Math.min(100, (b[1] / ext.height) * 100))
      const r = Math.max(0, Math.min(100, (b[2] / ext.width) * 100))
      const bt = Math.max(0, Math.min(100, (b[3] / ext.height) * 100))
      overlays.push(
        <div
          key="tbl"
          className="pdf-bbox"
          title="表格区域"
          style={{ left: `${l}%`, top: `${t}%`, width: `${Math.max(0, r - l)}%`, height: `${Math.max(0, bt - t)}%` }}
        />,
      )
    }
    const trace = ctx.selectedTrace
    if (trace && pdfCurPage === trace.pageNumber && trace.bbox?.length === 4) {
      const b = trace.bbox
      const l = Math.max(0, Math.min(100, (b[0] / ext.width) * 100))
      const t = Math.max(0, Math.min(100, (b[1] / ext.height) * 100))
      const r = Math.max(0, Math.min(100, (b[2] / ext.width) * 100))
      const bt = Math.max(0, Math.min(100, (b[3] / ext.height) * 100))
      overlays.push(
        <div
          key="tr"
          className={`pdf-bbox ${trace.source === 'text_anchor' ? 'pdf-bbox-text' : 'pdf-bbox-selected'}`}
          title={trace.source === 'cell_bbox' ? '单元格区域' : '文本锚定区域'}
          style={{ left: `${l}%`, top: `${t}%`, width: `${Math.max(0, r - l)}%`, height: `${Math.max(0, bt - t)}%` }}
        />,
      )
    }
  }

  return (
    <div ref={workbenchRef} className="apple-card rounded-[24px] p-4 sm:p-6">
      <h3 className="text-base font-semibold text-text mb-3">可视化溯源</h3>

      <div className="pdf-source-summary">
        <div>
          <strong>表 {srcTable.table_index || '--'}</strong>
          <span>Markdown 行 {srcTable.line || '-'}</span>
        </div>
        <div>
          <strong>{srcTable.rows || 0}</strong>
          <span>行</span>
        </div>
        <div>
          <strong>{srcTable.pdf_page_number || '--'}</strong>
          <span>
            PDF 页码{srcTable.pdf_page_source === 'markdown_marker_inferred' ? '（推断）' : ''}
          </span>
        </div>
        <div>
          <strong>{srcTable.cells || 0}</strong>
          <span>单元格</span>
        </div>
        <div>
          <strong>{Math.round((srcTable.empty_ratio || 0) * 1000) / 10}%</strong>
          <span>空单元格</span>
        </div>
        <div>
          <strong>{Math.round((srcTable.numeric_ratio || 0) * 1000) / 10}%</strong>
          <span>数字密度</span>
        </div>
      </div>

      <div className="pdf-source-meta">
        <span>附近标题</span>
        <b>{srcTable.heading || '未识别'}</b>
      </div>
      <div className="pdf-source-meta">
        <span>单位</span>
        <b>{srcTable.unit || '未识别'}</b>
      </div>
      <div className="pdf-source-meta">
        <span>命中类别</span>
        <b>{(srcTable.matched_financial_names || []).join('、') || '普通表'}</b>
      </div>
      <div className="pdf-source-meta">
        <span>PDF 坐标 bbox</span>
        <b>{(srcTable.bbox || []).join(', ') || '未识别'}</b>
      </div>
      <div className="pdf-source-meta">
        <span>页面截图</span>
        <b>{srcTable.source_image_path || '未识别'}</b>
      </div>

      <div className="pdf-workbench">
        <div className="pdf-source-block pdf-source-pane">
          <div className="pdf-source-pane-head">
            <div className="pdf-reading-topline">
              <h4>阅读视图</h4>
              <div className="pdf-reading-mode-switch">
                <button
                  className={`pdf-reading-mode-btn ${readingMode === 'page' ? 'active' : ''}`}
                  onClick={() => void switchReadingMode('page')}
                >
                  当前 PDF 页
                </button>
                <button
                  className={`pdf-reading-mode-btn ${readingMode === 'table' ? 'active' : ''}`}
                  onClick={() => void switchReadingMode('table')}
                >
                  当前表格
                </button>
              </div>
            </div>
            <div className="text-[.86rem] text-text-muted" style={{ minHeight: 20 }}>
              {readingMode === 'table'
                ? '当前表格模式：便于直接编辑并同步到下方修正文本。'
                : `当前 PDF 页模式：阅读视图随 PDF 翻页同步显示第 ${pdfCurPage} 页解析内容。`}
            </div>
          </div>
          {readingMode === 'table' ? (
            <div
              className="pdf-table-wrap pdf-editable"
              ref={editTableRef}
              onClick={onTableClick}
              onFocus={onTableFocus}
              onInput={onTableInput}
              onBlur={onTableInput}
              dangerouslySetInnerHTML={{ __html: readingHtml }}
            />
          ) : (
            <div className="pdf-reading-body" onClick={onReadingClick} dangerouslySetInnerHTML={{ __html: readingHtml }} />
          )}
        </div>

        <div className="pdf-source-block pdf-source-pane">
          <div className="pdf-source-pane-head">
            <h4>PDF 原页</h4>
            <div className="text-[.86rem] text-text-muted" style={{ minHeight: 20 }}>
              支持上下翻页与缩放，定位框仅显示在来源页。
            </div>
          </div>
          {img?.url || pageUrl ? (
            <div className="pdf-page-viewer" data-zoom={pdfZoom}>
              <div className="pdf-page-toolbar">
                <div className="pdf-page-topline">
                  <span>
                    PDF 第 {pdfCurPage} / {pageCount} 页
                  </span>
                  <div className="pdf-page-nav">
                    <button className="pdf-nav-btn" disabled={pdfCurPage <= 1} onClick={() => changePage(pdfCurPage - 1)}>
                      上一页
                    </button>
                    <input
                      className="pdf-page-input"
                      type="number"
                      min={1}
                      max={pageCount}
                      value={pdfCurPage}
                      onChange={(e) => changePage(Number(e.target.value))}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') changePage(Number((e.target as HTMLInputElement).value))
                      }}
                    />
                    <button className="pdf-nav-btn" disabled={pdfCurPage >= pageCount} onClick={() => changePage(pdfCurPage + 1)}>
                      下一页
                    </button>
                  </div>
                </div>
                <div className="pdf-zoom-controls">
                  {(['fit', '1', '1.5', '2'] as const).map((z) => (
                    <button key={z} className={`pdf-zoom-btn ${pdfZoom === z ? 'active' : ''}`} onClick={() => setPdfZoom(z)}>
                      {z === 'fit' ? '适应宽度' : z === '1' ? '100%' : z === '1.5' ? '150%' : '200%'}
                    </button>
                  ))}
                </div>
                <a
                  href={pageUrl}
                  target="_blank"
                  rel="noopener"
                  className="text-primary font-semibold text-sm no-underline inline-flex items-center gap-1"
                  onClick={(event) => {
                    handleAuthenticatedSourceClick(event.nativeEvent, pageUrl).catch((error) => {
                      console.warn('Failed to open authenticated source link', error)
                    })
                  }}
                >
                  <ExternalLink size={13} />
                  打开原页图片
                </a>
              </div>
              <div className="pdf-page-canvas">
                <div className="pdf-page-stage">
                  {pageBlobUrl ? <img src={pageBlobUrl} alt="PDF page" /> : null}
                  {overlays}
                </div>
              </div>
            </div>
          ) : (
            <div className="text-text-muted">未识别 PDF 页码，无法展示原页。</div>
          )}
        </div>
      </div>

      <div className="pdf-source-block">
        <h4>人工复核修正</h4>
        <div className="pdf-correction-toolbar">
          <label>
            状态
            <select ref={corrStatusRef} defaultValue={corr.review_status || 'unreviewed'}>
              {statusOpts.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <button className="pdf-trace-btn inline-flex items-center gap-1" onClick={() => void saveCorrection()}>
            <Save size={13} />
            保存修正
          </button>
          <span className="text-text-muted text-sm">{corr.updated_at ? `上次保存: ${corr.updated_at}` : ''}</span>
        </div>
        <textarea
          ref={corrTextRef}
          className="pdf-correction-editor"
          spellCheck={false}
          defaultValue={corr.table_markdown || srcTable.table_html || ''}
        />
        <textarea
          ref={corrNoteRef}
          className="pdf-correction-note"
          placeholder="复核备注，例如：第 3 列金额错位，应以 PDF 第 67 页为准。"
          defaultValue={corr.note || ''}
        />
      </div>

      {excerpt.length > 0 ? (
        <div className="pdf-source-block">
          <h4>Markdown 上下文</h4>
          {excerpt.map((item, i) => (
            <div key={i} className={`pdf-source-line ${item.focus ? 'focus' : ''}`}>
              <span>{item.line}</span>
              <code>{item.text || ' '}</code>
            </div>
          ))}
        </div>
      ) : null}

      {Object.keys(sArt).length > 0 ? (
        <div className="pdf-source-block">
          <h4>产物文件</h4>
          {Object.entries(sArt).map(([name, info]) => (
            <div key={name} className={`pdf-artifact-row ${info.exists ? 'ok' : 'missing'}`}>
              <span>{name}</span>
              <code>{info.path || '未生成'}</code>
              {info.exists && info.url ? (
                <a className="pdf-trace-btn inline-flex items-center gap-1" href={artifactUrl(info)} target="_blank" rel="noopener">
                  <ExternalLink size={13} />
                  打开
                </a>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}
