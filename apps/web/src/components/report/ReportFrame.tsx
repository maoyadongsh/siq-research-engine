import { Loader2 } from 'lucide-react'
import { useEffect, useRef } from 'react'
import type { ReportItem } from '@/lib/reportTypes'
import { handleAuthenticatedSourceClick } from '@/lib/authenticatedSourceLinks'

interface ReportFrameProps {
  selectedReportUrl: string
  selectedReport: ReportItem | undefined
  reportSrcDoc: string
  contentLoading: boolean
  iframeTitle: string
  updatedAt: string
  accent: string
}

export default function ReportFrame({
  selectedReportUrl,
  selectedReport,
  reportSrcDoc,
  contentLoading,
  iframeTitle,
  updatedAt,
  accent,
}: ReportFrameProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)

  useEffect(() => {
    if (contentLoading || !reportSrcDoc) return
    const iframe = iframeRef.current
    if (!iframe) return

    let cleanupDocument: (() => void) | undefined

    const bindDocument = () => {
      cleanupDocument?.()
      const doc = iframe.contentDocument
      if (!doc) return

      const onClick = (event: MouseEvent) => {
        const target = event.target
        if (!(target instanceof Element)) return
        const anchor = target.closest<HTMLAnchorElement>('a[href]')
        if (!anchor) return

        handleAuthenticatedSourceClick(event, anchor.href).catch((error) => {
          console.warn('Failed to open authenticated source link', error)
        })
      }

      doc.addEventListener('click', onClick)
      cleanupDocument = () => doc.removeEventListener('click', onClick)
    }

    iframe.addEventListener('load', bindDocument)
    bindDocument()

    return () => {
      iframe.removeEventListener('load', bindDocument)
      cleanupDocument?.()
    }
  }, [contentLoading, reportSrcDoc])

  return (
    <div className="secondary-panel overflow-hidden">
      {selectedReport && (
        <div className="border-b border-border bg-card">
          <div className="flex flex-col gap-4 px-5 py-4 md:flex-row md:items-center md:justify-between">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`h-2.5 w-2.5 rounded-full bg-gradient-to-br ${accent}`} />
                <p className="truncate text-base font-bold text-text">{selectedReport.filename}</p>
                <span className="secondary-status">{updatedAt}</span>
              </div>
              <p className="mt-1 text-sm text-text-muted">已套用 SIQ 阅读样式，可在右侧助手中追问结论、数据来源和风险点。</p>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              <span className="secondary-status secondary-status-info">{Math.max(1, Math.round(selectedReport.size / 1024))} KB</span>
              <span className="secondary-status secondary-status-success">已加载</span>
            </div>
          </div>
        </div>
      )}
      {contentLoading ? (
        <div className="flex items-center justify-center px-5 py-20 text-text-muted">
          <Loader2 className="mr-3 h-6 w-6 animate-spin text-primary" />
          正在加载报告内容...
        </div>
      ) : (
        <iframe
          ref={iframeRef}
          key={selectedReportUrl}
          sandbox="allow-same-origin allow-popups allow-downloads"
          referrerPolicy="no-referrer"
          srcDoc={reportSrcDoc}
          className="block w-full max-w-full border-none bg-white h-[min(70dvh,520px)] min-h-[320px] md:h-[min(78dvh,960px)] md:min-h-[600px]"
          style={{ border: 'none', display: 'block', background: '#fff' }}
          title={iframeTitle}
        />
      )}
    </div>
  )
}
