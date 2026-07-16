import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Boxes, ExternalLink, FileSearch, ListChecks, Loader2, PackageCheck, RefreshCw, Search } from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { buildDealEvidence, dryRunDealEvidenceIngest, fetchDealEvidence } from '@/lib/dealApi'
import type { DealEvidenceFilters, DealEvidenceIngestDryRun, DealEvidenceItem, DealEvidenceQualityReport, DealEvidenceResponse } from '@/lib/dealTypes'

function text(value: unknown, fallback = '未记录') {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function statusTone(status?: string | null): 'neutral' | 'info' | 'success' | 'warning' | 'error' {
  const value = String(status || '').toLowerCase()
  if (!value) return 'neutral'
  if (['ready', 'pass', 'passed', 'verified', 'complete', 'completed', 'success'].includes(value)) return 'success'
  if (['fail', 'failed', 'error', 'blocked'].includes(value)) return 'error'
  if (['warn', 'warning', 'partial', 'missing'].includes(value)) return 'warning'
  return 'info'
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

function formatConfidence(value?: number | string | null) {
  if (value === null || value === undefined || value === '') return '未记录'
  const numeric = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numeric)) return String(value)
  if (numeric >= 0 && numeric <= 1) return `${Math.round(numeric * 100)}%`
  return String(value)
}

function coverageText(report?: DealEvidenceQualityReport) {
  const covered = report?.dimensions?.length || 0
  const missing = report?.missing_dimensions?.length || 0
  return `${covered}/${covered + missing}`
}

function sortedDimensions(report?: DealEvidenceQualityReport) {
  return [...(report?.dimensions || [])].sort((a, b) => a.localeCompare(b))
}

function sortedMissingDimensions(report?: DealEvidenceQualityReport) {
  return [...(report?.missing_dimensions || [])].sort((a, b) => a.localeCompare(b))
}

function itemKey(item: DealEvidenceItem, index: number) {
  return item.evidence_id || `${item.document_id}-${item.dimension}-${index}`
}

function stringValue(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}

function uniqueStrings(values: Array<string | null | undefined>) {
  return Array.from(new Set(values.map((value) => value?.trim()).filter(Boolean) as string[])).sort((a, b) => a.localeCompare(b))
}

function documentLabel(document: Record<string, unknown>, fallback: string) {
  return stringValue(document.filename)
    || stringValue(document.original_filename)
    || stringValue(document.title)
    || stringValue(document.label)
    || fallback
}

function sourceLinks(item: DealEvidenceItem) {
  return [
    { label: 'Source', href: item.source_url },
    { label: 'Artifact', href: item.artifact_url },
    { label: 'Parser', href: item.parser_page_url },
  ].filter((link): link is { label: string; href: string } => Boolean(link.href?.trim()))
}

