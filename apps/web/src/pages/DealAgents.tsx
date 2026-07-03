import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, FileText, GitBranch, Loader2, RefreshCw, UsersRound } from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { fetchDealAgents } from '@/lib/dealApi'
import type { DealAgentsResponse, DealAgentSummary } from '@/lib/dealTypes'

type BadgeTone = 'neutral' | 'info' | 'success' | 'warning' | 'error'

const EXPECTED_IC_PROFILES: DealAgentSummary[] = [
  {
    agent_id: 'siq_ic_master_coordinator',
    role: 'master_coordinator',
    label: 'SIQ IC Master Coordinator',
    profile_path: 'agents/hermes/profiles/siq_ic_master_coordinator',
    is_r1_agent: false,
    status: 'non_r1',
  },
  {
    agent_id: 'siq_ic_strategist',
    role: 'strategist',
    label: 'SIQ IC Strategist',
    profile_path: 'agents/hermes/profiles/siq_ic_strategist',
    r1_sequence_index: 0,
    is_r1_agent: true,
  },
  {
    agent_id: 'siq_ic_sector_expert',
    role: 'sector_expert',
    label: 'SIQ IC Sector Expert',
    profile_path: 'agents/hermes/profiles/siq_ic_sector_expert',
    r1_sequence_index: 1,
    is_r1_agent: true,
  },
  {
    agent_id: 'siq_ic_finance_auditor',
    role: 'finance_auditor',
    label: 'SIQ IC Finance Auditor',
    profile_path: 'agents/hermes/profiles/siq_ic_finance_auditor',
    r1_sequence_index: 2,
    is_r1_agent: true,
  },
  {
    agent_id: 'siq_ic_legal_scanner',
    role: 'legal_scanner',
    label: 'SIQ IC Legal Scanner',
    profile_path: 'agents/hermes/profiles/siq_ic_legal_scanner',
    r1_sequence_index: 3,
    is_r1_agent: true,
  },
  {
    agent_id: 'siq_ic_risk_controller',
    role: 'risk_controller',
    label: 'SIQ IC Risk Controller',
    profile_path: 'agents/hermes/profiles/siq_ic_risk_controller',
    r1_sequence_index: 4,
    is_r1_agent: true,
  },
  {
    agent_id: 'siq_ic_chairman',
    role: 'chairman',
    label: 'SIQ IC Chairman',
    profile_path: 'agents/hermes/profiles/siq_ic_chairman',
    r1_sequence_index: 5,
    is_r1_agent: true,
  },
]

const EXPECTED_PROFILE_IDS = new Set(EXPECTED_IC_PROFILES.map((profile) => profile.agent_id))

