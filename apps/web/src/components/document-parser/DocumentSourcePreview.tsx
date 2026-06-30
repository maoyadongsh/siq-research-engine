import { useEffect, useState } from 'react'
import { ExternalLink, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { documentSourcePageImageUrl } from '@/features/document-parser/api'
import { apiBlob } from '@/lib/apiClient'
import type {
  DocumentLayoutPage,
  DocumentTable,
  DocumentTableRelation,
} from '@/lib/documentTypes'
import {
  bboxExtent,
  bboxStyle,
  hasFocusedKey,
  mergeStemStyle,
  pageNumber,
  relationFlowTone,
  relationId,
  relationTableIds,
  relationTables,
  validBbox,
  type FocusTarget,
  type OverlayEntry,
} from './documentResultWorkbenchUtils'

type AuthenticatedImageProps = {
  src: string
  alt: string
  className?: string
  onLoadSize?: (size: { width: number; height: number }) => void
}

export function AuthenticatedImage({
  src,
  alt,
  className,
  onLoadSize,
}: AuthenticatedImageProps) {
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

type PdfPagePreviewProps = {
  taskId: string
  pageNumberValue: number
  page?: DocumentLayoutPage
  overlays: OverlayEntry[]
  relations: DocumentTableRelation[]
  tableById: Map<string, DocumentTable>
  activeFocusKeys: Set<string>
  onFocus: (focus: FocusTarget) => void
  onOpenResource: (url: string, filename?: string) => void
}

export function PdfPagePreview({
  taskId,
  pageNumberValue,
  page,
  overlays,
  relations,
  tableById,
  activeFocusKeys,
  onFocus,
  onOpenResource,
}: PdfPagePreviewProps) {
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