function jsonPreview(value: unknown) {
  if (value === null || value === undefined || value === '') return '未返回'
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function unknownList(value: unknown) {
  return Array.isArray(value) ? value : []
}

export default function DealEvidence() {
  const { dealId = '' } = useParams()
  const [data, setData] = useState<DealEvidenceResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [building, setBuilding] = useState(false)
  const [buildError, setBuildError] = useState('')
  const [dryRun, setDryRun] = useState<DealEvidenceIngestDryRun | null>(null)
  const [dryRunLoading, setDryRunLoading] = useState(false)
  const [dryRunError, setDryRunError] = useState('')
  const [query, setQuery] = useState('')
  const [dimension, setDimension] = useState('')
  const [documentId, setDocumentId] = useState('')
  const [limit, setLimit] = useState('20')

  const evidenceFilters = useMemo<DealEvidenceFilters>(() => ({
    q: query,
    dimension,
    document_id: documentId,
    limit,
  }), [dimension, documentId, limit, query])

  const loadEvidence = useCallback(async (filters: DealEvidenceFilters = evidenceFilters, signal?: AbortSignal) => {
    setLoading(true)
    setError('')
    try {
      setData(await fetchDealEvidence(dealId, filters, signal))
    } catch (err) {
      if (!signal?.aborted) {
        setError(err instanceof Error ? err.message : '证据包加载失败')
      }
    } finally {
      if (!signal?.aborted) {
        setLoading(false)
      }
    }
  }, [dealId, evidenceFilters])

  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      void loadEvidence(evidenceFilters, controller.signal)
    }, 250)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [evidenceFilters, loadEvidence])

  const refresh = () => {
    void loadEvidence(evidenceFilters)
  }

  const handleBuild = async () => {
    setBuilding(true)
    setBuildError('')
    try {
      setData(await buildDealEvidence(dealId))
      setError('')
      await loadEvidence(evidenceFilters)
    } catch (err) {
      setBuildError(err instanceof Error ? err.message : '证据包构建失败')
    } finally {
      setBuilding(false)
    }
  }

  const handleDryRun = async () => {
    setDryRunLoading(true)
    setDryRunError('')
    try {
      const response = await dryRunDealEvidenceIngest(dealId)
      setDryRun(response.ingest_dry_run)
    } catch (err) {
      setDryRunError(err instanceof Error ? err.message : 'Evidence ingest dry-run 失败')
    } finally {
      setDryRunLoading(false)
    }
  }

  const report = data?.quality_report
  const items = useMemo(() => (Array.isArray(data?.items_preview) ? data.items_preview : []), [data])
  const dimensions = useMemo(() => sortedDimensions(report), [report])
  const missingDimensions = useMemo(() => sortedMissingDimensions(report), [report])
  const warnings = Array.isArray(report?.warnings) ? report.warnings : []
  const dryRunCounts = dryRun?.counts && typeof dryRun.counts === 'object' ? Object.entries(dryRun.counts) : []
  const dryRunWarnings = unknownList(dryRun?.warnings)
  const dryRunErrors = unknownList(dryRun?.errors)
  const postgresRowsPreview = unknownList(dryRun?.postgres_rows_preview)
  const milvusChunksPreview = unknownList(dryRun?.milvus_chunks_preview)
  const availableDimensions = useMemo(() => uniqueStrings([
    ...(Array.isArray(data?.available_filters?.dimensions) ? data.available_filters.dimensions : []),
    ...dimensions,
    ...items.map((item) => item.dimension),
  ]), [data, dimensions, items])
  const availableDocuments = useMemo(() => {
    const documents = Array.isArray(data?.available_filters?.documents) ? data.available_filters.documents : []
    const byId = new Map<string, string>()
    documents.forEach((document) => {
      const id = stringValue(document.document_id)
      if (id) byId.set(id, documentLabel(document, id))
    })
    const ids = [
      ...(Array.isArray(data?.available_filters?.document_ids) ? data.available_filters.document_ids : []),
      ...items.map((item) => item.document_id),
    ]
    ids.forEach((id) => {
      const value = id?.trim()
      if (value && !byId.has(value)) byId.set(value, value)
    })
    return Array.from(byId.entries()).sort((a, b) => a[1].localeCompare(b[1]))
  }, [data, items])
  const availableLimits = useMemo(() => uniqueStrings([
    ...(Array.isArray(data?.available_filters?.limits) ? data.available_filters.limits.map(String) : []),
    '10',
    '20',
    '50',
    '100',
  ]), [data])

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={FileSearch}
        eyebrow="Deal Evidence"
        title="Evidence"
        description="查看当前交易项目的证据包质量、维度覆盖和证据预览。"
        actions={
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="secondary">
              <Link to={`/deals/${encodeURIComponent(dealId)}`}>
                <ArrowLeft />
                返回项目
              </Link>
            </Button>
            <Button type="button" onClick={() => void handleBuild()} disabled={building || loading}>
              {building ? <Loader2 className="animate-spin" /> : <PackageCheck />}
              构建证据包
            </Button>
            <Button type="button" variant="secondary" onClick={() => void handleDryRun()} disabled={dryRunLoading || building}>
              {dryRunLoading ? <Loader2 className="animate-spin" /> : <ListChecks />}
              Dry-run 入库计划
            </Button>
          </div>
        }
      />

      {buildError ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          {buildError}
        </div>
      ) : null}

      {dryRunError ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          {dryRunError}
        </div>
      ) : null}

      {dryRun ? (
        <PageSection
          title="Dry-run 入库计划"
          description={`Postgres ${dryRun.postgres_written ? '会写入' : '未写入'} · Milvus ${dryRun.milvus_written ? '会写入' : '未写入'}`}
          actions={<StatusBadge tone={statusTone(dryRun.status)}>{text(dryRun.status)}</StatusBadge>}
        >
          <div className="primary-market-metric-grid grid gap-3 md:grid-cols-4">
            <Surface kind="card">
              <p className="text-sm text-text-muted">Schema</p>
              <p className="mt-1 break-all text-sm font-semibold text-text">{text(dryRun.schema_version)}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">Postgres</p>
              <p className="mt-1 text-sm font-semibold text-text">{dryRun.postgres_written ? '会写入' : 'dry-run 未写入'}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">Milvus</p>
              <p className="mt-1 text-sm font-semibold text-text">{dryRun.milvus_written ? '会写入' : 'dry-run 未写入'}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">Preview</p>
              <p className="mt-1 text-sm font-semibold text-text">{postgresRowsPreview.length} rows / {milvusChunksPreview.length} chunks</p>
            </Surface>
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Surface kind="muted" padding="sm">
              <p className="text-sm font-semibold text-text">Counts</p>
              {dryRunCounts.length ? (
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {dryRunCounts.map(([key, value]) => (
                    <div key={key} className="rounded-md border border-border/70 bg-background/70 px-3 py-2">
                      <p className="text-xs text-text-muted">{key}</p>
                      <p className="mt-0.5 break-all text-sm font-semibold text-text">{text(value, '0')}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="mt-2 text-sm text-text-muted">未返回 counts。</p>
              )}
            </Surface>
            <Surface kind="muted" padding="sm">
              <p className="text-sm font-semibold text-text">Targets</p>
              <div className="mt-3 grid gap-2 md:grid-cols-2">
                <pre className="max-h-48 overflow-auto rounded-md bg-background/70 p-3 text-xs text-text-muted">
                  {jsonPreview(dryRun.target_postgres)}
                </pre>
                <pre className="max-h-48 overflow-auto rounded-md bg-background/70 p-3 text-xs text-text-muted">
                  {jsonPreview(dryRun.target_milvus)}
                </pre>
              </div>
            </Surface>
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Surface kind="muted" padding="sm">
              <p className="text-sm font-semibold text-text">Warnings</p>
              {dryRunWarnings.length ? (
                <div className="mt-3 grid gap-2">
                  {dryRunWarnings.map((warning, index) => (
                    <p key={index} className="rounded-md border border-warning/30 bg-warning/5 px-3 py-2 text-sm leading-6 text-text">
                      {text(warning)}
                    </p>
                  ))}
                </div>
              ) : (
                <p className="mt-2 text-sm text-text-muted">暂无 warnings。</p>
              )}
            </Surface>
            <Surface kind="muted" padding="sm">
              <p className="text-sm font-semibold text-text">Errors</p>
              {dryRunErrors.length ? (
                <div className="mt-3 grid gap-2">
                  {dryRunErrors.map((error, index) => (
                    <p key={index} className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm leading-6 text-destructive">
                      {text(error)}
                    </p>
                  ))}
                </div>
              ) : (
                <p className="mt-2 text-sm text-text-muted">暂无 errors。</p>
              )}
            </Surface>
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Surface kind="muted" padding="sm">
              <p className="text-sm font-semibold text-text">Postgres rows preview</p>
              <pre className="mt-3 max-h-72 overflow-auto rounded-md bg-background/70 p-3 text-xs text-text-muted">
                {jsonPreview(postgresRowsPreview)}
              </pre>
            </Surface>
            <Surface kind="muted" padding="sm">
              <p className="text-sm font-semibold text-text">Milvus chunks preview</p>
              <pre className="mt-3 max-h-72 overflow-auto rounded-md bg-background/70 p-3 text-xs text-text-muted">
                {jsonPreview(milvusChunksPreview)}
              </pre>
            </Surface>
          </div>
        </PageSection>
      ) : null}

      <Surface kind="card">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-end">
          <div className="min-w-0 flex-1">
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-text-muted">Search</p>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="pl-9"
                placeholder="搜索 claim、quote、citation"
              />
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-3 xl:min-w-[560px]">
            <label className="min-w-0">
              <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-text-muted">Dimension</span>
              <select
                value={dimension}
                onChange={(event) => setDimension(event.target.value)}
                className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm text-text shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
              >
                <option value="">全部维度</option>
                {availableDimensions.map((value) => (
                  <option key={value} value={value}>{value}</option>
                ))}
              </select>
            </label>
            <label className="min-w-0">
              <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-text-muted">Document</span>
              <select
                value={documentId}
                onChange={(event) => setDocumentId(event.target.value)}
                className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm text-text shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
              >
                <option value="">全部文档</option>
                {availableDocuments.map(([id, label]) => (
                  <option key={id} value={id}>{label}</option>
                ))}
              </select>
            </label>
            <label className="min-w-0">
              <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-text-muted">Limit</span>
              <select
                value={limit}
                onChange={(event) => setLimit(event.target.value)}
                className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm text-text shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
              >
                {availableLimits.map((value) => (
                  <option key={value} value={value}>{value} 条</option>
                ))}
              </select>
            </label>
          </div>
          <Button type="button" variant="secondary" onClick={refresh} disabled={loading} className="xl:mb-0">
            {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            刷新
          </Button>
        </div>
      </Surface>

      {error ? (
        <PageSection>
          <EmptyState
            icon={FileSearch}
            title="证据包加载失败"
            description={error}
            action={
              <Button onClick={() => void loadEvidence()} disabled={loading}>
                {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                重试
              </Button>
            }
          />
        </PageSection>
      ) : loading ? (
        <div className="grid gap-3">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="h-20 animate-pulse rounded-lg bg-muted/60" />
          ))}
        </div>
      ) : !data || !report ? (
        <PageSection>
          <EmptyState
            icon={Boxes}
            title="暂无证据包"
            description="当前项目还没有 evidence index。可以先构建证据包。"
            action={
              <Button onClick={() => void handleBuild()} disabled={building}>
                {building ? <Loader2 className="animate-spin" /> : <PackageCheck />}
                构建证据包
              </Button>
            }
          />
        </PageSection>
      ) : (
        <>
          <div className="primary-market-metric-grid grid gap-3 md:grid-cols-4">
            <Surface kind="card">
              <p className="text-sm text-text-muted">质量状态</p>
              <div className="mt-2">
                <StatusBadge tone={statusTone(report.status)}>{text(report.status)}</StatusBadge>
              </div>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">证据条目</p>
              <p className="mt-1 text-2xl font-semibold text-text">{report.item_count ?? 0}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">已验证</p>
              <p className="mt-1 text-2xl font-semibold text-text">{report.verified_count ?? 0}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">维度覆盖</p>
              <p className="mt-1 text-2xl font-semibold text-text">{coverageText(report)}</p>
            </Surface>
          </div>

          <PageSection
            title="维度覆盖"
            description="已覆盖和缺失的 evidence dimensions。"
            actions={<StatusBadge tone={missingDimensions.length ? 'warning' : 'success'}>{missingDimensions.length ? `缺失 ${missingDimensions.length}` : '覆盖完整'}</StatusBadge>}
          >
            <div className="grid gap-3 lg:grid-cols-2">
              <Surface kind="muted" padding="sm">
                <p className="text-sm font-semibold text-text">已覆盖</p>
                {dimensions.length ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {dimensions.map((dimension) => (
                      <StatusBadge key={dimension} tone="success">{dimension}</StatusBadge>
                    ))}
                  </div>
                ) : (
                  <p className="mt-2 text-sm text-text-muted">暂无覆盖维度</p>
                )}
              </Surface>
              <Surface kind="muted" padding="sm">
                <p className="text-sm font-semibold text-text">缺失</p>
                {missingDimensions.length ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {missingDimensions.map((dimension) => (
                      <StatusBadge key={dimension} tone="warning">{dimension}</StatusBadge>
                    ))}
                  </div>
                ) : (
                  <p className="mt-2 text-sm text-text-muted">暂无缺失维度</p>
                )}
              </Surface>
            </div>
          </PageSection>

          <PageSection title="质量警告" description={`${warnings.length} 条 warning`}>
            {warnings.length ? (
              <div className="grid gap-3">
                {warnings.map((warning, index) => (
                  <Surface key={`${warning}-${index}`} kind="row" padding="sm">
                    <p className="text-sm leading-6 text-text">{warning}</p>
                  </Surface>
                ))}
              </div>
            ) : (
              <EmptyState title="暂无质量警告" description="质量报告未返回 warnings。" size="sm" />
            )}
          </PageSection>

          <PageSection title="证据预览" description={`${items.length} 条 preview item`}>
            {items.length ? (
              <div className="grid gap-3">
                {items.map((item, index) => (
                  <Surface key={itemKey(item, index)} kind="row" padding="sm">
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusBadge tone="info">{text(item.dimension, 'unknown')}</StatusBadge>
                          <StatusBadge tone={statusTone(item.evidence_type)}>{text(item.evidence_type, 'evidence')}</StatusBadge>
                          <span className="text-xs text-text-muted">{formatTime(item.created_at)}</span>
                        </div>
                        <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-text">{text(item.claim)}</p>
                        {item.quote ? (
                          <blockquote className="mt-2 border-l-2 border-primary/30 pl-3 text-sm leading-6 text-text-muted">
                            {item.quote}
                          </blockquote>
                        ) : null}
                        <p className="mt-2 break-all font-mono text-xs text-text-muted">{text(item.evidence_id)}</p>
                      </div>
                      <div className="primary-market-mobile-fluid grid min-w-56 gap-3 text-sm lg:max-w-md">
                        <div className="grid gap-2 sm:grid-cols-2">
                          <div>
                            <p className="text-xs text-text-muted">Confidence</p>
                            <p className="font-semibold text-text">{formatConfidence(item.confidence)}</p>
                          </div>
                          <div>
                            <p className="text-xs text-text-muted">Document</p>
                            <p className="break-all font-semibold text-text">{text(item.document_id)}</p>
                          </div>
                        </div>
                        {sourceLinks(item).length ? (
                          <div className="flex flex-wrap gap-2 lg:justify-end">
                            {sourceLinks(item).map((link) => (
                              <Button key={link.label} asChild variant="outline" size="sm">
                                <a href={link.href} target="_blank" rel="noreferrer">
                                  <ExternalLink />
                                  {link.label}
                                </a>
                              </Button>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    </div>
                    <div className="mt-3 grid gap-2 text-xs text-text-muted md:grid-cols-2">
                      <p className="break-all">Source: {text(item.source_path)}</p>
                      <p className="break-all">Anchor: {text(item.source_anchor)}</p>
                      <p className="break-all">Citation: {text(item.citation)}</p>
                      <p className="break-all">Locator: {text(item.locator)}</p>
                      <p className="break-all">Roles: {item.role_hints?.length ? item.role_hints.join(' / ') : '未记录'}</p>
                    </div>
                  </Surface>
                ))}
              </div>
            ) : (
              <EmptyState title="暂无证据预览" description="接口未返回 items_preview。" size="sm" />
            )}
          </PageSection>

          <PageSection title="Evidence Index JSON">
            <pre className="max-h-[520px] overflow-auto rounded-lg bg-muted/60 p-3 text-xs text-text-muted">
              {JSON.stringify(data.evidence_index, null, 2)}
            </pre>
          </PageSection>
        </>
      )}
    </PageShell>
  )
}
