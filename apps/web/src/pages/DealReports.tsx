import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, FileJson, FileText, Loader2, RefreshCw, Search } from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  fetchDealR1AgentReports,
  fetchDealR2AgentReports,
  fetchDealR3ReviewSummary,
  fetchDealReport,
  fetchDealReports,
} from '@/lib/dealApi'
import type {
  DealR1AgentReportSummary,
  DealR1AgentReportsResponse,
  DealR2AgentReportSummary,
  DealR2AgentReportsResponse,
  DealR3ReviewReportSummary,
  DealR3ReviewSummaryResponse,
  DealReportDetailResponse,
  DealReportMeta,
  DealReportsResponse,
} from '@/lib/dealTypes'

function text(value: unknown, fallback = '未记录') {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

function statusTone(status?: string | null): 'neutral' | 'info' | 'success' | 'warning' | 'error' {
  if (status === 'available' || status === 'pass' || status === 'match') return 'success'
  if (status === 'missing') return 'warning'
  if (status === 'warn' || status === 'advisory' || status === 'receipt_only' || status === 'report_only' || status === 'mismatch') return 'warning'
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

function compactList(values?: string[], limit = 3) {
  if (!Array.isArray(values) || values.length === 0) return '无'
  const shown = values.slice(0, limit).join(', ')
  return values.length > limit ? `${shown} +${values.length - limit}` : shown
}

function formatScoreDelta(value?: number | string | null) {
  if (value === null || value === undefined || value === '') return '未记录'
  const numeric = Number(value)
  if (Number.isNaN(numeric)) return String(value)
  return numeric > 0 ? `+${numeric}` : String(numeric)
}

export default function DealReports() {
  const { dealId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedPath = searchParams.get('path') || ''
  const [data, setData] = useState<DealReportsResponse | null>(null)
  const [r1Reports, setR1Reports] = useState<DealR1AgentReportsResponse | null>(null)
  const [r2Reports, setR2Reports] = useState<DealR2AgentReportsResponse | null>(null)
  const [r3Review, setR3Review] = useState<DealR3ReviewSummaryResponse | null>(null)
  const [detail, setDetail] = useState<DealReportDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState('')
  const [r1Error, setR1Error] = useState('')
  const [r2Error, setR2Error] = useState('')
  const [r3Error, setR3Error] = useState('')
  const [detailError, setDetailError] = useState('')
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      setLoading(true)
      setError('')
      setR1Error('')
      setR2Error('')
      setR3Error('')
      try {
        const [reportsResult, r1Result, r2Result, r3Result] = await Promise.allSettled([
          fetchDealReports(dealId, controller.signal),
          fetchDealR1AgentReports(dealId, controller.signal),
          fetchDealR2AgentReports(dealId, controller.signal),
          fetchDealR3ReviewSummary(dealId, controller.signal),
        ])
        if (reportsResult.status === 'rejected') {
          throw reportsResult.reason
        }
        setData(reportsResult.value)
        if (r1Result.status === 'fulfilled') {
          setR1Reports(r1Result.value)
        } else {
          setR1Reports(null)
          setR1Error(r1Result.reason instanceof Error ? r1Result.reason.message : 'R1 报告合同摘要加载失败')
        }
        if (r2Result.status === 'fulfilled') {
          setR2Reports(r2Result.value)
        } else {
          setR2Reports(null)
          setR2Error(r2Result.reason instanceof Error ? r2Result.reason.message : 'R2 修订合同摘要加载失败')
        }
        if (r3Result.status === 'fulfilled') {
          setR3Review(r3Result.value)
        } else {
          setR3Review(null)
          setR3Error(r3Result.reason instanceof Error ? r3Result.reason.message : 'R3 红蓝摘要加载失败')
        }
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
  const r1Agents = useMemo(() => (Array.isArray(r1Reports?.agents) ? r1Reports.agents : []), [r1Reports])
  const r2Agents = useMemo(() => (Array.isArray(r2Reports?.agents) ? r2Reports.agents : []), [r2Reports])
  const r3Reports = useMemo(() => (Array.isArray(r3Review?.reports) ? r3Review.reports : []), [r3Review])
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
    setR1Reports(null)
    setR2Reports(null)
    setR3Review(null)
    setLoading(true)
    setError('')
    setR1Error('')
    setR2Error('')
    setR3Error('')
    void Promise.allSettled([
      fetchDealReports(dealId),
      fetchDealR1AgentReports(dealId),
      fetchDealR2AgentReports(dealId),
      fetchDealR3ReviewSummary(dealId),
    ])
      .then(([reportsResult, r1Result, r2Result, r3Result]) => {
        if (reportsResult.status === 'rejected') throw reportsResult.reason
        setData(reportsResult.value)
        if (r1Result.status === 'fulfilled') {
          setR1Reports(r1Result.value)
        } else {
          setR1Error(r1Result.reason instanceof Error ? r1Result.reason.message : 'R1 报告合同摘要加载失败')
        }
        if (r2Result.status === 'fulfilled') {
          setR2Reports(r2Result.value)
        } else {
          setR2Error(r2Result.reason instanceof Error ? r2Result.reason.message : 'R2 修订合同摘要加载失败')
        }
        if (r3Result.status === 'fulfilled') {
          setR3Review(r3Result.value)
        } else {
          setR3Error(r3Result.reason instanceof Error ? r3Result.reason.message : 'R3 红蓝摘要加载失败')
        }
      })
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

          <PageSection
            title="R1 Expert Contracts"
            description="报告字段、startup receipt 关联和检索摘要章节状态。"
            actions={r1Reports ? <StatusBadge tone="info">{text(r1Reports.schema_version)}</StatusBadge> : null}
          >
            {r1Error ? (
              <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-text">
                {r1Error}
              </div>
            ) : null}
            {r1Reports ? (
              <div className="grid gap-3 md:grid-cols-4">
                <Surface kind="muted" padding="sm">
                  <p className="text-xs text-text-muted">Pass</p>
                  <p className="mt-1 text-xl font-semibold text-text">{r1Reports.counts?.pass ?? 0}</p>
                </Surface>
                <Surface kind="muted" padding="sm">
                  <p className="text-xs text-text-muted">Warn</p>
                  <p className="mt-1 text-xl font-semibold text-text">{r1Reports.counts?.warn ?? 0}</p>
                </Surface>
                <Surface kind="muted" padding="sm">
                  <p className="text-xs text-text-muted">Missing</p>
                  <p className="mt-1 text-xl font-semibold text-text">{r1Reports.counts?.missing ?? 0}</p>
                </Surface>
                <Surface kind="muted" padding="sm">
                  <p className="text-xs text-text-muted">Artifacts</p>
                  <p className="mt-1 text-xl font-semibold text-text">{r1Reports.counts?.artifacts_available ?? 0}</p>
                </Surface>
              </div>
            ) : null}
            {r1Agents.length ? (
              <div className="grid gap-3 lg:grid-cols-2">
                {r1Agents.map((agent: DealR1AgentReportSummary) => (
                  <Surface key={agent.agent_id} kind="row" padding="sm">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-semibold text-text">{text(agent.label, agent.agent_id)}</p>
                        <p className="mt-1 break-all font-mono text-xs text-text-muted">{agent.agent_id}</p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <StatusBadge tone={statusTone(agent.status)}>{text(agent.status)}</StatusBadge>
                        <StatusBadge tone={statusTone(agent.startup_receipt_linkage)}>
                          {text(agent.startup_receipt_linkage)}
                        </StatusBadge>
                        <StatusBadge tone={statusTone(agent.markdown_section_status)}>
                          {text(agent.markdown_section_status)}
                        </StatusBadge>
                      </div>
                    </div>
                    <div className="mt-3 grid gap-2 text-xs text-text-muted">
                      <p className="break-all">Artifact: {text(agent.artifact_path)} · {agent.artifact_available ? 'available' : 'missing'}</p>
                      <p>Required: {compactList(agent.missing_required_fields)}</p>
                      {Array.isArray(agent.missing_contract_fields) && agent.missing_contract_fields.length ? (
                        <p>Contract fields: {compactList(agent.missing_contract_fields)}</p>
                      ) : null}
                      <p>Markdown: {compactList(agent.missing_markdown_sections, 2)}</p>
                      <p>Rec: {text(agent.recommendation)} · Score: {text(agent.score)}</p>
                    </div>
                  </Surface>
                ))}
              </div>
            ) : r1Reports ? (
              <EmptyState title="暂无 R1 合同摘要" size="sm" />
            ) : null}
          </PageSection>

          <PageSection
            title="R2 Revision Contracts"
            description="R2 修订观点合同摘要、分数变化和缺失字段状态。"
            actions={r2Reports ? <StatusBadge tone="info">{text(r2Reports.schema_version)}</StatusBadge> : null}
          >
            {r2Error ? (
              <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-text">
                {r2Error}
              </div>
            ) : null}
            {r2Reports ? (
              <div className="space-y-3">
                <div className="grid gap-3 md:grid-cols-5">
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Agents</p>
                    <p className="mt-1 text-xl font-semibold text-text">{r2Reports.counts?.agents ?? r2Agents.length}</p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Pass</p>
                    <p className="mt-1 text-xl font-semibold text-text">{r2Reports.counts?.pass ?? 0}</p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Warn</p>
                    <p className="mt-1 text-xl font-semibold text-text">{r2Reports.counts?.warn ?? 0}</p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Missing</p>
                    <p className="mt-1 text-xl font-semibold text-text">{r2Reports.counts?.missing ?? 0}</p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Revisions</p>
                    <p className="mt-1 text-xl font-semibold text-text">{r2Reports.counts?.revisions ?? 0}</p>
                  </Surface>
                </div>
                <Surface kind="muted" padding="sm">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-text-muted">
                    <p className="break-all">Artifact: {text(r2Reports.artifact_path)}</p>
                    <StatusBadge tone={r2Reports.artifact_available ? 'success' : 'warning'}>
                      {r2Reports.artifact_available ? 'available' : 'missing'}
                    </StatusBadge>
                  </div>
                </Surface>
              </div>
            ) : null}
            {r2Agents.length ? (
              <div className="grid gap-3 lg:grid-cols-2">
                {r2Agents.map((agent: DealR2AgentReportSummary) => (
                  <Surface key={agent.agent_id} kind="row" padding="sm">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-semibold text-text">{text(agent.label, agent.agent_id)}</p>
                        <p className="mt-1 break-all font-mono text-xs text-text-muted">{agent.agent_id}</p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <StatusBadge tone={statusTone(agent.status)}>{text(agent.status)}</StatusBadge>
                        <StatusBadge tone={agent.artifact_available ? 'success' : 'warning'}>
                          {agent.artifact_available ? 'artifact' : 'no artifact'}
                        </StatusBadge>
                      </div>
                    </div>
                    <div className="mt-3 grid gap-2 text-xs text-text-muted">
                      <p>
                        Score: {text(agent.r2_score ?? agent.score)} · Delta: {formatScoreDelta(agent.score_change)} ·
                        R1: {text(agent.r1_score)}
                      </p>
                      <p>
                        Revisions: {agent.revision_count ?? 0} · Verified: {agent.verified_count ?? 0} ·
                        Assumed: {agent.assumed_count ?? 0} · Open Q: {agent.open_questions_count ?? 0}
                      </p>
                      <p>Rec: {text(agent.recommendation)} · Confidence: {text(agent.confidence)}</p>
                      <p className="break-all">Artifact: {text(agent.artifact_path)}</p>
                      <p>Summary: {text(agent.summary)}</p>
                      <p>Contract fields: {compactList(agent.missing_contract_fields)}</p>
                      <p>Advisory fields: {compactList(agent.missing_advisory_fields)}</p>
                    </div>
                  </Surface>
                ))}
              </div>
            ) : r2Reports ? (
              <EmptyState title="暂无 R2 修订合同摘要" size="sm" />
            ) : null}
          </PageSection>

          <PageSection
            title="R3 Review Summary"
            description="红蓝对抗或显式跳过的只读摘要。"
            actions={r3Review ? (
              <div className="flex flex-wrap gap-2">
                <StatusBadge tone={statusTone(r3Review.status)}>{text(r3Review.status)}</StatusBadge>
                <StatusBadge tone={r3Review.mode === 'skip' ? 'info' : 'neutral'}>{text(r3Review.mode)}</StatusBadge>
              </div>
            ) : null}
          >
            {r3Error ? (
              <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-text">
                {r3Error}
              </div>
            ) : null}
            {r3Review ? (
              <div className="space-y-3">
                <div className="grid gap-3 md:grid-cols-5">
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Reports</p>
                    <p className="mt-1 text-xl font-semibold text-text">{r3Review.counts?.reports ?? r3Reports.length}</p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Pass</p>
                    <p className="mt-1 text-xl font-semibold text-text">{r3Review.counts?.pass ?? 0}</p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Warn</p>
                    <p className="mt-1 text-xl font-semibold text-text">{r3Review.counts?.warn ?? 0}</p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Challenges</p>
                    <p className="mt-1 text-xl font-semibold text-text">{r3Review.counts?.challenges ?? 0}</p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Warnings</p>
                    <p className="mt-1 text-xl font-semibold text-text">{r3Review.counts?.warnings ?? 0}</p>
                  </Surface>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  {(['json', 'markdown'] as const).map((key) => {
                    const artifact = r3Review.artifacts?.[key]
                    return (
                      <Surface key={key} kind="muted" padding="sm">
                        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-text-muted">
                          <p className="break-all">{key.toUpperCase()}: {text(artifact?.path)}</p>
                          <StatusBadge tone={artifact?.available ? 'success' : 'warning'}>
                            {artifact?.available ? 'available' : 'missing'}
                          </StatusBadge>
                        </div>
                      </Surface>
                    )
                  })}
                </div>
                {r3Review.skipped ? (
                  <Surface kind="muted" padding="sm">
                    <p className="text-sm font-semibold text-text">Skipped</p>
                    <p className="mt-1 text-sm text-text-muted">{text(r3Review.skip_reason, '显式跳过，未记录原因')}</p>
                  </Surface>
                ) : null}
                {Array.isArray(r3Review.warnings) && r3Review.warnings.length ? (
                  <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-text">
                    {r3Review.warnings.map((warning) => (
                      <p key={warning} className="break-all font-mono text-xs">{warning}</p>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
            {r3Reports.length ? (
              <div className="grid gap-3 lg:grid-cols-2">
                {r3Reports.map((report: DealR3ReviewReportSummary) => (
                  <Surface key={report.agent_id} kind="row" padding="sm">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-semibold text-text">{text(report.label, report.agent_id)}</p>
                        <p className="mt-1 break-all font-mono text-xs text-text-muted">{report.agent_id}</p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <StatusBadge tone={statusTone(report.status)}>{text(report.status)}</StatusBadge>
                        {report.stance ? <StatusBadge tone="info">{report.stance}</StatusBadge> : null}
                      </div>
                    </div>
                    <div className="mt-3 grid gap-2 text-xs text-text-muted">
                      <p>Rec: {text(report.recommendation)} · Challenges: {report.challenge_count ?? 0} · Evidence: {report.evidence_count ?? 0}</p>
                      <p>Summary: {text(report.summary)}</p>
                    </div>
                  </Surface>
                ))}
              </div>
            ) : r3Review && !r3Review.skipped ? (
              <EmptyState title="暂无 R3 红蓝报告摘要" size="sm" />
            ) : null}
          </PageSection>

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
