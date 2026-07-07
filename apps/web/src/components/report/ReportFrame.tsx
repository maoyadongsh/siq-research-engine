import { Loader2, RefreshCw } from 'lucide-react'
import { useEffect, useRef } from 'react'
import type { ReportItem } from '@/lib/reportTypes'
import { handleAuthenticatedSourceClick, isAuthenticatedSourceLink, openAuthenticatedSourceLink } from '@/lib/authenticatedSourceLinks'
import { REPORT_IFRAME_SANDBOX, isReportSourceLinkMessage } from './reportFrameSandbox'

interface ReportFrameProps {
  selectedReportUrl: string
  selectedReport: ReportItem | undefined
  reportSrcDoc: string
  contentLoading: boolean
  iframeTitle: string
  updatedAt: string
  accent: string
  error?: string | null
  onRetry?: () => void
}

export default function ReportFrame({
  selectedReportUrl,
  selectedReport,
  reportSrcDoc,
  contentLoading,
  iframeTitle,
  updatedAt,
  accent,
  error,
  onRetry,
}: ReportFrameProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)

  useEffect(() => {
    if (contentLoading || !reportSrcDoc) return

    const onMessage = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) return
      if (!isReportSourceLinkMessage(event.data)) return
      if (!isAuthenticatedSourceLink(event.data.href)) return
      openAuthenticatedSourceLink(event.data.href).catch((error) => {
        console.warn('Failed to open authenticated source link from report frame', error)
      })
    }

    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [contentLoading, reportSrcDoc])

  useEffect(() => {
    if (contentLoading || !reportSrcDoc) return
    const iframe = iframeRef.current
    if (!iframe) return

    let cleanupDocument: (() => void) | undefined

    const bindDocument = () => {
      cleanupDocument?.()
      let doc: Document | null = null
      try {
        doc = iframe.contentDocument
      } catch {
        return
      }
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
    <div className="surface-panel overflow-hidden">
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
        <div className="space-y-4 px-5 py-10">
          <div className="flex items-center gap-2 text-text-muted">
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
            <span className="text-sm font-medium">正在加载报告内容...</span>
          </div>
          <div className="h-4 w-1/3 animate-pulse rounded-lg bg-border" />
          <div className="h-48 animate-pulse rounded-xl bg-border" />
          <div className="h-4 w-2/3 animate-pulse rounded-lg bg-border" />
          <div className="h-4 w-1/2 animate-pulse rounded-lg bg-border" />
        </div>
      ) : error ? (
        <div className="flex flex-col items-center justify-center px-5 py-16 text-center">
          <p className="text-sm font-semibold text-error">报告内容加载失败</p>
          <p className="mt-1 max-w-md text-sm text-text-muted">{error}</p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-4 inline-flex h-10 items-center gap-2 rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text shadow-sm hover:bg-bg"
            >
              <RefreshCw className="h-4 w-4" />
              重试
            </button>
          )}
        </div>
      ) : (
        <iframe
          ref={iframeRef}
          key={selectedReportUrl}
          sandbox={REPORT_IFRAME_SANDBOX}
          referrerPolicy="no-referrer"
          srcDoc={reportSrcDoc}
          className="block w-full max-w-full border-none bg-white h-[min(72dvh,560px)] min-h-[360px] md:h-[min(80dvh,1020px)] md:min-h-[640px]"
          style={{ border: 'none', display: 'block', background: '#fff' }}
          title={iframeTitle}
        />
      )}
    </div>
  )
}
