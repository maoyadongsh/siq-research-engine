import { Copy, Download, FileText } from 'lucide-react'
import { type CSSProperties, type RefObject, type UIEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { copyText } from '../../lib/clipboard'
import { getDownloadUrl } from '../../features/pdf-parsing/api'
import { downloadAuthenticatedFile } from '../../lib/authenticatedFiles'
import { shouldWindowPdfMarkdownLines } from './pdfMarkdownWindowing'

export interface PdfMarkdownPreviewProps {
  markdown: string
  mdLines: string[]
  focusedLine: number | null
  taskId: string | null
  mdRef: RefObject<HTMLDivElement | null>
  showToast: (msg: string) => void
}

const VIRTUAL_LINE_THRESHOLD = 900
const VIRTUAL_LINE_HEIGHT = 22
const VIRTUAL_WINDOW_LINES = 180
const VIRTUAL_OVERSCAN_LINES = 60

function clampLine(value: number, total: number) {
  return Math.max(1, Math.min(total || 1, value))
}

export function PdfMarkdownPreview({ markdown, mdLines, focusedLine, taskId, mdRef, showToast }: PdfMarkdownPreviewProps) {
  const [anchorLine, setAnchorLine] = useState(1)
  const useWindowedLines = useMemo(
    () => shouldWindowPdfMarkdownLines(mdLines, VIRTUAL_LINE_THRESHOLD),
    [mdLines],
  )

  useEffect(() => {
    const frame = requestAnimationFrame(() => setAnchorLine(1))
    return () => cancelAnimationFrame(frame)
  }, [markdown])

  useEffect(() => {
    if (!focusedLine || !useWindowedLines) return
    const frame = requestAnimationFrame(() => setAnchorLine(clampLine(focusedLine, mdLines.length)))
    return () => cancelAnimationFrame(frame)
  }, [focusedLine, mdLines.length, useWindowedLines])

  const visibleRange = useMemo(() => {
    if (!useWindowedLines) return { startLine: 1, endLine: mdLines.length }
    const halfWindow = Math.floor(VIRTUAL_WINDOW_LINES / 2)
    const baseLine = clampLine(anchorLine, mdLines.length)
    const startLine = clampLine(baseLine - halfWindow - VIRTUAL_OVERSCAN_LINES, mdLines.length)
    const endLine = clampLine(baseLine + halfWindow + VIRTUAL_OVERSCAN_LINES, mdLines.length)
    return { startLine, endLine }
  }, [anchorLine, mdLines.length, useWindowedLines])

  const visibleLines = useMemo(
    () => mdLines.slice(visibleRange.startLine - 1, visibleRange.endLine),
    [mdLines, visibleRange.endLine, visibleRange.startLine],
  )

  const handleScroll = useCallback(
    (event: UIEvent<HTMLDivElement>) => {
      if (!useWindowedLines) return
      const nextAnchor = clampLine(Math.floor(event.currentTarget.scrollTop / VIRTUAL_LINE_HEIGHT) + 1, mdLines.length)
      setAnchorLine((current) => (Math.abs(current - nextAnchor) > 12 ? nextAnchor : current))
    },
    [mdLines.length, useWindowedLines],
  )

  useEffect(() => {
    if (!focusedLine) return
    const frame = requestAnimationFrame(() => {
      mdRef.current?.querySelector(`[data-line="${focusedLine}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })
    return () => cancelAnimationFrame(frame)
  }, [focusedLine, mdRef, visibleRange.endLine, visibleRange.startLine])

  if (!markdown) return null
  const topSpacerStyle: CSSProperties = useWindowedLines
    ? { height: `${(visibleRange.startLine - 1) * VIRTUAL_LINE_HEIGHT}px` }
    : { height: 0 }
  const bottomSpacerStyle: CSSProperties = useWindowedLines
    ? { height: `${Math.max(0, mdLines.length - visibleRange.endLine) * VIRTUAL_LINE_HEIGHT}px` }
    : { height: 0 }

  return (
    <div className="apple-card rounded-[24px] p-4 sm:p-6">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-text">Markdown 结果</h3>
          {taskId ? <p className="text-xs text-text-muted">任务 {taskId}</p> : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="pdf-small-action inline-flex items-center gap-1"
            onClick={() => {
              void copyText(markdown)
              showToast('Markdown 已复制')
            }}
          >
            <Copy className="h-4 w-4" />
            复制全文
          </button>
          {taskId ? (
            <>
              <button
                type="button"
                className="pdf-small-action inline-flex items-center gap-1"
                onClick={() => {
                  downloadAuthenticatedFile(getDownloadUrl(taskId, 'raw')).catch(() => showToast('下载失败'))
                }}
              >
                <Download className="h-4 w-4" />
                原始 MD
              </button>
              <button
                type="button"
                className="pdf-small-action inline-flex items-center gap-1"
                onClick={() => {
                  downloadAuthenticatedFile(getDownloadUrl(taskId, 'complete')).catch(() => showToast('下载失败'))
                }}
              >
                <Download className="h-4 w-4" />
                增强版 MD
              </button>
              <button
                type="button"
                className="pdf-small-action primary inline-flex items-center gap-1"
                onClick={() => {
                  downloadAuthenticatedFile(getDownloadUrl(taskId, 'corrected')).catch(() => showToast('下载失败'))
                }}
              >
                <Download className="h-4 w-4" />
                修正版 MD
              </button>
            </>
          ) : null}
        </div>
      </div>
      <div className="pdf-markdown-body" ref={mdRef} onScroll={handleScroll} data-windowed={useWindowedLines ? 'true' : 'false'}>
        {mdLines.length ? (
          <>
            {useWindowedLines ? <div aria-hidden="true" style={topSpacerStyle} /> : null}
            {visibleLines.map((line, index) => {
              const lineNo = visibleRange.startLine + index
              return (
                <div key={lineNo} data-line={lineNo} className={`pdf-markdown-line ${focusedLine === lineNo ? 'is-focused' : ''}`}>
                  <span className="pdf-markdown-line-number">{lineNo}</span>
                  <span>{line || ' '}</span>
                </div>
              )
            })}
            {useWindowedLines ? <div aria-hidden="true" style={bottomSpacerStyle} /> : null}
          </>
        ) : (
          <div className="flex items-center gap-2 text-text-muted">
            <FileText className="h-4 w-4" />
            暂无 Markdown 内容
          </div>
        )}
      </div>
    </div>
  )
}
