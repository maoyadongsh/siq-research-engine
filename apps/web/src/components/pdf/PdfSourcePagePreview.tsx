import { useEffect, useState, type CSSProperties, type MouseEvent } from 'react'
import { ExternalLink, FileText, Loader2 } from 'lucide-react'
import { handleAuthenticatedSourceClick } from '../../lib/authenticatedSourceLinks'
import { useAuthenticatedBlobUrl } from '../../lib/authenticatedFiles'
import { parseBbox as parsePdfBbox } from '../../lib/pdfSanitize'
import type { BboxExtent } from '../../lib/pdfTypes'
import { blockFocusKey, type PageOverlayEntry, type TableRelationCandidate } from './pdfSourceWorkbenchTypes'

function validBbox(value: unknown): number[] {
  const bbox = parsePdfBbox(value)
  if (!bbox || bbox.length !== 4) return []
  if (bbox[2] <= bbox[0] || bbox[3] <= bbox[1]) return []
  return bbox
}

function clampPercent(value: number) {
  if (!Number.isFinite(value)) return 0
  return Math.max(0, Math.min(100, value))
}

function bboxStyle(bbox: number[], extent: BboxExtent): CSSProperties {
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

function mergeStemStyle(bbox: number[], extent: BboxExtent, mode: 'from' | 'to'): CSSProperties {
  const x = clampPercent((((bbox[0] + bbox[2]) / 2) / extent.width) * 100)
  if (mode === 'from') {
    const top = clampPercent((bbox[3] / extent.height) * 100)
    return { left: `${x}%`, top: `${top}%`, height: `${Math.max(5, 100 - top)}%` }
  }
  const height = clampPercent((bbox[1] / extent.height) * 100)
  return { left: `${x}%`, top: 0, height: `${Math.max(5, height)}%` }
}

export function PageMergeBridge({
  relation,
  onClick,
}: {
  relation: TableRelationCandidate
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className={`pdf-page-merge-bridge ${relation.relationType === 'continuation' ? 'is-accepted' : 'is-candidate'}`}
      title={`${relation.pageNumbers[0]} -> ${relation.pageNumbers[1]} · ${relation.relationType}`}
      onClick={onClick}
    >
      <span>合并</span>
    </button>
  )
}

export function PdfPagePreviewCard({
  pageNumberValue,
  pageUrl,
  pageExtent,
  overlays,
  relationEntries,
  onReadingClick,
  onBlockFocus,
}: {
  pageNumberValue: number
  pageUrl: string
  pageExtent: BboxExtent
  overlays: PageOverlayEntry[]
  relationEntries: Array<{ relation: TableRelationCandidate; mode: 'from' | 'to' }>
  onReadingClick: (e: MouseEvent<HTMLDivElement>) => void
  onBlockFocus: (entry: PageOverlayEntry) => void
}) {
  const [pageImageFailed, setPageImageFailed] = useState(false)
  const pageBlobUrl = useAuthenticatedBlobUrl(pageUrl)

  useEffect(() => {
    if (!pageUrl || pageBlobUrl) return
    const timer = window.setTimeout(() => setPageImageFailed(true), 12000)
    return () => window.clearTimeout(timer)
  }, [pageBlobUrl, pageUrl])

  return (
    <article className="pdf-pdf-page-card">
      <div className="pdf-pdf-page-title">
        <span>PDF p{pageNumberValue}</span>
        <a
          href={pageUrl}
          target="_blank"
          rel="noopener"
          className="text-primary inline-flex items-center gap-1 text-sm font-semibold no-underline"
          onClick={(event) => {
            handleAuthenticatedSourceClick(event.nativeEvent, pageUrl).catch((error) => {
              console.warn('Failed to open authenticated source link', error)
            })
          }}
        >
          <ExternalLink size={13} />
          打开页图
        </a>
      </div>
      <div className="pdf-pdf-page-canvas" onClick={onReadingClick}>
        {pageBlobUrl ? (
          <>
            <img src={pageBlobUrl} alt={`PDF 第 ${pageNumberValue} 页`} className="pdf-pdf-page-image" onError={() => setPageImageFailed(true)} />
            <div className="pdf-overlay-layer" aria-hidden={!overlays.length}>
              {overlays.map((entry) => {
                const style = bboxStyle(entry.bbox, pageExtent)
                const cls =
                  entry.tone === 'focused'
                    ? `pdf-bbox ${entry.source === 'block' ? 'pdf-bbox-block' : 'pdf-bbox-table'} pdf-bbox-selected`
                    : entry.tone === 'trace'
                      ? 'pdf-bbox pdf-bbox-text'
                      : entry.tone === 'block'
                        ? 'pdf-bbox pdf-bbox-block'
                        : 'pdf-bbox pdf-bbox-table'
                return (
                  <button
                    key={`${pageNumberValue}-${entry.source}-${entry.tableIndex || entry.blockId || 'trace'}-${entry.bbox.join('-')}`}
                    type="button"
                    className={cls}
                    style={style}
                    title={entry.detail}
                    aria-label={entry.detail}
                    data-ptidx={entry.tableIndex || ''}
                    data-block-id={entry.blockId || ''}
                    data-block-type={entry.blockType || ''}
                    data-page-number={entry.pageNumber || ''}
                    data-focus-key={entry.blockId && entry.pageNumber ? blockFocusKey(entry.pageNumber, entry.blockId) : ''}
                    onClick={
                      entry.source === 'block'
                        ? (event) => {
                            event.preventDefault()
                            event.stopPropagation()
                            onBlockFocus(entry)
                          }
                        : undefined
                    }
                  >
                    <span>{entry.label}</span>
                  </button>
                )
              })}
              {relationEntries.map((item, index) => {
                const table = item.mode === 'from' ? item.relation.fromTable : item.relation.toTable
                const bbox = validBbox(table.bbox)
                if (!bbox.length) return null
                return (
                  <button
                    key={`${pageNumberValue}-${item.mode}-${item.relation.pageNumbers.join('-')}-${index}`}
                    type="button"
                    className={`pdf-merge-stem ${item.mode === 'from' ? 'is-from' : 'is-to'} ${item.relation.relationType === 'continuation' ? 'is-accepted' : 'is-candidate'}`}
                    style={mergeStemStyle(bbox, pageExtent, item.mode)}
                    title={`p${item.relation.pageNumbers[0]} -> p${item.relation.pageNumbers[1]} · ${item.relation.relationType}`}
                    aria-label="合并候选"
                  >
                    <span>合并</span>
                  </button>
                )
              })}
            </div>
          </>
        ) : (
          <div className={`pdf-page-state ${pageImageFailed ? 'is-error' : ''}`}>
            {pageImageFailed ? <FileText className="h-5 w-5" /> : <Loader2 className="h-5 w-5 animate-spin" />}
            <span>{pageImageFailed ? '页图暂不可用，请稍后重试或打开页图。' : '正在加载页图...'}</span>
          </div>
        )}
      </div>
    </article>
  )
}
