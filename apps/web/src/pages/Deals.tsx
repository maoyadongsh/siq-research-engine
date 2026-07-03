import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  BriefcaseBusiness,
  CheckCircle2,
  CircleCheck,
  CircleX,
  Clock3,
  Loader2,
  RefreshCcw,
  Search,
  Upload,
} from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { fetchDealJob, fetchDeals, importOpenClawDeal } from '@/lib/dealApi'
import type { DealJobStatus, DealListResponse, DealStats, DealSummary, OpenClawImportPayload } from '@/lib/dealTypes'

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  r0_ready: 'R0 就绪',
  r1_in_progress: 'R1 尽调中',
  r4_completed: 'R4 已归档',
  archived: '已归档',
  closed: '已关闭',
}

const JOB_STATUS_LABELS: Record<string, string> = {
  queued: '已排队',
  pending: '等待中',
  running: '运行中',
  in_progress: '运行中',
  completed: '已完成',
  complete: '已完成',
  completed_with_warnings: '已完成',
  succeeded: '已完成',
  success: '已完成',
  done: '已完成',
  finished: '已完成',
  failed: '失败',
  failure: '失败',
  error: '失败',
  errored: '失败',
  canceled: '已取消',
  cancelled: '已取消',
}

const TERMINAL_JOB_STATUSES = new Set([
  'completed',
  'complete',
  'completed_with_warnings',
  'succeeded',
  'success',
  'done',
  'finished',
  'failed',
  'failure',
  'error',
  'errored',
  'canceled',
  'cancelled',
])
const SUCCESS_JOB_STATUSES = new Set(['completed', 'complete', 'completed_with_warnings', 'succeeded', 'success', 'done', 'finished'])
const FAILURE_JOB_STATUSES = new Set(['failed', 'failure', 'error', 'errored'])

function statusLabel(status?: string | null) {
  if (!status) return '未设置'
  return STATUS_LABELS[status] || status
}

function statusTone(status?: string | null): 'neutral' | 'info' | 'success' | 'warning' | 'error' {
  if (!status || status === 'draft') return 'neutral'
  if (status === 'r4_completed' || status === 'archived' || status === 'closed') return 'success'
  if (status.includes('blocked') || status.includes('fail')) return 'error'
  if (status.includes('review') || status.includes('risk')) return 'warning'
  return 'info'
}

function formatUpdatedAt(value?: string | null) {
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

function deriveStats(deals: DealSummary[], stats?: DealStats): DealStats {
  if (stats) return stats
  return {
    total: deals.length,
    active: deals.filter((deal) => !['r4_completed', 'archived', 'closed'].includes(String(deal.status || ''))).length,
    diligence: deals.filter((deal) => String(deal.status || '').startsWith('r1')).length,
    highRisk: 0,
  }
}

function metricCards(stats: DealStats) {
  return [
    { label: '项目总数', value: stats.total, icon: BriefcaseBusiness },
    { label: '进行中', value: stats.active, icon: Clock3 },
    { label: '尽调中', value: stats.diligence, icon: Search },
    { label: '高风险', value: stats.highRisk, icon: AlertTriangle },
  ]
}

function normalizeJobStatus(status?: string | null) {
  return String(status || '').trim().toLowerCase()
}

function isTerminalJobStatus(status?: string | null) {
  return TERMINAL_JOB_STATUSES.has(normalizeJobStatus(status))
}

function isSuccessfulJobStatus(status?: string | null) {
  return SUCCESS_JOB_STATUSES.has(normalizeJobStatus(status))
}

function jobStatusLabel(status?: string | null) {
  const normalized = normalizeJobStatus(status)
  if (!normalized) return '未提交'
  return JOB_STATUS_LABELS[normalized] || status || normalized
}

function jobStatusTone(status?: string | null): 'neutral' | 'info' | 'success' | 'warning' | 'error' {
  const normalized = normalizeJobStatus(status)
  if (!normalized) return 'neutral'
  if (SUCCESS_JOB_STATUSES.has(normalized)) return 'success'
  if (FAILURE_JOB_STATUSES.has(normalized)) return 'error'
  if (normalized === 'canceled' || normalized === 'cancelled') return 'warning'
  if (normalized === 'queued' || normalized === 'pending') return 'warning'
  return 'info'
}

function jobStatusIcon(status?: string | null) {
  const normalized = normalizeJobStatus(status)
  if (SUCCESS_JOB_STATUSES.has(normalized)) return CircleCheck
  if (FAILURE_JOB_STATUSES.has(normalized)) return CircleX
  if (normalized) return Loader2
  return undefined
}

function parseMetadataText(text: string) {
  const trimmed = text.trim()
  if (!trimmed) return undefined
  const parsed = JSON.parse(trimmed) as unknown
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Metadata 必须是 JSON 对象')
  }
  return parsed as Record<string, unknown>
}

