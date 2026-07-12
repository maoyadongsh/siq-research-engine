import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  BriefcaseBusiness,
  CheckCircle2,
  Clock3,
  FileSearch,
  FolderOpen,
  GitBranch,
  Loader2,
  MessageSquareText,
  RefreshCw,
  Search,
  TrendingUp,
} from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { DealListResponse, DealStatusResponse, DealSummary } from '@/lib/dealTypes'
import { fetchPrimaryMarketProjects } from '@/features/primary-market/primaryMarketApi'
import {
  componentPath,
  deriveProjectMetrics,
  deriveProjectRow,
  formatTime,
  phaseLabel,
  statusTone,
  text,
  type PrimaryMarketProjectRow,
} from '@/features/primary-market/primaryMarketViewModel'

function projectHref(path: string, dealId: string) {
  const params = new URLSearchParams({ dealId })
  return `${path}?${params.toString()}`
}

function sortRows(rows: PrimaryMarketProjectRow[]) {
  const rank: Record<PrimaryMarketProjectRow['category'], number> = {
    blocked: 0,
    decision_pending: 1,
    ready: 2,
    in_progress: 3,
    draft: 4,
    completed: 5,
  }
  return [...rows].sort((a, b) => {
    const categoryDelta = rank[a.category] - rank[b.category]
    if (categoryDelta) return categoryDelta
    const aTime = new Date(a.deal.updated_at || '').getTime()
    const bTime = new Date(b.deal.updated_at || '').getTime()
    return (Number.isFinite(bTime) ? bTime : 0) - (Number.isFinite(aTime) ? aTime : 0)
  })
}

function metricCards(metrics: ReturnType<typeof deriveProjectMetrics>) {
  return [
    { label: '项目总数', value: metrics.total, icon: BriefcaseBusiness, tone: 'info' as const },
    { label: '进行中', value: metrics.active, icon: Clock3, tone: 'neutral' as const },
    { label: '阻断中', value: metrics.blocked, icon: AlertTriangle, tone: metrics.blocked ? 'error' as const : 'success' as const },
    { label: '待人工确认', value: metrics.decisionPending, icon: CheckCircle2, tone: metrics.decisionPending ? 'warning' as const : 'neutral' as const },
    { label: '可推进', value: metrics.ready, icon: GitBranch, tone: metrics.ready ? 'success' as const : 'neutral' as const },
  ]
}

function phaseDistribution(rows: PrimaryMarketProjectRow[]) {
  const counts = new Map<string, number>()
  for (const row of rows) {
    const phase = row.phase || '-'
    counts.set(phase, (counts.get(phase) || 0) + 1)
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1])
}

function ProjectActions({ deal, className = '' }: { deal: DealSummary; className?: string }) {
  const dealId = deal.deal_id
  return (
    <div className={`grid grid-cols-2 gap-2 sm:flex sm:flex-wrap xl:grid xl:grid-cols-2 ${className}`}>
      <Button asChild variant="secondary" size="sm" className="justify-start">
        <Link to={projectHref('/primary-market/materials', dealId)}>
          <FolderOpen />
          材料
        </Link>
      </Button>
      <Button asChild variant="secondary" size="sm" className="justify-start">
        <Link to={projectHref('/primary-market/meeting', dealId)}>
          <MessageSquareText />
          投决
        </Link>
      </Button>
      <Button asChild variant="secondary" size="sm" className="justify-start">
        <Link to={projectHref('/primary-market/post-investment', dealId)}>
          <TrendingUp />
          投后
        </Link>
      </Button>
      <Button asChild variant="outline" size="sm" className="justify-start">
        <Link to={`/deals/${encodeURIComponent(dealId)}`}>
          <ArrowRight />
          项目详情
        </Link>
      </Button>
    </div>
  )
}

