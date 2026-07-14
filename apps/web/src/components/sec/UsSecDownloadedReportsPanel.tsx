import { useMemo, useState } from 'react'
import { CheckCircle2, FileText, FolderOpen, Loader2, RefreshCw, Search, Upload } from 'lucide-react'
import { EmptyState } from '@/components/page'
import { formatDateTime, formatSize } from '../../lib/pdfFormatting'
import type { UsSecDownloadedRow } from '../../features/market-parsing/usSecWorkbench'

export interface UsSecDownloadedReportsPanelProps {
  rows: UsSecDownloadedRow[]
  query: string
  loading: boolean
  busyPath: string
  selectedPath: string
  onQueryChange: (value: string) => void
  onRefresh: () => Promise<void>
  onSelect: (row: UsSecDownloadedRow) => Promise<void>
  onParse: (row: UsSecDownloadedRow) => Promise<void>
  onUploadClick: () => void
}

const statusText: Record<UsSecDownloadedRow['parseStatus'], string> = {
  unparsed: '未解析',
  building: '解析中',
  package_ready: '解析产物已生成',
  postgres_ready: 'PostgreSQL 已入库',
  stale: 'PostgreSQL 待更新',
  warning: '质量警告',
  failed: '质量失败',
}

const statusClass: Record<UsSecDownloadedRow['parseStatus'], string> = {
  unparsed: '',
  building: 'secondary-status-info',
  package_ready: 'secondary-status-success',
  postgres_ready: 'secondary-status-success',
  stale: 'secondary-status-warning',
  warning: 'secondary-status-warning',
  failed: 'secondary-status-warning',
}

function isStructuredUsDisclosure(row: UsSecDownloadedRow): boolean {
  return row.fileType !== 'PDF'
}

export function UsSecDownloadedReportsPanel({
  rows,
  query,
  loading,
  busyPath,
  selectedPath,
  onQueryChange,
  onRefresh,
  onSelect,
  onParse,
  onUploadClick,
}: UsSecDownloadedReportsPanelProps) {
  const [expanded, setExpanded] = useState(false)
  const sortedRows = useMemo(
    () => [...rows].sort((a, b) => new Date(b.downloadedAt || 0).getTime() - new Date(a.downloadedAt || 0).getTime()),
    [rows],
  )
  const visibleRows = expanded ? sortedRows : sortedRows.slice(0, 5)
  const hasMore = sortedRows.length > visibleRows.length

  return (
    <section className="secondary-panel p-5">
      <div className="pdf-source-choice">
        <div className="pdf-source-choice-head">
          <div>
            <h3 className="flex items-center gap-2">
              <FolderOpen className="h-5 w-5 text-primary" />
              已下载财报
            </h3>
            <p>优先从搜索下载阶段保存的 SEC 披露文件开始；HTML/iXBRL/XML/ZIP 走结构化解析产物生成。</p>
          </div>
          <div className="pdf-download-search">
            <label>
              <Search className="h-4 w-4" />
              <input
                value={query}
                onChange={(event) => onQueryChange(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void onRefresh()
                }}
                placeholder="搜索公司、ticker、form 或文件名"
              />
            </label>
            <button
              className="pdf-icon-btn"
              onClick={() => void onRefresh()}
              disabled={loading}
              aria-label="刷新已下载财报"
            >
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              <span>刷新</span>
            </button>
            <span className="pdf-download-count">{rows.length ? `${rows.length} 份` : '无结果'}</span>
          </div>
        </div>

        {rows.length ? (
          <>
            <div className="pdf-download-list">
              {visibleRows.map((row) => {
                const busy = busyPath === row.relativePath || row.parseStatus === 'building'
                const selected = selectedPath === row.relativePath
                const canParse = isStructuredUsDisclosure(row)
                return (
                  <div key={row.id} className={`pdf-download-item ${selected ? 'ring-1 ring-primary/30' : ''}`}>
                    <div className="pdf-download-main">
                      <FileText className="h-5 w-5" />
                      <div className="min-w-0">
                        <div className="pdf-download-title">{row.filename}</div>
                        <div className="pdf-download-meta">
                          <span>{row.companyName}</span>
                          {row.ticker ? <span>{row.ticker}</span> : null}
                          {row.form ? <span>{row.form}</span> : null}
                          <span>{row.fileType}</span>
                          <span>{formatSize(row.sizeBytes)}</span>
                          <span>{formatDateTime(row.downloadedAt)}</span>
                        </div>
                      </div>
                    </div>
                    <div className="pdf-download-actions">
                      <span className={`secondary-status ${statusClass[row.parseStatus]}`}>{statusText[row.parseStatus]}</span>
                      <button
                        className="pdf-small-action"
                        onClick={() => void onSelect(row)}
                        disabled={busy}
                      >
                        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                        选择
                      </button>
                      <button
                        className="pdf-small-action primary"
                        onClick={() => void onParse(row)}
                        disabled={busy || !canParse}
                        title={canParse ? '生成 SEC Markdown/JSON 解析产物包' : 'PDF 附件请使用美股 PDF 兼容入口'}
                      >
                        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                        解析
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
            {hasMore ? (
              <button
                type="button"
                onClick={() => setExpanded((value) => !value)}
                className="mt-3 w-full rounded-xl border border-border bg-bg/60 px-3 py-2 text-xs font-semibold leading-5 text-text-muted hover:bg-bg hover:text-text"
              >
                {expanded ? '收起' : '展开'} 已下载财报（{visibleRows.length}/{sortedRows.length}）
              </button>
            ) : null}
          </>
        ) : (
          <EmptyState
            icon={FolderOpen}
            title={loading ? '正在读取已下载财报...' : '暂无已下载财报'}
            description={loading ? '请稍候' : '可先到搜索下载页下载 SEC 披露，或使用下方上传入口。'}
            size="sm"
            className="rounded-[18px] border border-dashed border-border bg-bg/50"
          />
        )}

        <div className="mt-3 flex flex-wrap gap-2">
          <button onClick={onUploadClick} className="pdf-small-action inline-flex items-center gap-1">
            <Upload className="h-4 w-4" />
            上传附件到 US 目录
          </button>
        </div>
      </div>
    </section>
  )
}
