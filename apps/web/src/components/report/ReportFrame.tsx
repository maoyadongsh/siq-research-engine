import { Loader2, RefreshCw } from 'lucide-react'
import { useEffect, useRef } from 'react'
import type { ReportItem } from '@/lib/reportTypes'
import { handleAuthenticatedSourceClick, isAuthenticatedSourceLink, openAuthenticatedSourceLink } from '@/lib/authenticatedSourceLinks'
import { REPORT_IFRAME_SANDBOX, isReportFrameHeightMessage, isReportSourceLinkMessage } from './reportFrameSandbox'

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

function renderInlineMarkdown(doc: Document) {
  const walker = doc.createTreeWalker(doc.body, 4)
  const nodes: Text[] = []
  let current = walker.nextNode()
  while (current) {
    const parent = current.parentElement
    if (parent && !parent.closest('script,style,pre,code,textarea')) nodes.push(current as Text)
    current = walker.nextNode()
  }

  nodes.forEach((node) => {
    const source = node.data
    const heading = source.match(/^(\s*)#{1,6}\s+(.+)$/)
    if (heading) {
      const fragment = doc.createDocumentFragment()
      fragment.append(heading[1])
      const strong = doc.createElement('strong')
      strong.className = 'siq-md-inline-heading'
      strong.textContent = heading[2]
      fragment.append(strong)
      node.replaceWith(fragment)
      return
    }

    if (!/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/.test(source)) return
    const fragment = doc.createDocumentFragment()
    const pattern = /(\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/g
    let cursor = 0
    for (const match of source.matchAll(pattern)) {
      const index = match.index || 0
      if (index > cursor) fragment.append(source.slice(cursor, index))
      if (match[2]) {
        const strong = doc.createElement('strong')
        strong.textContent = match[2]
        fragment.append(strong)
      } else if (match[3]) {
        const code = doc.createElement('code')
        code.textContent = match[3]
        fragment.append(code)
      } else {
        const anchor = doc.createElement('a')
        anchor.textContent = match[4]
        anchor.href = match[5]
        fragment.append(anchor)
      }
      cursor = index + match[0].length
    }
    if (cursor < source.length) fragment.append(source.slice(cursor))
    node.replaceWith(fragment)
  })
}

export default function ReportFrame({
  selectedReportUrl,
  selectedReport,
  reportSrcDoc,
  contentLoading,
  iframeTitle,
  accent,
  error,
  onRetry,
}: ReportFrameProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)

  useEffect(() => {
    if (contentLoading || !reportSrcDoc) return
    const onMessage = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) return
      if (isReportFrameHeightMessage(event.data)) {
        const height = Math.min(Math.max(Math.ceil(event.data.height), 360), 100000)
        iframeRef.current.style.height = `${height}px`
        return
      }
      if (!isReportSourceLinkMessage(event.data) || !isAuthenticatedSourceLink(event.data.href)) return
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

      renderInlineMarkdown(doc)
      doc.documentElement.classList.add('siq-report-document')

      const syncHeight = () => {
        const height = Math.max(doc.documentElement.scrollHeight, doc.body.scrollHeight)
        iframe.style.height = `${height + 2}px`
      }
      syncHeight()
      const resizeObserver = new ResizeObserver(syncHeight)
      resizeObserver.observe(doc.documentElement)
      resizeObserver.observe(doc.body)

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
      cleanupDocument = () => {
        doc.removeEventListener('click', onClick)
        resizeObserver.disconnect()
      }
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
        <div className="border-b border-border bg-card px-5 py-4">
          <div className="flex items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full bg-gradient-to-br ${accent}`} />
            <p className="text-base font-bold text-text">预览</p>
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
          className="block min-h-[360px] w-full max-w-full overflow-hidden border-none bg-white"
          style={{ border: 'none', display: 'block', background: '#fff', overflow: 'hidden' }}
          title={iframeTitle}
        />
      )}
    </div>
  )
}