function text(value: unknown, fallback = '未记录') {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

function statusTone(status?: string | null): BadgeTone {
  const value = String(status || '').toLowerCase()
  if (!value || value === 'unknown' || value === 'non_r1') return 'neutral'
  if (value === 'ready' || value === 'pass' || value === 'available' || value === 'ok') return 'success'
  if (value === 'blocked' || value === 'fail' || value === 'failed' || value === 'error') return 'error'
  if (value.includes('blocked') || value.includes('fail') || value.includes('error')) return 'error'
  if (value === 'missing_report' || value === 'missing' || value === 'warn' || value === 'not_returned' || value === 'missing_profile') return 'warning'
  if (value.includes('missing') || value.includes('warn')) return 'warning'
  return 'info'
}

function boolText(value?: boolean, trueText = 'yes', falseText = 'no') {
  if (value === true) return trueText
  if (value === false) return falseText
  return 'unknown'
}

function isR1Agent(agent: DealAgentSummary) {
  if (typeof agent.is_r1_agent === 'boolean') return agent.is_r1_agent
  return typeof agent.r1_sequence_index === 'number' && agent.agent_id !== 'siq_ic_master_coordinator'
}

function r1SequenceLabel(agent: DealAgentSummary) {
  if (typeof agent.r1_sequence_index !== 'number') return '-'
  return String(agent.r1_sequence_index + 1)
}

function agentStatus(agent: DealAgentSummary) {
  if (agent.status) return agent.status
  if (agent.readiness?.allowed === true) return 'ready'
  if (agent.readiness?.allowed === false) return 'blocked'
  if (agent.report?.status) return agent.report.status
  if (agent.readiness?.has_report === false) return 'missing_report'
  if (!isR1Agent(agent)) return 'non_r1'
  return 'unknown'
}

function compactList(values?: string[], limit = 3) {
  if (!Array.isArray(values) || values.length === 0) return ''
  const shown = values.slice(0, limit).join(', ')
  return values.length > limit ? `${shown} +${values.length - limit}` : shown
}

function reportLink(dealId: string, artifactPath?: string | null) {
  const path = artifactPath?.trim()
  if (!path) return ''
  const params = new URLSearchParams({ path })
  return `/deals/${encodeURIComponent(dealId)}/reports?${params.toString()}`
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

function mergeAgents(data: DealAgentsResponse | null, fallbackStatus: string): DealAgentSummary[] {
  const responseAgents = Array.isArray(data?.agents) ? data.agents.filter((agent) => agent.agent_id) : []
  const byId = new Map(responseAgents.map((agent) => [agent.agent_id, agent]))
  const expected = EXPECTED_IC_PROFILES.map((profile) => {
    const agent = byId.get(profile.agent_id)
    if (!agent) return { ...profile, status: fallbackStatus }
    return {
      ...profile,
      ...agent,
      agent_id: agent.agent_id || profile.agent_id,
      role: agent.role ?? profile.role,
      label: agent.label ?? profile.label,
      profile_path: agent.profile_path ?? profile.profile_path,
      r1_sequence_index: agent.r1_sequence_index ?? profile.r1_sequence_index,
      is_r1_agent: agent.is_r1_agent ?? profile.is_r1_agent,
    }
  })
  const extras = responseAgents.filter((agent) => !EXPECTED_PROFILE_IDS.has(agent.agent_id))
  return [...expected, ...extras]
}

function AgentCard({ agent, dealId }: { agent: DealAgentSummary; dealId: string }) {
  const status = agentStatus(agent)
  const artifactTo = reportLink(dealId, agent.report?.artifact_path)
  const blockingReasons = compactList(agent.readiness?.blocking_reasons)
  const warnings = compactList(agent.readiness?.warnings)

  return (
    <Surface kind="row" padding="sm" className="h-full">
      <div className="flex h-full flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-semibold text-text">{text(agent.label, agent.agent_id)}</p>
            <p className="mt-1 break-all font-mono text-xs text-text-muted">{agent.agent_id}</p>
            <p className="mt-1 break-all text-xs text-text-muted">{text(agent.profile_path)}</p>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <StatusBadge tone={statusTone(status)}>{text(status)}</StatusBadge>
            <StatusBadge tone={isR1Agent(agent) ? 'info' : 'neutral'}>
              {isR1Agent(agent) ? `R1 #${r1SequenceLabel(agent)}` : 'non-R1'}
            </StatusBadge>
          </div>
        </div>

        <div className="grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <p className="text-xs text-text-muted">Runtime</p>
            <p className="mt-1 font-semibold text-text">{boolText(agent.runtime?.enabled, 'enabled', 'disabled')}</p>
            <p className="mt-1 break-all text-xs text-text-muted">
              {agent.runtime?.port ? `:${agent.runtime.port}` : text(agent.runtime?.base_url)}
            </p>
          </div>
          <div>
            <p className="text-xs text-text-muted">Preflight</p>
            <p className="mt-1 font-semibold text-text">{text(agent.readiness?.preflight_status)}</p>
            <p className="mt-1 text-xs text-text-muted">Queue: {boolText(agent.readiness?.would_queue)}</p>
          </div>
          <div>
            <p className="text-xs text-text-muted">Report</p>
            <p className="mt-1 font-semibold text-text">{text(agent.report?.status, boolText(agent.readiness?.has_report, 'present', 'missing'))}</p>
            <p className="mt-1 text-xs text-text-muted">
              Score: {text(agent.report?.score, '-')} · Rec: {text(agent.report?.recommendation, '-')}
            </p>
          </div>
          <div>
            <p className="text-xs text-text-muted">Receipt</p>
            <p className="mt-1 font-semibold text-text">{boolText(agent.receipt?.present ?? agent.readiness?.has_startup_receipt, 'present', 'missing')}</p>
            <p className="mt-1 break-all text-xs text-text-muted">{text(agent.receipt?.receipt_id ?? agent.readiness?.startup_receipt_id)}</p>
          </div>
        </div>

        {blockingReasons || warnings ? (
          <div className="grid gap-2 rounded-lg bg-muted/50 p-3 text-xs text-text-muted">
            {blockingReasons ? <p className="break-words">Blocking: {blockingReasons}</p> : null}
            {warnings ? <p className="break-words">Warnings: {warnings}</p> : null}
          </div>
        ) : null}

        <div className="mt-auto flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <Link to={`/deals/${encodeURIComponent(dealId)}/workflow`}>
              <GitBranch />
              Workflow
            </Link>
          </Button>
          {artifactTo ? (
            <Button asChild variant="secondary" size="sm">
              <Link to={artifactTo}>
                <FileText />
                Report
              </Link>
            </Button>
          ) : null}
        </div>
      </div>
    </Surface>
  )
}

export default function DealAgents() {
  const { dealId = '' } = useParams()
  const [data, setData] = useState<DealAgentsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadAgents = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setError('')
    try {
      setData(await fetchDealAgents(dealId, signal))
    } catch (err) {
      if (!signal?.aborted) {
        setData(null)
        setError(err instanceof Error ? err.message : 'Deal Agents 加载失败')
      }
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [dealId])

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      await Promise.resolve()
      if (controller.signal.aborted) return
      await loadAgents(controller.signal)
    })()
    return () => controller.abort()
  }, [loadAgents])

  const agents = useMemo(() => mergeAgents(data, data ? 'missing_profile' : 'unknown'), [data])
  const readyCount = data?.counts?.ready ?? agents.filter((agent) => agentStatus(agent) === 'ready').length
  const blockedCount = data?.counts?.blocked ?? agents.filter((agent) => agentStatus(agent) === 'blocked').length
  const reportCount = data?.counts?.reports ?? agents.filter((agent) => agent.report?.artifact_available || agent.readiness?.has_report).length
  const receiptCount = data?.counts?.receipts ?? agents.filter((agent) => agent.receipt?.present || agent.readiness?.has_startup_receipt).length

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={UsersRound}
        eyebrow="Deal Agents"
        title="IC Agents"
        description={dealId}
        actions={
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="secondary">
              <Link to={`/deals/${encodeURIComponent(dealId)}`}>
                <ArrowLeft />
                返回项目
              </Link>
            </Button>
            <Button asChild variant="outline">
              <Link to={`/deals/${encodeURIComponent(dealId)}/workflow`}>
                <GitBranch />
                Workflow
              </Link>
            </Button>
            <Button asChild variant="outline">
              <Link to={`/deals/${encodeURIComponent(dealId)}/reports`}>
                <FileText />
                Reports
              </Link>
            </Button>
            <Button type="button" variant="secondary" onClick={() => void loadAgents()} disabled={loading}>
              {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              刷新
            </Button>
          </div>
        }
      />

      {error ? (
        <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-text">
          Agents API 加载失败：{error}。下方显示默认 IC profile 清单。
        </div>
      ) : null}

      {loading ? (
        <div className="grid gap-3">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="h-24 animate-pulse rounded-lg bg-muted/60" />
          ))}
        </div>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
            <Surface kind="card">
              <p className="text-sm text-text-muted">Profiles</p>
              <p className="mt-1 text-2xl font-semibold text-text">{data?.counts?.agents ?? agents.length}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">R1 Agents</p>
              <p className="mt-1 text-2xl font-semibold text-text">{data?.counts?.r1_agents ?? agents.filter(isR1Agent).length}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">Ready</p>
              <p className="mt-1 text-2xl font-semibold text-text">{readyCount}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">Blocked</p>
              <p className="mt-1 text-2xl font-semibold text-text">{blockedCount}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">Reports</p>
              <p className="mt-1 text-2xl font-semibold text-text">{reportCount}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">Receipts</p>
              <p className="mt-1 text-2xl font-semibold text-text">{receiptCount}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">Runtime</p>
              <p className="mt-1 text-2xl font-semibold text-text">
                {data?.counts?.runtime_enabled ?? agents.filter((agent) => agent.runtime?.enabled).length}
              </p>
            </Surface>
          </div>

          <PageSection
            title="Profile Status"
            description={data ? `Generated: ${formatTime(data.generated_at)} · ${text(data.schema_version)}` : 'Fallback profile list'}
          >
            {agents.length ? (
              <div className="grid gap-3 lg:grid-cols-2">
                {agents.map((agent) => (
                  <AgentCard key={agent.agent_id} agent={agent} dealId={dealId} />
                ))}
              </div>
            ) : (
              <EmptyState title="暂无 Agent 状态" description="接口未返回 agents 数组。" size="sm" />
            )}
          </PageSection>
        </>
      )}
    </PageShell>
  )
}
