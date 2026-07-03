import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, FileJson, FileText, Loader2, RefreshCw, Search } from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { fetchDealReport, fetchDealReports } from '@/lib/dealApi'
import type { DealReportDetailResponse, DealReportMeta, DealReportsResponse } from '@/lib/dealTypes'

function text(value: unknown, fallback = '未记录') {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

function statusTone(status?: string | null): 'neutral' | 'info' | 'success' | 'warning' | 'error' {
  if (status === 'available') return 'success'
  if (status === 'missing') return 'warning'
  if (status === 'error') return 'error'
  return 'neutral'
}

function formatBytes(value?: number | null) {
  if (!value || value < 0) return '0 B'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function formatTime(value?: string | null) {
  if (!value) return '未记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function reportKey(report: DealReportMeta) {
  return report.path || `${report.category}-${report.title}`
}

function reportMatches(report: DealReportMeta, query: string, category: string) {
  const haystack = `${report.path} ${report.title || ''} ${report.category || ''} ${report.format || ''}`.toLowerCase()
  const matchesQuery = !query || haystack.includes(query.toLowerCase())
  const matchesCategory = !category || report.category === category
  return matchesQuery && matchesCategory
}

function renderContent(detail: DealReportDetailResponse | null) {
  if (!detail) return null
  const format = String(detail.report?.format || '').toLowerCase()
  if (format === 'json' && detail.json !== undefined) {
    return JSON.stringify(detail.json, null, 2)
  }
  if (format === 'ndjson' && detail.rows_preview) {
    return JSON.stringify(detail.rows_preview, null, 2)
  }
  return detail.content || ''
}

export default function DealReports() {
  const { dealId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedPath = searchParams.get('path') || ''
  const [data, setData] = useState<DealReportsResponse | null>(null)
  const [detail, setDetail] = useState<DealReportDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState('')
  const [detailError, setDetailError] = useState('')
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      setLoading(true)
      setError('')
      try {
        setData(await fetchDealReports(dealId, controller.signal))
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : '报告索引加载失败')
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    })()
    return () => controller.abort()
  }, [dealId])

  useEffect(() => {
    if (!selectedPath) {
      return
    }
    const controller = new AbortController()
    void (async () => {
      setDetailLoading(true)
      setDetailError('')
      try {
        setDetail(await fetchDealReport(dealId, selectedPath, controller.signal))
      } catch (err) {
        if (!controller.signal.aborted) {
          setDetail(null)
          setDetailError(err instanceof Error ? err.message : '报告读取失败')
        }
      } finally {
        if (!controller.signal.aborted) setDetailLoading(false)
      }
    })()
    return () => controller.abort()
  }, [dealId, selectedPath])

  const reports = useMemo(() => (Array.isArray(data?.reports) ? data.reports : []), [data])
  const missingReports = useMemo(() => (Array.isArray(data?.missing_expected) ? data.missing_expected : []), [data])
  const categories = useMemo(() => {
    const values = new Set<string>()
    ;[...reports, ...missingReports].forEach((report) => {
      if (report.category) values.add(report.category)
    })
    return Array.from(values).sort((a, b) => a.localeCompare(b))
  }, [missingReports, reports])
  const filteredReports = useMemo(
    () => reports.filter((report) => reportMatches(report, query.trim(), category)),
    [category, query, reports],
  )
  const filteredMissing = useMemo(
    () => missingReports.filter((report) => reportMatches(report, query.trim(), category)),
    [category, missingReports, query],
  )
  const activeDetail = selectedPath ? detail : null
  const activeDetailError = selectedPath ? detailError : ''
  const renderedContent = renderContent(activeDetail)

  const selectReport = (path: string) => {
    const params = new URLSearchParams(searchParams)
    params.set('path', path)
    setSearchParams(params)
  }

  const refresh = () => {
    setData(null)
    setLoading(true)
    void fetchDealReports(dealId)
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : '报告索引加载失败'))
      .finally(() => setLoading(false))
  }

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={FileText}
        eyebrow="Deal Reports"
        title="报告与产物索引"
        description="查看项目包内 workflow、discussion、decision、evidence 和 audit 产物。"
        actions={
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="secondary">
              <Link to={`/deals/${encodeURIComponent(dealId)}`}>
                <ArrowLeft />
                返回项目
              </Link>
            </Button>
            <Button type="button" variant="secondary" onClick={refresh} disabled={loading}>
              {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              刷新
            </Button>
          </div>
        }
      />

      {error ? (
        <PageSection>
          <EmptyState title="报告索引加载失败" description={error} />
        </PageSection>
      ) : loading ? (
        <div className="grid gap-3">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="h-20 animate-pulse rounded-lg bg-muted/60" />
          ))}
        </div>
      ) : !data ? (
        <PageSection>
          <EmptyState title="暂无报告索引" description="项目包中没有可读取的 reports index。" />
        </PageSection>
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-4">
            <Surface kind="card">
              <p className="text-sm text-text-muted">Available</p>
              <p className="mt-1 text-2xl font-semibold text-text">{data.counts?.reports ?? reports.length}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">Expected</p>
              <p className="mt-1 text-2xl font-semibold text-text">{data.counts?.expected ?? 0}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">Missing</p>
              <p className="mt-1 text-2xl font-semibold text-text">{data.counts?.missing_expected ?? missingReports.length}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">Schema</p>
              <p className="mt-1 break-all text-sm font-semibold text-text">{text(data.schema_version)}</p>
            </Surface>
          </div>

          <Surface kind="card">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
              <div className="min-w-0 flex-1">
                <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-text-muted">Search</p>
                <div className="relative">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
                  <Input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    className="pl-9"
                    placeholder="搜索路径、标题、分类"
                  />
                </div>
              </div>
              <label className="min-w-56">
                <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-text-muted">Category</span>
                <select
                  value={category}
                  onChange={(event) => setCategory(event.target.value)}
                  className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm text-text shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
                >
                  <option value="">全部分类</option>
                  {categories.map((value) => (
                    <option key={value} value={value}>{value}</option>
                  ))}
                </select>
              </label>
            </div>
          </Surface>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.35fr)]">
            <PageSection title="可用报告" description={`${filteredReports.length} 个文件`}>
              {filteredReports.length ? (
                <div className="grid gap-3">
                  {filteredReports.map((report) => {
                    const selected = report.path === selectedPath
                    return (
                      <button
                        key={reportKey(report)}
                        type="button"
                        onClick={() => selectReport(report.path)}
                        className={`rounded-lg border p-3 text-left transition ${selected ? 'border-primary/45 bg-primary/5' : 'border-border bg-card hover:border-primary/30 hover:bg-bg'}`}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="break-all text-sm font-semibold text-text">{text(report.title, report.path)}</p>
                            <p className="mt-1 break-all font-mono text-xs text-text-muted">{report.path}</p>
                          </div>
                          <StatusBadge tone={statusTone(report.status)}>{text(report.status, 'available')}</StatusBadge>
                        </div>
                        <div className="mt-3 flex flex-wrap gap-2 text-xs text-text-muted">
                          <span>{text(report.category)}</span>
                          <span>{text(report.format)}</span>
                          <span>{formatBytes(report.size_bytes)}</span>
                          <span>{formatTime(report.updated_at)}</span>
                        </div>
                      </button>
                    )
                  })}
                </div>
              ) : (
                <EmptyState title="没有匹配的报告" description="可以调整搜索词或分类筛选。" size="sm" />
              )}
            </PageSection>

            <PageSection
              title="报告预览"
              description={activeDetail?.report?.path || selectedPath || '选择左侧报告后查看内容'}
              actions={activeDetail ? <StatusBadge tone={statusTone(activeDetail.report.status)}>{text(activeDetail.report.format)}</StatusBadge> : null}
            >
              {activeDetailError ? (
                <EmptyState title="报告读取失败" description={activeDetailError} size="sm" />
              ) : detailLoading ? (
                <div className="h-48 animate-pulse rounded-lg bg-muted/60" />
              ) : activeDetail ? (
                <div className="space-y-3">
                  <div className="grid gap-2 text-xs text-text-muted sm:grid-cols-2">
                    <p className="break-all">Path: {activeDetail.report.path}</p>
                    <p className="break-all">SHA256: {text(activeDetail.report.sha256)}</p>
                    <p>Size: {formatBytes(activeDetail.report.size_bytes)}</p>
                    <p>Updated: {formatTime(activeDetail.report.updated_at)}</p>
                  </div>
                  {activeDetail.invalid_lines ? (
                    <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-text">
                      NDJSON 有 {activeDetail.invalid_lines} 行无法解析，已跳过。
                    </div>
                  ) : null}
                  {activeDetail.parse_error ? (
                    <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-text">
                      {activeDetail.parse_error}
                    </div>
                  ) : null}
                  {typeof renderedContent === 'string' ? (
                    <pre className="max-h-[720px] whitespace-pre-wrap overflow-auto rounded-lg bg-muted/60 p-4 text-sm leading-6 text-text">
                      {renderedContent}
                    </pre>
                  ) : renderedContent}
                </div>
              ) : (
                <EmptyState icon={FileJson} title="选择报告" description="从左侧列表打开 JSON、Markdown 或证据产物。" size="sm" />
              )}
            </PageSection>
          </div>

          <PageSection title="缺失的预期产物" description={`${filteredMissing.length} 个 missing item`}>
            {filteredMissing.length ? (
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {filteredMissing.map((report) => (
                  <Surface key={reportKey(report)} kind="muted" padding="sm">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-text">{text(report.title, report.path)}</p>
                        <p className="mt-1 break-all font-mono text-xs text-text-muted">{report.path}</p>
                      </div>
                      <StatusBadge tone="warning">missing</StatusBadge>
                    </div>
                  </Surface>
                ))}
              </div>
            ) : (
              <EmptyState title="预期产物已齐备" description="当前筛选下没有缺失项。" size="sm" />
            )}
          </PageSection>
        </>
      )}
    </PageShell>
  )
}