function stringifyUnknown(value: unknown) {
  if (value === undefined || value === null || value === '') return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2) || String(value)
  } catch {
    return String(value)
  }
}

function jobErrorText(job?: DealJobStatus | null) {
  if (!job) return ''
  const directError = stringifyUnknown(job.error)
  if (directError) return directError
  if (job.ok === false || FAILURE_JOB_STATUSES.has(normalizeJobStatus(job.status))) {
    for (const value of [job.detail, job.message]) {
      const text = stringifyUnknown(value)
      if (text) return text
    }
  }
  return ''
}

function jobResultValue(job?: DealJobStatus | null) {
  if (!job) return null
  if (job.result !== undefined && job.result !== null) return job.result
  if (!isSuccessfulJobStatus(job.status)) return null
  const fallback: Record<string, unknown> = {}
  if (job.deal_id) fallback.deal_id = job.deal_id
  if (job.project_id) fallback.project_id = job.project_id
  if (typeof job.ok === 'boolean') fallback.ok = job.ok
  return Object.keys(fallback).length ? fallback : null
}

function mergeJobStatus(previous: DealJobStatus | null, next: DealJobStatus): DealJobStatus {
  return {
    ...(previous || {}),
    ...next,
    job_id: next.job_id || previous?.job_id,
    kind: next.kind || previous?.kind,
  }
}

