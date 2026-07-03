import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Boxes, FileSearch, Loader2, PackageCheck, RefreshCw } from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { buildDealEvidence, fetchDealEvidence } from '@/lib/dealApi'
import type { DealEvidenceItem, DealEvidenceQualityReport, DealEvidenceResponse } from '@/lib/dealTypes'

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

export default function DealEvidence() {
  const { dealId = '' } = useParams()
  const [data, setData] = useState<DealEvidenceResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [building, setBuilding] = useState(false)
  const [buildError, setBuildError] = useState('')

  const loadEvidence = async (signal?: AbortSignal) => {
    setLoading(true)
    setError('')
    try {
      setData(await fetchDealEvidence(dealId, signal))
    } catch (err) {
      if (!signal?.aborted) {
        setError(err instanceof Error ? err.message : '证据包加载失败')
      }
    } finally {
      if (!signal?.aborted) {
        setLoading(false)
      }
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      setLoading(true)
      setError('')
      try {
        setData(await fetchDealEvidence(dealId, controller.signal))
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : '证据包加载失败')
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false)
        }
      }
    })()
    return () => controller.abort()
  }, [dealId])

  const handleBuild = async () => {
    setBuilding(true)
    setBuildError('')
    try {
      setData(await buildDealEvidence(dealId))
      setError('')
    } catch (err) {
      setBuildError(err instanceof Error ? err.message : '证据包构建失败')
    } finally {
      setBuilding(false)
    }
  }

  const report = data?.quality_report
  const items = Array.isArray(data?.items_preview) ? data.items_preview : []
  const dimensions = sortedDimensions(report)
  const missingDimensions = sortedMissingDimensions(report)
  const warnings = Array.isArray(report?.warnings) ? report.warnings : []

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
          </div>
        }
      />

      {buildError ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          {buildError}
        </div>
      ) : null}

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
          <div className="grid gap-3 md:grid-cols-4">
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
                        <p className="mt-2 break-all font-mono text-xs text-text-muted">{text(item.evidence_id)}</p>
                      </div>
                      <div className="grid min-w-56 gap-2 text-sm sm:grid-cols-2 lg:max-w-md">
                        <div>
                          <p className="text-xs text-text-muted">Confidence</p>
                          <p className="font-semibold text-text">{formatConfidence(item.confidence)}</p>
                        </div>
                        <div>
                          <p className="text-xs text-text-muted">Document</p>
                          <p className="break-all font-semibold text-text">{text(item.document_id)}</p>
                        </div>
                      </div>
                    </div>
                    <div className="mt-3 grid gap-2 text-xs text-text-muted md:grid-cols-2">
                      <p className="break-all">Source: {text(item.source_path)}</p>
                      <p className="break-all">Anchor: {text(item.source_anchor)}</p>
                      <p className="break-all">Citation: {text(item.citation)}</p>
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