export default function PrimaryMarketWorkbench() {
  const [queryDraft, setQueryDraft] = useState('')
  const [query, setQuery] = useState('')
  const [data, setData] = useState<DealListResponse>({ deals: [] })
  const [statuses, setStatuses] = useState<Record<string, DealStatusResponse | null>>({})
  const [statusErrors, setStatusErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [reloadKey, setReloadKey] = useState(0)

  const refresh = useCallback(() => {
    setRefreshing(true)
    setReloadKey((value) => value + 1)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      setLoading(true)
      setError('')
      setStatusErrors({})
      try {
        const payload = await fetchPrimaryMarketProjects({ q: query, page: 1, page_size: 50, include_status: true }, controller.signal)
        const deals = Array.isArray(payload.deals) ? payload.deals : []
        setData({ deals, stats: payload.stats })
        if (controller.signal.aborted) return
        setStatuses(payload.status_summaries || {})
      } catch (err) {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '一级市场项目加载失败')
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false)
          setRefreshing(false)
        }
      }
    })()
    return () => controller.abort()
  }, [query, reloadKey])

  const rows = useMemo(
    () => sortRows(data.deals.map((deal) => deriveProjectRow(deal, statuses[deal.deal_id]))),
    [data.deals, statuses],
  )
  const metrics = useMemo(() => deriveProjectMetrics(rows), [rows])
  const blockers = rows.filter((row) => row.category === 'blocked').slice(0, 6)
  const humanQueue = rows.filter((row) => row.category === 'decision_pending').slice(0, 6)
  const phaseCounts = phaseDistribution(rows)

  const submitSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setQuery(queryDraft.trim())
  }

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={BriefcaseBusiness}
        eyebrow="Primary Market"
        title="一级市场工作平台"
        description="查看一级市场项目池、阻断原因、下一步动作与投委会状态。"
        actions={
          <Button type="button" variant="secondary" onClick={refresh} disabled={loading || refreshing}>
            {loading || refreshing ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            刷新
          </Button>
        }
      />

      {error ? (
        <PageSection>
          <EmptyState title="一级市场项目加载失败" description={error} action={<Button onClick={refresh}>重试</Button>} />
        </PageSection>
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            {metricCards(metrics).map((metric) => {
              const Icon = metric.icon
              return (
                <Surface key={metric.label} kind="card">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-sm text-text-muted">{metric.label}</p>
                      <p className="mt-1 text-2xl font-semibold text-text">{metric.value}</p>
                    </div>
                    <span className="premium-icon h-10 w-10 rounded-[10px]">
                      <Icon className="h-5 w-5" />
                    </span>
                  </div>
                  <div className="mt-3">
                    <StatusBadge tone={metric.tone}>{metric.label}</StatusBadge>
                  </div>
                </Surface>
              )
            })}
          </div>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1.7fr)_minmax(320px,0.8fr)]">
            <PageSection
              title="项目池"
              description={`${rows.length} 个一级市场项目。状态失败会降级展示，不阻塞列表。`}
              actions={
                <form onSubmit={submitSearch} className="flex min-w-0 gap-2">
                  <Input
                    value={queryDraft}
                    onChange={(event) => setQueryDraft(event.target.value)}
                    placeholder="搜索公司、行业或 deal id"
                    className="w-56"
                    aria-label="搜索一级市场项目"
                  />
                  <Button type="submit" variant="secondary" disabled={loading}>
                    <Search />
                    搜索
                  </Button>
                </form>
              }
            >
              {loading ? (
                <div className="grid gap-3">
                  {Array.from({ length: 5 }).map((_, index) => (
                    <div key={index} className="h-20 animate-pulse rounded-lg bg-muted/60" />
                  ))}
                </div>
              ) : rows.length === 0 ? (
                <EmptyState icon={BriefcaseBusiness} title="暂无一级市场项目" description="当前 Deal OS 中没有可展示的 deal package。" />
              ) : (
                <div className="grid gap-3">
                  {rows.map((row) => (
                    <Surface key={row.deal.deal_id} kind="row" padding="md" className="overflow-hidden">
                      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                        <div className="min-w-0 flex-1 space-y-4">
                          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                            <div className="min-w-0">
                              <p className="text-base font-semibold leading-6 text-text">{row.deal.company_name || row.deal.deal_id}</p>
                              <p className="mt-1 break-all font-mono text-xs leading-5 text-text-muted">{row.deal.deal_id}</p>
                              <p className="mt-1 text-sm text-text-muted">
                                {[row.deal.industry, row.deal.stage].filter(Boolean).join(' · ') || '未设置'}
                              </p>
                            </div>
                            <div className="flex shrink-0 flex-wrap gap-2 lg:justify-end">
                              <StatusBadge tone={row.phase === 'R4' ? 'success' : 'info'}>{phaseLabel(row.phase)}</StatusBadge>
                              <StatusBadge tone={row.statusTone}>{row.statusLabel}</StatusBadge>
                              <StatusBadge tone={row.ready ? 'success' : 'warning'}>{row.ready ? 'ready' : 'waiting'}</StatusBadge>
                            </div>
                          </div>

                          <div className="grid gap-3 md:grid-cols-[minmax(0,1.1fr)_minmax(220px,0.9fr)_minmax(150px,0.55fr)]">
                            <div className="rounded-lg bg-muted/35 px-3 py-2.5">
                              <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">下一步</p>
                              <p className="mt-1 break-words text-sm leading-6 text-text">{row.nextAction}</p>
                            </div>
                            <div className="rounded-lg bg-muted/35 px-3 py-2.5">
                              <div className="flex items-start justify-between gap-3">
                                <div>
                                  <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">门禁</p>
                                  <p className="mt-1 text-sm font-semibold text-text">阻断 {row.blockingCount}</p>
                                </div>
                                <p className="shrink-0 text-xs leading-5 text-text-muted">warn {row.warningCount} · missing {row.missingCount}</p>
                              </div>
                              {row.blockingMessages[0] ? (
                                <p className="mt-2 break-words text-xs leading-5 text-warning">{row.blockingMessages[0]}</p>
                              ) : null}
                            </div>
                            <div className="rounded-lg bg-muted/35 px-3 py-2.5">
                              <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">更新</p>
                              <p className="mt-1 text-sm leading-6 text-text-muted">{formatTime(row.deal.updated_at)}</p>
                              {row.deal.final_decision ? (
                                <p className="mt-1 text-xs leading-5 text-text-muted">
                                  {row.deal.final_decision}{typeof row.deal.final_score === 'number' ? ` · ${row.deal.final_score}` : ''}
                                </p>
                              ) : null}
                            </div>
                          </div>
                          {statusErrors[row.deal.deal_id] ? (
                            <p className="rounded-lg bg-warning/10 px-3 py-2 text-xs leading-5 text-warning">{statusErrors[row.deal.deal_id]}</p>
                          ) : null}
                        </div>
                        <ProjectActions deal={row.deal} className="xl:w-[240px]" />
                      </div>
                    </Surface>
                  ))}
                </div>
              )}
            </PageSection>

            <div className="space-y-5">
              <PageSection title="待办队列" compact>
                <div className="space-y-3">
                  {blockers.length ? (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">阻断项目</p>
                      {blockers.map((row) => {
                        const firstComponent = row.status?.components?.find((component) => component.blocking)
                        const to = componentPath(row.deal.deal_id, firstComponent?.href) || `/deals/${encodeURIComponent(row.deal.deal_id)}`
                        return (
                          <Surface key={row.deal.deal_id} as={Link} to={to} kind="row" padding="sm" className="block">
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <p className="font-semibold text-text">{row.deal.company_name}</p>
                                <p className="mt-1 break-words text-xs text-text-muted">{row.blockingMessages[0] || row.nextAction}</p>
                              </div>
                              <StatusBadge tone="error">{row.blockingCount}</StatusBadge>
                            </div>
                          </Surface>
                        )
                      })}
                    </div>
                  ) : (
                    <Surface kind="muted" padding="sm">
                      <p className="text-sm text-text-muted">暂无阻断项目。</p>
                    </Surface>
                  )}

                  {humanQueue.length ? (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">人工确认</p>
                      {humanQueue.map((row) => (
                        <Surface key={row.deal.deal_id} as={Link} to={projectHref('/primary-market/meeting', row.deal.deal_id)} kind="row" padding="sm" className="block">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="font-semibold text-text">{row.deal.company_name}</p>
                              <p className="mt-1 text-xs text-text-muted">{text(row.deal.final_decision)} · {text(row.deal.final_score, '-')}</p>
                            </div>
                            <StatusBadge tone="warning">R4</StatusBadge>
                          </div>
                        </Surface>
                      ))}
                    </div>
                  ) : null}
                </div>
              </PageSection>

              <PageSection title="阶段分布" compact>
                {phaseCounts.length ? (
                  <div className="space-y-2">
                    {phaseCounts.map(([phase, count]) => (
                      <div key={phase} className="flex items-center justify-between gap-3 rounded-lg bg-muted/40 px-3 py-2 text-sm">
                        <span className="font-semibold text-text">{phaseLabel(phase)}</span>
                        <StatusBadge tone={statusTone(phase)}>{count}</StatusBadge>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState title="暂无阶段数据" size="sm" />
                )}
              </PageSection>

              <PageSection title="快捷推进" compact>
                <div className="grid gap-2">
                  <Button asChild variant="secondary" className="justify-start">
                    <Link to="/primary-market/materials">
                      <FileSearch />
                      打开材料中心
                    </Link>
                  </Button>
                  <Button asChild variant="secondary" className="justify-start">
                    <Link to="/primary-market/meeting">
                      <MessageSquareText />
                      打开投研决策
                    </Link>
                  </Button>
                  <Button asChild variant="secondary" className="justify-start">
                    <Link to="/primary-market/post-investment">
                      <TrendingUp />
                      打开投后管理
                    </Link>
                  </Button>
                </div>
              </PageSection>
            </div>
          </div>
        </>
      )}
    </PageShell>
  )
}