export default function Deals() {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [data, setData] = useState<DealListResponse>({ deals: [] })
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [reloadKey, setReloadKey] = useState(0)
  const [openClawDealId, setOpenClawDealId] = useState('')
  const [openClawProjectId, setOpenClawProjectId] = useState('')
  const [openClawSourceRoot, setOpenClawSourceRoot] = useState('')
  const [openClawOverwrite, setOpenClawOverwrite] = useState(false)
  const [openClawMetadata, setOpenClawMetadata] = useState('')
  const [openClawSubmitting, setOpenClawSubmitting] = useState(false)
  const [openClawJob, setOpenClawJob] = useState<DealJobStatus | null>(null)
  const [openClawError, setOpenClawError] = useState('')

  const refresh = useCallback(() => {
    setRefreshing(true)
    setReloadKey((value) => value + 1)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      setLoading(true)
      setError('')
      try {
        const payload = await fetchDeals({ q: query, status }, controller.signal)
        setData({
          deals: Array.isArray(payload.deals) ? payload.deals : [],
          stats: payload.stats,
        })
      } catch (err) {
        if (controller.signal.aborted) return
        setError(err instanceof Error ? err.message : '交易项目加载失败')
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false)
          setRefreshing(false)
        }
      }
    })()
    return () => controller.abort()
  }, [query, status, reloadKey])

  const openClawJobId = openClawJob?.job_id || ''
  const openClawJobStatus = openClawJob?.status || ''
  const openClawBusy = openClawSubmitting || (!!openClawJobId && !isTerminalJobStatus(openClawJobStatus))

  useEffect(() => {
    if (!openClawJobId || isTerminalJobStatus(openClawJobStatus)) return
    const controller = new AbortController()
    let cancelled = false
    let timer: number | undefined

    const poll = async () => {
      try {
        const job = await fetchDealJob(openClawJobId, controller.signal)
        if (cancelled) return
        setOpenClawJob((previous) => mergeJobStatus(previous, job))
        const status = job.status || openClawJobStatus
        setOpenClawError(jobErrorText(job))
        if (isTerminalJobStatus(status)) {
          if (isSuccessfulJobStatus(status)) refresh()
          return
        }
        timer = window.setTimeout(poll, 1600)
      } catch (err) {
        if (cancelled || controller.signal.aborted) return
        setOpenClawError(err instanceof Error ? err.message : '任务状态查询失败')
        timer = window.setTimeout(poll, 2500)
      }
    }

    timer = window.setTimeout(poll, 800)
    return () => {
      cancelled = true
      controller.abort()
      if (timer) window.clearTimeout(timer)
    }
  }, [openClawJobId, openClawJobStatus, refresh])

  const stats = useMemo(() => deriveStats(data.deals, data.stats), [data.deals, data.stats])
  const openClawStatusIcon = jobStatusIcon(openClawJobStatus)
  const openClawStatusSpin = !!openClawJobId && !isTerminalJobStatus(openClawJobStatus)
  const openClawResultText = stringifyUnknown(jobResultValue(openClawJob))
  const openClawDisplayError = openClawError || jobErrorText(openClawJob)

  const handleOpenClawImport = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const dealId = openClawDealId.trim()
    const projectId = openClawProjectId.trim()
    const sourceRoot = openClawSourceRoot.trim()

    if (!dealId || (!projectId && !sourceRoot)) {
      setOpenClawError('Deal ID 必填，Project ID 和 Source Root 至少填写一个')
      return
    }

    let metadata: Record<string, unknown> | undefined
    try {
      metadata = parseMetadataText(openClawMetadata)
    } catch (err) {
      setOpenClawError(err instanceof Error ? err.message : 'Metadata JSON 解析失败')
      return
    }

    const payload: OpenClawImportPayload = {
      deal_id: dealId,
      overwrite: openClawOverwrite,
    }
    if (projectId) payload.project_id = projectId
    if (sourceRoot) payload.source_root = sourceRoot
    if (metadata) payload.metadata = metadata

    setOpenClawSubmitting(true)
    setOpenClawError('')
    try {
      const response = await importOpenClawDeal(payload)
      setOpenClawJob(response)
      const message = jobErrorText(response)
      if (response.ok === false) {
        setOpenClawError(message || 'OpenClaw 导入失败')
        return
      }
      if (message) setOpenClawError(message)
      const responseStatus = response.status || ''
      const completed = isTerminalJobStatus(responseStatus)
      if ((completed && isSuccessfulJobStatus(responseStatus)) || (!response.job_id && response.ok === true && response.queued !== true)) {
        refresh()
      }
    } catch (err) {
      setOpenClawError(err instanceof Error ? err.message : 'OpenClaw 导入失败')
    } finally {
      setOpenClawSubmitting(false)
    }
  }, [openClawDealId, openClawMetadata, openClawOverwrite, openClawProjectId, openClawSourceRoot, refresh])

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={BriefcaseBusiness}
        eyebrow="Deal OS"
        title="交易工作台"
        description="一级市场项目、投委会阶段、证据状态与最终投决的统一入口。"
        actions={
          <Button variant="secondary" onClick={refresh} disabled={loading || refreshing}>
            <RefreshCcw className={refreshing ? 'animate-spin' : ''} />
            刷新
          </Button>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {metricCards(stats).map(({ label, value, icon: Icon }) => (
          <Surface key={label} kind="card" padding="md" className="min-w-0">
            <div className="flex items-center gap-3">
              <span className="premium-icon h-10 w-10 rounded-lg">
                <Icon className="h-5 w-5" />
              </span>
              <div className="min-w-0">
                <p className="text-sm text-text-muted">{label}</p>
                <p className="mt-1 text-2xl font-semibold text-text">{value}</p>
              </div>
            </div>
          </Surface>
        ))}
      </div>

      <PageSection
        title="OpenClaw 导入"
        actions={
          openClawJob ? (
            <StatusBadge
              tone={jobStatusTone(openClawJobStatus)}
              icon={openClawStatusIcon}
              className={openClawStatusSpin ? '[&_svg]:animate-spin' : undefined}
            >
              {jobStatusLabel(openClawJobStatus)}
            </StatusBadge>
          ) : null
        }
      >
        <form onSubmit={handleOpenClawImport} className="space-y-3">
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.3fr)]">
            <label className="min-w-0 space-y-1.5">
              <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">Deal ID</span>
              <Input
                required
                value={openClawDealId}
                onChange={(event) => setOpenClawDealId(event.target.value)}
                disabled={openClawBusy}
                className="font-mono"
                placeholder="DEAL-ACME-2026"
              />
            </label>
            <label className="min-w-0 space-y-1.5">
              <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">Project ID</span>
              <Input
                value={openClawProjectId}
                onChange={(event) => setOpenClawProjectId(event.target.value)}
                disabled={openClawBusy}
                className="font-mono"
                placeholder="SIQ-YUSHU-2026-002"
              />
            </label>
            <label className="min-w-0 space-y-1.5">
              <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">Source Root</span>
              <Input
                value={openClawSourceRoot}
                onChange={(event) => setOpenClawSourceRoot(event.target.value)}
                disabled={openClawBusy}
                className="font-mono"
                placeholder="/data/openclaw"
              />
            </label>
          </div>
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
            <label className="min-w-0 space-y-1.5">
              <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">Metadata JSON</span>
              <Textarea
                value={openClawMetadata}
                onChange={(event) => setOpenClawMetadata(event.target.value)}
                disabled={openClawBusy}
                rows={2}
                className="min-h-16 font-mono text-sm"
                placeholder='{"source":"openclaw"}'
              />
            </label>
            <div className="flex flex-col gap-2 sm:flex-row lg:flex-col">
              <label className="inline-flex h-9 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm font-medium text-text shadow-xs">
                <input
                  type="checkbox"
                  checked={openClawOverwrite}
                  onChange={(event) => setOpenClawOverwrite(event.target.checked)}
                  disabled={openClawBusy}
                  className="h-4 w-4 rounded border-input accent-primary"
                />
                覆盖已有
              </label>
              <Button type="submit" disabled={openClawBusy} className="min-w-28">
                {openClawSubmitting ? <Loader2 className="animate-spin" /> : <Upload />}
                导入
              </Button>
            </div>
          </div>
        </form>

        {openClawJob || openClawDisplayError ? (
          <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
            {openClawJob ? (
              <div className="rounded-lg border border-border/70 bg-muted/30 p-3">
                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="min-w-0">
                    <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Job ID</p>
                    <p className="mt-1 break-all font-mono text-sm text-text">{openClawJobId || '-'}</p>
                  </div>
                  <div className="min-w-0">
                    <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Status</p>
                    <p className="mt-1 text-sm font-semibold text-text">{jobStatusLabel(openClawJobStatus)}</p>
                  </div>
                  <div className="min-w-0">
                    <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Kind</p>
                    <p className="mt-1 break-all font-mono text-sm text-text">{openClawJob.kind || '-'}</p>
                  </div>
                </div>
              </div>
            ) : null}
            {openClawDisplayError ? (
              <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-destructive">
                <p className="text-xs font-semibold uppercase tracking-wide">Error</p>
                <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-words text-xs leading-5">
                  {openClawDisplayError}
                </pre>
              </div>
            ) : null}
            {openClawResultText ? (
              <div className="rounded-lg border border-border/70 bg-muted/30 p-3 lg:col-span-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Result</p>
                <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-text-muted">
                  {openClawResultText}
                </pre>
              </div>
            ) : null}
          </div>
        ) : null}
      </PageSection>

      <PageSection
        title="项目列表"
        actions={
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <div className="relative min-w-0 sm:w-72">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="pl-9"
                placeholder="搜索项目或公司"
              />
            </div>
            <select
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              className="h-9 rounded-md border border-input bg-background px-3 text-sm text-text shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
            >
              <option value="">全部状态</option>
              <option value="draft">草稿</option>
              <option value="r0_ready">R0 就绪</option>
              <option value="r1_in_progress">R1 尽调中</option>
              <option value="r4_completed">R4 已归档</option>
            </select>
          </div>
        }
      >
        {error ? (
          <EmptyState icon={AlertTriangle} title="加载失败" description={error} action={<Button onClick={refresh}>重试</Button>} />
        ) : loading ? (
          <div className="grid gap-3">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="h-16 animate-pulse rounded-lg bg-muted/60" />
            ))}
          </div>
        ) : data.deals.length === 0 ? (
          <EmptyState icon={BriefcaseBusiness} title="暂无交易项目" description="导入或创建项目后会显示在这里。" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] border-separate border-spacing-0 text-left text-sm">
              <thead>
                <tr className="text-xs uppercase tracking-wide text-text-muted">
                  <th className="border-b border-border/70 px-3 py-3 font-semibold">项目</th>
                  <th className="border-b border-border/70 px-3 py-3 font-semibold">行业 / 阶段</th>
                  <th className="border-b border-border/70 px-3 py-3 font-semibold">状态</th>
                  <th className="border-b border-border/70 px-3 py-3 font-semibold">当前阶段</th>
                  <th className="border-b border-border/70 px-3 py-3 font-semibold">投决</th>
                  <th className="border-b border-border/70 px-3 py-3 font-semibold">更新</th>
                </tr>
              </thead>
              <tbody>
                {data.deals.map((deal) => (
                  <tr key={deal.deal_id} className="align-top">
                    <td className="border-b border-border/50 px-3 py-3">
                      <Link
                        to={`/deals/${encodeURIComponent(deal.deal_id)}`}
                        className="font-semibold text-text underline-offset-4 hover:text-primary hover:underline"
                      >
                        {deal.company_name || deal.deal_id}
                      </Link>
                      <div className="mt-1 text-xs text-text-muted">{deal.deal_id}</div>
                      {deal.legacy_project_id ? (
                        <div className="mt-1 text-xs text-text-muted">{deal.legacy_project_id}</div>
                      ) : null}
                    </td>
                    <td className="border-b border-border/50 px-3 py-3 text-text-muted">
                      <div>{deal.industry || '未设置'}</div>
                      <div className="mt-1">{deal.stage || '未设置'}</div>
                    </td>
                    <td className="border-b border-border/50 px-3 py-3">
                      <StatusBadge tone={statusTone(deal.status)}>{statusLabel(deal.status)}</StatusBadge>
                    </td>
                    <td className="border-b border-border/50 px-3 py-3 text-text-muted">{deal.current_phase || '未开始'}</td>
                    <td className="border-b border-border/50 px-3 py-3">
                      {deal.final_decision ? (
                        <StatusBadge tone="success" icon={CheckCircle2}>
                          {deal.final_decision}
                          {typeof deal.final_score === 'number' ? ` · ${deal.final_score}` : ''}
                        </StatusBadge>
                      ) : (
                        <span className="text-text-muted">未生成</span>
                      )}
                    </td>
                    <td className="border-b border-border/50 px-3 py-3 text-text-muted">{formatUpdatedAt(deal.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </PageSection>
    </PageShell>
  )
}
