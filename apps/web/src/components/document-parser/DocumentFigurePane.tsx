import { ExternalLink, Image } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/page'
import { documentArtifactUrl } from '@/features/document-parser/api'
import type { DocumentFigure, DocumentSourceMapPayload } from '@/lib/documentTypes'
import { sourceEntriesFor, pageNumber } from './documentResultWorkbenchUtils'
import { AuthenticatedImage } from './DocumentSourcePreview'

export type DocumentFigurePaneProps = {
  figures: DocumentFigure[]
  sourceMap: DocumentSourceMapPayload | null
  taskId: string
  onFocusFigure: (figureId: string, page: number) => void
  openResource: (url: string, filename?: string) => void | Promise<void>
}

export function DocumentFigurePane({
  figures,
  sourceMap,
  taskId,
  onFocusFigure,
  openResource,
}: DocumentFigurePaneProps) {
  return (
    <div className="doc-figure-list">
      {figures.length ? figures.map((figure, index) => {
        const imageId = figure.image_id || figure.block_id || `figure-${index + 1}`
        const sourceUrl = sourceEntriesFor(sourceMap, (entry) => entry.image_id === figure.image_id)[0]?.open_source_url
        return (
          <div className="doc-data-row" key={imageId}>
            <h3><Image className="mr-2 inline h-4 w-4" />{figure.caption || imageId}</h3>
            <p>页码 {figure.page_number || 1} · {figure.type || 'image'} · {figure.evidence_id || ''}</p>
            {figure.bbox?.length ? <p>bbox: {figure.bbox.join(', ')} {figure.bbox_unit || ''}</p> : null}
            <div className="doc-action-row mt-2 justify-start">
              <Button
                type="button"
                size="sm"
                variant="secondary"
                onClick={() => onFocusFigure(imageId, pageNumber(figure.page_number))}
              >
                定位原页
              </Button>
              {sourceUrl ? (
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  leftIcon={<ExternalLink className="h-4 w-4" />}
                  onClick={() => void openResource(sourceUrl, `${imageId}.json`)}
                >
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
  )
}
