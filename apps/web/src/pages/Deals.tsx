import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  BriefcaseBusiness,
  CheckCircle2,
  Clock3,
  RefreshCcw,
  Search,
} from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { fetchDeals } from '@/lib/dealApi'
import type { DealListResponse, DealStats, DealSummary } from '@/lib/dealTypes'

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  r0_ready: 'R0 就绪',
  r1_in_progress: 'R1 尽调中',
  r4_completed: 'R4 已归档',
  archived: '已归档',
  closed: '已关闭',
}

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

export default function Deals() {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [data, setData] = useState<DealListResponse>({ deals: [] })
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

  const stats = useMemo(() => deriveStats(data.deals, data.stats), [data.deals, data.stats])

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={BriefcaseBusiness}
        eyebrow="Deal OS"
        title="项目管理"
        description="穿透到单个一级市场项目，查看资料室、证据、智能体、工作流、报告、投决和审计细节。"
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
          <EmptyState icon={BriefcaseBusiness} title="暂无一级市场项目" description="创建项目后会显示在这里。" />
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
