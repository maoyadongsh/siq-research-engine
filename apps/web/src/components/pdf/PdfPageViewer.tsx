import type { PdfCtx } from '../../lib/pdfTypes'
import { handleAuthenticatedSourceClick } from '../../lib/authenticatedSourceLinks'
import { useAuthenticatedBlobUrl } from '../../lib/authenticatedFiles'

export interface PdfPageViewerProps {
  pdfCtx: React.MutableRefObject<PdfCtx | null>
  pdfCurPage: number
  setPdfCurPage: (p: number) => void
  pdfZoom: string
  setPdfZoom: (z: string) => void
  getPdfUrl: (page: number) => string
}

export function PdfPageViewer({ pdfCtx, pdfCurPage, setPdfCurPage, pdfZoom, setPdfZoom, getPdfUrl }: PdfPageViewerProps) {
  const ctx = pdfCtx.current
  const pageCount = ctx?.pageCount || pdfCurPage || 1
  const pageUrl = getPdfUrl(pdfCurPage)
  const pageBlobUrl = useAuthenticatedBlobUrl(pageUrl)

  const updatePage = (page: number) => {
    const next = Math.max(1, Math.min(pageCount, page))
    // eslint-disable-next-line react-hooks/immutability
    if (pdfCtx.current) pdfCtx.current.currentPage = next
    setPdfCurPage(next)
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
    <div className="pdf-source-block pdf-source-pane">
      <div className="pdf-source-pane-head">
        <h4>PDF 原页</h4>
        <div className="text-[.86rem] text-text-muted" style={{ minHeight: 20 }}>
          支持上下翻页与缩放，定位框仅显示在来源页。
        </div>
      </div>
      {ctx ? (
        <div className="pdf-page-viewer" data-zoom={pdfZoom}>
          <div className="pdf-page-toolbar">
            <div className="pdf-page-topline">
              <span>
                PDF 第 {pdfCurPage} / {pageCount} 页
              </span>
              <div className="pdf-page-nav">
                <button className="pdf-nav-btn" disabled={pdfCurPage <= 1} onClick={() => updatePage(pdfCurPage - 1)}>
                  上一页
                </button>
                <input
                  className="pdf-page-input"
                  type="number"
                  min={1}
                  max={pageCount}
                  value={pdfCurPage}
                  onChange={(e) => updatePage(Number(e.target.value))}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') updatePage(Number((e.target as HTMLInputElement).value))
                  }}
                />
                <button className="pdf-nav-btn" disabled={pdfCurPage >= pageCount} onClick={() => updatePage(pdfCurPage + 1)}>
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
              className="text-primary font-semibold text-sm no-underline"
              onClick={(event) => {
                handleAuthenticatedSourceClick(event.nativeEvent, pageUrl).catch((error) => {
                  console.warn('Failed to open authenticated source link', error)
                })
              }}
            >
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
  )
}
