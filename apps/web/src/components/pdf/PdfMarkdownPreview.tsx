import { Copy, Download, FileText } from 'lucide-react'
import type { RefObject } from 'react'
import { copyText } from '../../lib/clipboard'
import { getDownloadUrl } from '../../features/pdf-parsing/api'
import { downloadAuthenticatedFile } from '../../lib/authenticatedFiles'

export interface PdfMarkdownPreviewProps {
  markdown: string
  mdLines: string[]
  focusedLine: number | null
  taskId: string | null
  mdRef: RefObject<HTMLDivElement | null>
  showToast: (msg: string) => void
}

export function PdfMarkdownPreview({ markdown, mdLines, focusedLine, taskId, mdRef, showToast }: PdfMarkdownPreviewProps) {
  if (!markdown) return null
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
      <div className="pdf-markdown-body" ref={mdRef}>
        {mdLines.length ? (
          mdLines.map((line, index) => {
            const lineNo = index + 1
            return (
              <div key={lineNo} data-line={lineNo} className={`pdf-markdown-line ${focusedLine === lineNo ? 'is-focused' : ''}`}>
                <span className="pdf-markdown-line-number">{lineNo}</span>
                <span>{line || ' '}</span>
              </div>
            )
          })
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
