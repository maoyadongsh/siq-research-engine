import { Link } from 'react-router-dom'
import { ExternalLink, FileText, FolderOpen, Loader2, Play, RefreshCw, Search, Trash2 } from 'lucide-react'
import { EmptyState } from '@/components/page'
import { formatBytes, parsePathForDownloadedReport, type DownloadedPdf } from './model'

function formatDateTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function DownloadedReportsPanel({
  reports,
  loading,
  query,
  confirmDeletePath,
  deletingPath,
  onQueryChange,
  onRefresh,
  onOpen,
  onRequestDelete,
  onConfirmDelete,
  onCancelDelete,
}: {
  reports: DownloadedPdf[]
  loading: boolean
  query: string
  confirmDeletePath: string
  deletingPath: string
  onQueryChange: (value: string) => void
  onRefresh: () => void
  onOpen: (report: DownloadedPdf) => void
  onRequestDelete: (path: string) => void
  onConfirmDelete: (report: DownloadedPdf) => void
  onCancelDelete: () => void
}) {
  return (
    <div className="apple-card rounded-[var(--radius-panel)] p-4 sm:p-6">
      <div className="mb-5 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-text sm:text-xl">
            <FolderOpen className="h-5 w-5 text-primary" />
            已下载财报文件
          </h2>
          <p className="mt-1 text-sm leading-6 text-text-muted sm:text-base">来自本地 downloads 目录，PDF 可进入解析；HTML/iXBRL 可在浏览器新标签中打开查看。</p>
        </div>
        <div className="grid w-full gap-2 sm:grid-cols-[auto_minmax(240px,320px)_auto] sm:items-center lg:w-auto">
          <label htmlFor="downloaded-report-query" className="text-sm font-semibold leading-5 text-text-muted sm:whitespace-nowrap">
            搜索公司或文件名
          </label>
          <div className="relative min-w-0">
            <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
            <input
              id="downloaded-report-query"
              type="search"
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') onRefresh()
              }}
              placeholder="输入公司或文件名"
              className="form-control h-10 min-h-10 w-full rounded-xl py-0 pl-10 pr-3 text-sm"
            />
          </div>
          <button
            onClick={onRefresh}
            disabled={loading}
            className="inline-flex h-10 min-w-[96px] shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text shadow-sm hover:bg-bg disabled:opacity-60"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            刷新
          </button>
        </div>
      </div>
      {loading ? (
        <div className="flex justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-primary" />
        </div>
      ) : reports.length === 0 ? (
        <EmptyState
          icon={FolderOpen}
          title="暂无已下载财报文件"
          description="完成上方下载后，这里会自动汇总本地文件。"
          className="border-dashed"
        />
      ) : (
        <div className="divide-y divide-border overflow-hidden rounded-2xl border border-border">
          {reports.map((report) => {
            const isPdf = report.isPdf !== false
            const actionGridColumns = confirmDeletePath === report.relativePath ? 'repeat(2, minmax(0, 1fr))' : 'repeat(3, minmax(0, 1fr))'
            return (
              <div
                key={report.id}
                className="content-auto group flex flex-col gap-3 bg-card px-4 py-4 transition-colors hover:bg-primary/[0.035] sm:flex-row sm:items-center sm:gap-4 sm:px-5"
              >
                <button
                  type="button"
                  onClick={() => onOpen(report)}
                  className="flex min-w-0 flex-1 items-start gap-3 text-left sm:items-center sm:gap-4"
                >
                  <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                    <FileText className="h-5 w-5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block break-words text-sm font-semibold leading-6 text-text sm:truncate sm:text-base">{report.filename}</span>
                    <span className="mt-1 block break-words text-xs leading-5 text-text-muted sm:truncate sm:text-sm">{report.company} · {report.category} · {report.relativePath}</span>
                    {!isPdf ? <span className="mt-1 block text-xs font-semibold text-warning">非 PDF 文件：{report.contentType || 'HTML/iXBRL'}</span> : null}
                  </span>
                </button>
                <span className="hidden shrink-0 text-right text-sm text-text-muted md:block">
                  <span className="block font-mono">{formatBytes(report.size)}</span>
                  <span className="mt-1 block">{formatDateTime(report.mtime)}</span>
                </span>
                <div
                  className="grid gap-2 sm:flex sm:shrink-0 sm:items-center"
                  style={{ gridTemplateColumns: actionGridColumns }}
                >
                  {isPdf ? (
                    <Link
                      to={parsePathForDownloadedReport(report.relativePath)}
                      className="flex h-10 min-w-0 w-full items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-border px-2.5 text-sm font-semibold text-text-muted transition-colors hover:bg-primary/10 hover:text-primary sm:w-10 sm:border-0 sm:px-0"
                      aria-label="解析 PDF"
                    >
                      <Play className="h-5 w-5" />
                      <span className="sm:hidden">解析</span>
                    </Link>
                  ) : (
                    <button
                      type="button"
                      className="flex h-10 min-w-0 w-full cursor-not-allowed items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-border px-2.5 text-sm font-semibold text-text-muted opacity-45 sm:w-10 sm:border-0 sm:px-0"
                      disabled
                      title="该文件不是 PDF，暂不能送入 PDF 解析器"
                      aria-label="非 PDF 暂不能解析"
                    >
                      <Play className="h-5 w-5" />
                      <span className="sm:hidden">解析</span>
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => onOpen(report)}
                    className="flex h-10 min-w-0 w-full items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-border px-2.5 text-sm font-semibold text-text-muted transition-colors hover:bg-primary/10 hover:text-primary sm:w-10 sm:border-0 sm:px-0"
                    aria-label="打开文件"
                  >
                    <ExternalLink className="h-5 w-5" />
                    <span className="sm:hidden">打开</span>
                  </button>
                  {confirmDeletePath === report.relativePath ? (
                    <>
                      <button
                        type="button"
                        onClick={() => onConfirmDelete(report)}
                        disabled={deletingPath === report.relativePath}
                        className="inline-flex h-10 w-full min-w-0 items-center justify-center whitespace-nowrap rounded-xl bg-error px-2.5 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-60"
                      >
                        {deletingPath === report.relativePath ? <Loader2 className="h-4 w-4 animate-spin" /> : '确认'}
                      </button>
                      <button
                        type="button"
                        onClick={onCancelDelete}
                        disabled={Boolean(deletingPath)}
                        className="inline-flex h-10 w-full min-w-0 items-center justify-center whitespace-nowrap rounded-xl border border-border bg-card px-2.5 text-sm font-semibold text-text hover:bg-bg disabled:opacity-60"
                      >
                        取消
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={() => onRequestDelete(report.relativePath)}
                      className="flex h-10 min-w-0 w-full items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-border px-2.5 text-sm font-semibold text-text-muted transition-colors hover:bg-error/10 hover:text-error sm:w-10 sm:border-0 sm:px-0"
                      aria-label="删除 PDF"
                    >
                      <Trash2 className="h-5 w-5" />
                      <span className="sm:hidden">删除</span>
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
