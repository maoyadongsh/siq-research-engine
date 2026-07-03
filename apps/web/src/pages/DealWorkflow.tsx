import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, GitBranch, Loader2, RefreshCw } from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import {
  dryRunDealWorkflowR1Agent,
  fetchDealDisputes,
  fetchDealPhaseArtifacts,
  fetchDealPreflight,
  fetchDealWorkflow,
  generateDealStartupRetrieval,
} from '@/lib/dealApi'
import type {
  DealAgentTaskDryRunResponse,
  DealPhaseArtifactsResponse,
  DealDisputesResponse,
  DealPreflight,
  DealWorkflowResponse,
} from '@/lib/dealTypes'

function tone(status?: string): 'neutral' | 'info' | 'success' | 'warning' | 'error' {
  if (!status || status === 'pending') return 'neutral'
  if (status === 'completed') return 'success'
  if (status === 'in_progress') return 'info'
  if (status === 'blocked' || status === 'failed') return 'error'
  return 'warning'
}

function preflightTone(status?: string): 'neutral' | 'info' | 'success' | 'warning' | 'error' {
  if (status === 'pass') return 'success'
  if (status === 'warn') return 'warning'
  if (status === 'fail') return 'error'
  return 'neutral'
}

function summaryTone(status?: string): 'neutral' | 'info' | 'success' | 'warning' | 'error' {
  if (status === 'pass') return 'success'
  if (status === 'warn') return 'warning'
  if (status === 'missing') return 'neutral'
  if (status === 'fail') return 'error'
  return 'neutral'
}

function text(value: unknown, fallback = '未记录') {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

function evidenceText(count?: number) {
  return typeof count === 'number' ? String(count) : '0'
}

function listPreview(values?: unknown[]) {
  if (!Array.isArray(values) || values.length === 0) return '无'
  return values.map((value) => text(value)).join(' / ')
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function displayValue(value: unknown, fallback = '未记录') {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function preflightDetailsPreview(details?: Record<string, unknown>) {
  if (!details) return []
  const lines: string[] = []
  const issues = Array.isArray(details.issues) ? details.issues : []
  issues.slice(0, 3).forEach((issue) => {
    const item = asRecord(issue)
    const agentId = text(item.agent_id, 'unknown')
    const missing = Array.isArray(item.missing_or_invalid) ? item.missing_or_invalid.map((value) => text(value)).join(', ') : ''
    const unknown = Array.isArray(item.unknown_evidence_ids) ? item.unknown_evidence_ids.map((value) => text(value)).join(', ') : ''
    lines.push([agentId, missing, unknown ? `unknown: ${unknown}` : ''].filter(Boolean).join(' · '))
  })
  const missingAgents = Array.isArray(details.missing_agents) ? details.missing_agents : []
  if (missingAgents.length) lines.push(`missing_agents: ${missingAgents.slice(0, 5).map((value) => text(value)).join(', ')}`)
  const missingDimensions = Array.isArray(details.missing_dimensions) ? details.missing_dimensions : []
  if (missingDimensions.length) lines.push(`missing_dimensions: ${missingDimensions.slice(0, 5).map((value) => text(value)).join(', ')}`)
  return lines
}

function dryRunOutputContract(dryRun: DealAgentTaskDryRunResponse | null) {
  return asRecord(asRecord(dryRun?.payload).output_contract)
}

export default function DealWorkflow() {
  const { dealId = '' } = useParams()
  const [data, setData] = useState<DealWorkflowResponse | null>(null)
  const [preflight, setPreflight] = useState<DealPreflight | null>(null)
  const [preflightError, setPreflightError] = useState('')
  const [disputesSummary, setDisputesSummary] = useState<DealDisputesResponse | null>(null)
  const [disputesError, setDisputesError] = useState('')
  const [phaseArtifacts, setPhaseArtifacts] = useState<DealPhaseArtifactsResponse | null>(null)
  const [phaseArtifactsError, setPhaseArtifactsError] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [receiptBusy, setReceiptBusy] = useState('')
  const [receiptError, setReceiptError] = useState('')
  const [taskDryRun, setTaskDryRun] = useState<DealAgentTaskDryRunResponse | null>(null)
  const [taskDryRunBusy, setTaskDryRunBusy] = useState('')
  const [taskDryRunError, setTaskDryRunError] = useState('')

  const fetchWorkflowBundle = useCallback(async (signal?: AbortSignal) => {
    const [workflowResult, preflightResult, disputesResult, phaseArtifactsResult] = await Promise.allSettled([
      fetchDealWorkflow(dealId, signal),
      fetchDealPreflight(dealId, signal),
      fetchDealDisputes(dealId, signal),
      fetchDealPhaseArtifacts(dealId, signal),
    ])
    if (workflowResult.status === 'rejected') {
      throw workflowResult.reason
    }
    return {
      workflow: workflowResult.value,
      preflight: preflightResult,
      disputes: disputesResult,
      phaseArtifacts: phaseArtifactsResult,
    }
  }, [dealId])

  const loadWorkflow = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setError('')
    setPreflight(null)
    setPreflightError('')
    setDisputesSummary(null)
    setDisputesError('')
    setPhaseArtifacts(null)
    setPhaseArtifactsError('')
    try {
      const result = await fetchWorkflowBundle(signal)
      setData(result.workflow)
      if (result.preflight.status === 'fulfilled') {
        setPreflight(result.preflight.value.preflight)
      } else {
        setPreflight(null)
        setPreflightError(result.preflight.reason instanceof Error ? result.preflight.reason.message : 'Preflight 加载失败')
      }
      if (result.disputes.status === 'fulfilled') {
        setDisputesSummary(result.disputes.value)
      } else {
        setDisputesSummary(null)
        setDisputesError(result.disputes.reason instanceof Error ? result.disputes.reason.message : 'Disputes summary 加载失败')
      }
      if (result.phaseArtifacts.status === 'fulfilled') {
        setPhaseArtifacts(result.phaseArtifacts.value)
      } else {
        setPhaseArtifacts(null)
        setPhaseArtifactsError(
          result.phaseArtifacts.reason instanceof Error
            ? result.phaseArtifacts.reason.message
            : '阶段产物加载失败',
        )
      }
    } catch (err) {
      if (!signal?.aborted) {
        setError(err instanceof Error ? err.message : 'Workflow 加载失败')
      }
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [fetchWorkflowBundle])

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      await Promise.resolve()
      if (controller.signal.aborted) return
      setLoading(true)
      setError('')
      setPreflight(null)
      setPreflightError('')
      setDisputesSummary(null)
      setDisputesError('')
      setPhaseArtifacts(null)
      setPhaseArtifactsError('')
      try {
        const result = await fetchWorkflowBundle(controller.signal)
        setData(result.workflow)
        if (result.preflight.status === 'fulfilled') {
          setPreflight(result.preflight.value.preflight)
        } else {
          setPreflight(null)
          setPreflightError(result.preflight.reason instanceof Error ? result.preflight.reason.message : 'Preflight 加载失败')
        }
        if (result.disputes.status === 'fulfilled') {
          setDisputesSummary(result.disputes.value)
        } else {
          setDisputesSummary(null)
          setDisputesError(result.disputes.reason instanceof Error ? result.disputes.reason.message : 'Disputes summary 加载失败')
        }
        if (result.phaseArtifacts.status === 'fulfilled') {
          setPhaseArtifacts(result.phaseArtifacts.value)
        } else {
          setPhaseArtifacts(null)
          setPhaseArtifactsError(
            result.phaseArtifacts.reason instanceof Error
              ? result.phaseArtifacts.reason.message
              : '阶段产物加载失败',
          )
        }
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : 'Workflow 加载失败')
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    })()
    return () => controller.abort()
  }, [fetchWorkflowBundle])

  const handleGenerateReceipt = async (agentId: string) => {
    setReceiptBusy(agentId)
    setReceiptError('')
    try {
      await generateDealStartupRetrieval(dealId, agentId, { round_name: 'R1', limit: 10 })
      await loadWorkflow()
    } catch (err) {
      setReceiptError(err instanceof Error ? err.message : 'Startup receipt 生成失败')
    } finally {
      setReceiptBusy('')
    }
  }

  const handleTaskDryRun = async (agentId: string) => {
    setTaskDryRunBusy(agentId)
    setTaskDryRunError('')
    try {
      setTaskDryRun(await dryRunDealWorkflowR1Agent(dealId, agentId, { round_name: 'R1' }))
    } catch (err) {
      setTaskDryRunError(err instanceof Error ? err.message : '任务 dry-run 失败')
    } finally {
      setTaskDryRunBusy('')
    }
  }

  const workflow = data?.workflow
  const phases = workflow?.phases ? Object.entries(workflow.phases) : []
  const agentReports = data?.agent_reports || []
  const r1Readiness = data?.r1_agent_readiness
  const readinessByAgent = new Map((r1Readiness?.agents || []).map((item) => [item.agent_id, item]))
  const startupReceipts = data?.startup_receipts
  const disputes = disputesSummary ? disputesSummary.disputes || [] : data?.disputes || []
  const disputeCounts = disputesSummary?.counts
  const disputeArtifacts = disputesSummary?.artifacts
  const disputeWarnings = disputesSummary?.warnings || []
  const preflightFindings = preflight?.checks.filter((check) => check.status !== 'pass') || []
  const phaseArtifactPhases = phaseArtifacts?.phases || []
  const phaseArtifactWarnings = phaseArtifacts?.warnings || []

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={GitBranch}
        eyebrow="Deal Workflow"
        title={workflow?.company_name || dealId || '投委会流程'}
        description="R0-R4 阶段状态、门禁结果和归档状态。"
        actions={
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="secondary">
              <Link to={`/deals/${encodeURIComponent(dealId)}`}>
                <ArrowLeft />
                返回项目
              </Link>
            </Button>
            <Button type="button" variant="secondary" onClick={() => void loadWorkflow()} disabled={loading}>
              {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              刷新
            </Button>
          </div>
        }
      />

      {receiptError ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          {receiptError}
        </div>
      ) : null}

      {taskDryRunError ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          {taskDryRunError}
        </div>
      ) : null}

      {error ? (
        <PageSection>
          <EmptyState title="Workflow 加载失败" description={error} />
        </PageSection>
      ) : loading ? (
        <div className="h-40 animate-pulse rounded-lg bg-muted/60" />
      ) : !workflow ? (
        <PageSection>
          <EmptyState title="暂无 Workflow" description="项目包中没有 workflow_state.json。" />
        </PageSection>
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-3">
            <Surface kind="card">
              <p className="text-sm text-text-muted">当前阶段</p>
              <p className="mt-1 text-xl font-semibold text-text">{text(workflow.current_phase)}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">状态</p>
              <div className="mt-2">
                <StatusBadge tone={tone(workflow.status)}>{text(workflow.status)}</StatusBadge>
              </div>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">最终结果</p>
              <p className="mt-1 text-xl font-semibold text-text">
                {workflow.final_decision ? `${workflow.final_decision}${typeof workflow.final_score === 'number' ? ` · ${workflow.final_score}` : ''}` : '未生成'}
              </p>
            </Surface>
          </div>

          <PageSection title="阶段状态">
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {phases.map(([phase, info]) => (
                <Surface key={phase} kind="row" padding="sm">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="font-semibold text-text">{phase}</p>
                      <p className="mt-1 text-xs text-text-muted">
                        {text(info.started_at)} → {text(info.completed_at)}
                      </p>
                    </div>
                    <StatusBadge tone={tone(String(info.status || ''))}>{text(info.status)}</StatusBadge>
                  </div>
                </Surface>
              ))}
            </div>
          </PageSection>

          <PageSection
            title="阶段产物"
            actions={
              phaseArtifacts ? (
                <StatusBadge tone={summaryTone(phaseArtifacts.status)}>
                  {text(phaseArtifacts.status)}
                </StatusBadge>
              ) : phaseArtifactsError ? (
                <StatusBadge tone="warning">non-blocking</StatusBadge>
              ) : null
            }
          >
            <div className="space-y-3">
              {phaseArtifactsError ? (
                <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-warning">
                  阶段产物加载失败，不影响 Workflow 展示：{phaseArtifactsError}
                </div>
              ) : null}

              {phaseArtifacts ? (
                <div className="space-y-3">
                  <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
                    {Object.entries(phaseArtifacts.counts || {}).map(([key, value]) => (
                      <Surface key={key} kind="muted" padding="sm">
                        <p className="text-xs text-text-muted">{key}</p>
                        <p className="mt-1 font-semibold text-text">{displayValue(value, '0')}</p>
                      </Surface>
                    ))}
                  </div>

                  {phaseArtifactWarnings.length ? (
                    <Surface kind="muted" padding="sm">
                      <p className="text-sm font-semibold text-text">Warnings</p>
                      <div className="mt-2 grid gap-1">
                        {phaseArtifactWarnings.map((warning, index) => (
                          <p key={`${warning}-${index}`} className="break-all font-mono text-xs text-text-muted">
                            {displayValue(warning)}
                          </p>
                        ))}
                      </div>
                    </Surface>
                  ) : null}

                  {phaseArtifactPhases.length ? (
                    <div className="grid gap-3 xl:grid-cols-2">
                      {phaseArtifactPhases.map((phase, index) => {
                        const phaseKey = phase.phase || `phase-${index}`
                        const jsonArtifact = phase.artifacts?.json
                        const markdownArtifact = phase.artifacts?.markdown
                        const phaseCounts = Object.entries(phase.counts || {})
                        const phaseWarnings = phase.warnings || []
                        const itemsPreview = phase.items_preview || []
                        return (
                          <Surface key={`${phaseKey}-${index}`} kind="row" padding="sm">
                            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                              <div className="min-w-0">
                                <div className="flex flex-wrap items-center gap-2">
                                  <p className="font-semibold text-text">
                                    {text(phase.phase)} · {text(phase.label)}
                                  </p>
                                  <StatusBadge tone={summaryTone(phase.status)}>
                                    {text(phase.status)}
                                  </StatusBadge>
                                  {phase.blocking ? (
                                    <StatusBadge tone="warning">blocking</StatusBadge>
                                  ) : null}
                                </div>
                                <p className="mt-1 text-xs text-text-muted">
                                  mode: {text(phase.mode)}
                                  {phase.skip_reason ? ` · skip: ${phase.skip_reason}` : ''}
                                </p>
                              </div>
                              <div className="flex flex-wrap gap-2">
                                <StatusBadge tone={jsonArtifact?.available ? 'success' : 'neutral'}>
                                  JSON · {jsonArtifact?.available ? 'available' : 'missing'}
                                </StatusBadge>
                                <StatusBadge tone={markdownArtifact?.available ? 'success' : 'neutral'}>
                                  Markdown · {markdownArtifact?.available ? 'available' : 'missing'}
                                </StatusBadge>
                              </div>
                            </div>

                            <div className="mt-3 grid gap-3 md:grid-cols-2">
                              <Surface kind="muted" padding="sm">
                                <p className="text-sm font-semibold text-text">Artifacts</p>
                                <div className="mt-2 grid gap-1 text-xs text-text-muted">
                                  <p className="break-all font-mono">JSON: {text(jsonArtifact?.path)}</p>
                                  <p className="break-all font-mono">Markdown: {text(markdownArtifact?.path)}</p>
                                </div>
                              </Surface>

                              <Surface kind="muted" padding="sm">
                                <p className="text-sm font-semibold text-text">Counts</p>
                                {phaseCounts.length ? (
                                  <div className="mt-2 grid grid-cols-2 gap-2 text-sm">
                                    {phaseCounts.map(([key, value]) => (
                                      <div key={key} className="min-w-0">
                                        <p className="text-xs text-text-muted">{key}</p>
                                        <p className="font-semibold text-text">{displayValue(value, '0')}</p>
                                      </div>
                                    ))}
                                  </div>
                                ) : (
                                  <p className="mt-2 text-sm text-text-muted">暂无 counts。</p>
                                )}
                              </Surface>
                            </div>

                            {phaseWarnings.length ? (
                              <div className="mt-3 grid gap-1">
                                {phaseWarnings.map((warning, warningIndex) => (
                                  <p
                                    key={`${phaseKey}-warning-${warningIndex}`}
                                    className="break-all font-mono text-xs text-text-muted"
                                  >
                                    warning: {displayValue(warning)}
                                  </p>
                                ))}
                              </div>
                            ) : null}

                            {itemsPreview.length ? (
                              <div className="mt-3 grid gap-2">
                                {itemsPreview.map((item, itemIndex) => (
                                  <Surface key={`${phaseKey}-item-${itemIndex}`} kind="muted" padding="sm">
                                    <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                                      <div className="min-w-0">
                                        <p className="break-all font-mono text-xs text-text-muted">
                                          {text(item.agent_id, 'unknown_agent')}
                                        </p>
                                        {item.summary ? (
                                          <p className="mt-1 line-clamp-2 text-sm text-text-muted">
                                            {item.summary}
                                          </p>
                                        ) : null}
                                      </div>
                                      <div className="grid min-w-36 grid-cols-2 gap-2 text-sm">
                                        <div>
                                          <p className="text-xs text-text-muted">Score</p>
                                          <p className="font-semibold text-text">{text(item.score)}</p>
                                        </div>
                                        <div>
                                          <p className="text-xs text-text-muted">Rec.</p>
                                          <p className="font-semibold text-text">{text(item.recommendation)}</p>
                                        </div>
                                      </div>
                                    </div>
                                  </Surface>
                                ))}
                              </div>
                            ) : null}
                          </Surface>
                        )
                      })}
                    </div>
                  ) : (
                    <EmptyState title="暂无阶段产物" description="phase-artifacts 响应中没有 phases。" size="sm" />
                  )}
                </div>
              ) : !phaseArtifactsError ? (
                <EmptyState title="暂无阶段产物" size="sm" />
              ) : null}
            </div>
          </PageSection>

          <PageSection
            title="Preflight"
            actions={
              preflight ? (
                <StatusBadge tone={preflightTone(preflight.status)}>
                  {text(preflight.status)}
                </StatusBadge>
              ) : null
            }
          >
            {preflightError ? (
              <EmptyState title="Preflight 加载失败" description={preflightError} size="sm" />
            ) : preflight ? (
              <div className="space-y-3">
                <div className="grid gap-3 sm:grid-cols-4">
                  {Object.entries(preflight.counts || {}).map(([key, value]) => (
                    <Surface key={key} kind="muted" padding="sm">
                      <p className="text-xs text-text-muted">{key}</p>
                      <p className="mt-1 font-semibold text-text">{value}</p>
                    </Surface>
                  ))}
                </div>
                {preflightFindings.length ? (
                  <div className="grid gap-3 md:grid-cols-2">
                    {preflightFindings.map((check) => (
                      <Surface key={check.id} kind="row" padding="sm">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="font-semibold text-text">{check.label}</p>
                            <p className="mt-1 text-sm text-text-muted">{check.message}</p>
                            {preflightDetailsPreview(check.details).length ? (
                              <div className="mt-2 grid gap-1">
                                {preflightDetailsPreview(check.details).map((line, index) => (
                                  <p key={`${check.id}-${index}`} className="break-all font-mono text-xs text-text-muted">
                                    {line}
                                  </p>
                                ))}
                              </div>
                            ) : null}
                          </div>
                          <StatusBadge tone={preflightTone(check.status)}>{check.status}</StatusBadge>
                        </div>
                      </Surface>
                    ))}
                  </div>
                ) : (
                  <EmptyState title="Preflight 通过" description="核心合同、证据门禁和 R4 评分字段满足当前最小要求。" size="sm" />
                )}
              </div>
            ) : (
              <EmptyState title="暂无 Preflight" size="sm" />
            )}
          </PageSection>

          <PageSection
            title="R1 专家摘要"
            actions={
              <div className="flex flex-wrap gap-2">
                {r1Readiness ? (
                  <StatusBadge tone={r1Readiness.next_agent_id ? 'info' : 'neutral'}>
                    Next · {r1Readiness.next_agent_id || '无'}
                  </StatusBadge>
                ) : null}
                {startupReceipts ? (
                  <StatusBadge tone={startupReceipts.count > 0 ? 'success' : 'neutral'}>
                    Startup receipts · {startupReceipts.count}
                  </StatusBadge>
                ) : null}
              </div>
            }
          >
            {agentReports.length ? (
              <div className="grid gap-3 xl:grid-cols-2">
                {agentReports.map((report) => {
                  const readiness = readinessByAgent.get(report.agent_id)
                  const blockingReasons = readiness?.blocking_reasons || []
                  return (
                    <Surface key={report.agent_id} kind="row" padding="sm">
                      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="font-semibold text-text">{report.label || report.agent_id}</p>
                            <StatusBadge tone={report.has_report ? 'success' : 'neutral'}>
                              {report.has_report ? '已报告' : '待报告'}
                            </StatusBadge>
                            <StatusBadge tone={report.has_startup_receipt ? 'success' : 'warning'}>
                              {report.has_startup_receipt ? 'Receipt' : 'No receipt'}
                            </StatusBadge>
                            {readiness ? (
                              <StatusBadge tone={readiness.allowed ? 'success' : 'warning'}>
                                {readiness.allowed ? 'Ready' : 'Blocked'}
                              </StatusBadge>
                            ) : null}
                          </div>
                          <p className="mt-1 break-all font-mono text-xs text-text-muted">{report.agent_id}</p>
                          {blockingReasons.length ? (
                            <div className="mt-2 grid gap-1">
                              {blockingReasons.slice(0, 2).map((reason) => (
                                <p key={reason} className="break-all font-mono text-xs text-text-muted">
                                  {reason}
                                </p>
                              ))}
                            </div>
                          ) : null}
                          {report.summary ? (
                            <p className="mt-2 line-clamp-2 text-sm text-text-muted">{report.summary}</p>
                          ) : null}
                        </div>
                        <div className="grid min-w-40 grid-cols-2 gap-2 text-sm">
                          <div>
                            <p className="text-xs text-text-muted">Score</p>
                            <p className="font-semibold text-text">{text(report.score)}</p>
                          </div>
                          <div>
                            <p className="text-xs text-text-muted">Rec.</p>
                            <p className="font-semibold text-text">{text(report.recommendation)}</p>
                          </div>
                          <div>
                            <p className="text-xs text-text-muted">Verified</p>
                            <p className="font-semibold text-text">{evidenceText(report.verified_count)}</p>
                          </div>
                          <div>
                            <p className="text-xs text-text-muted">Assumed</p>
                            <p className="font-semibold text-text">{evidenceText(report.assumed_count)}</p>
                          </div>
                        </div>
                        <Button
                          type="button"
                          variant={report.has_startup_receipt ? 'outline' : 'secondary'}
                          size="sm"
                          onClick={() => void handleGenerateReceipt(report.agent_id)}
                          disabled={Boolean(receiptBusy)}
                          className="md:self-start"
                        >
                          {receiptBusy === report.agent_id ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                          {report.has_startup_receipt ? '重建 receipt' : '生成 receipt'}
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => void handleTaskDryRun(report.agent_id)}
                          disabled={Boolean(taskDryRunBusy)}
                          className="md:self-start"
                        >
                          {taskDryRunBusy === report.agent_id ? <Loader2 className="animate-spin" /> : <GitBranch />}
                          任务 dry-run
                        </Button>
                      </div>
                      <div className="mt-3 grid gap-2 text-xs text-text-muted md:grid-cols-2">
                        <p className="min-w-0">Open questions: {listPreview(report.open_questions)}</p>
                        <p className="min-w-0">Risk flags: {listPreview(report.risk_flags)}</p>
                      </div>
                    </Surface>
                  )
                })}
              </div>
            ) : (
              <EmptyState title="暂无专家摘要" description="项目包中还没有 R1 reports 或 startup receipts。" size="sm" />
            )}
          </PageSection>

          {taskDryRun ? (
            <PageSection
              title="Agent Task Dry-run"
              description={taskDryRun.agent_id || '未记录'}
              actions={
                <StatusBadge tone={taskDryRun.allowed ? 'success' : 'warning'}>
                  {taskDryRun.allowed ? 'allowed' : 'blocked'}
                </StatusBadge>
              }
            >
              <div className="grid gap-3 md:grid-cols-4">
                <Surface kind="muted" padding="sm">
                  <p className="text-xs text-text-muted">Round</p>
                  <p className="mt-1 font-semibold text-text">{text(taskDryRun.round_name)}</p>
                </Surface>
                <Surface kind="muted" padding="sm">
                  <p className="text-xs text-text-muted">Preflight</p>
                  <p className="mt-1 font-semibold text-text">{text(taskDryRun.preflight_status)}</p>
                </Surface>
                <Surface kind="muted" padding="sm">
                  <p className="text-xs text-text-muted">Hermes</p>
                  <p className="mt-1 font-semibold text-text">{taskDryRun.hermes_called ? 'called' : 'not called'}</p>
                </Surface>
                <Surface kind="muted" padding="sm">
                  <p className="text-xs text-text-muted">Report</p>
                  <p className="mt-1 font-semibold text-text">{taskDryRun.report_written ? 'written' : 'not written'}</p>
                </Surface>
              </div>
              <div className="grid gap-3 lg:grid-cols-2">
                <Surface kind="muted" padding="sm">
                  <p className="text-sm font-semibold text-text">Blocking reasons</p>
                  {taskDryRun.blocking_reasons?.length ? (
                    <div className="mt-3 grid gap-2">
                      {taskDryRun.blocking_reasons.map((reason) => (
                        <p key={reason} className="break-all font-mono text-xs text-text-muted">{reason}</p>
                      ))}
                    </div>
                  ) : (
                    <p className="mt-2 text-sm text-text-muted">暂无 blocking reason。</p>
                  )}
                </Surface>
                <Surface kind="muted" padding="sm">
                  <p className="text-sm font-semibold text-text">Output contract</p>
                  <div className="mt-3 grid gap-2 text-xs text-text-muted">
                    <p className="break-all">JSON: {text(dryRunOutputContract(taskDryRun).json_path)}</p>
                    <p className="break-all">Key: {text(dryRunOutputContract(taskDryRun).json_key)}</p>
                    <p className="break-all">Markdown: {text(dryRunOutputContract(taskDryRun).markdown_path)}</p>
                  </div>
                </Surface>
              </div>
            </PageSection>
          ) : null}

          <PageSection
            title="显性分歧"
            actions={
              disputesSummary ? (
                <StatusBadge tone={summaryTone(disputesSummary.status)}>
                  {text(disputesSummary.status)}
                </StatusBadge>
              ) : disputesError ? (
                <StatusBadge tone="warning">fallback</StatusBadge>
              ) : null
            }
          >
            <div className="space-y-3">
              {disputesError ? (
                <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-warning">
                  Disputes summary 加载失败，已回退到 workflow 内联分歧：{disputesError}
                </div>
              ) : null}

              {disputesSummary ? (
                <div className="space-y-3">
                  <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
                    <Surface kind="muted" padding="sm">
                      <p className="text-xs text-text-muted">Disputes</p>
                      <p className="mt-1 font-semibold text-text">{disputeCounts?.disputes ?? disputes.length}</p>
                    </Surface>
                    <Surface kind="muted" padding="sm">
                      <p className="text-xs text-text-muted">Resolved</p>
                      <p className="mt-1 font-semibold text-text">{disputeCounts?.resolved ?? 0}</p>
                    </Surface>
                    <Surface kind="muted" padding="sm">
                      <p className="text-xs text-text-muted">Unresolved</p>
                      <p className="mt-1 font-semibold text-text">{disputeCounts?.unresolved ?? 0}</p>
                    </Surface>
                    <Surface kind="muted" padding="sm">
                      <p className="text-xs text-text-muted">High</p>
                      <p className="mt-1 font-semibold text-text">{disputeCounts?.high_severity ?? 0}</p>
                    </Surface>
                    <Surface kind="muted" padding="sm">
                      <p className="text-xs text-text-muted">Positions</p>
                      <p className="mt-1 font-semibold text-text">{disputeCounts?.positions ?? 0}</p>
                    </Surface>
                    <Surface kind="muted" padding="sm">
                      <p className="text-xs text-text-muted">Rulings</p>
                      <p className="mt-1 font-semibold text-text">{disputeCounts?.rulings ?? 0}</p>
                    </Surface>
                  </div>

                  <div className="grid gap-3 md:grid-cols-2">
                    {(['json', 'markdown'] as const).map((key) => {
                      const artifact = disputeArtifacts?.[key]
                      return (
                        <Surface key={key} kind="muted" padding="sm">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="text-sm font-semibold text-text">{key.toUpperCase()} artifact</p>
                              <p className="mt-1 break-all font-mono text-xs text-text-muted">{text(artifact?.path)}</p>
                            </div>
                            <StatusBadge tone={artifact?.available ? 'success' : 'neutral'}>
                              {artifact?.available ? 'available' : 'missing'}
                            </StatusBadge>
                          </div>
                        </Surface>
                      )
                    })}
                  </div>

                  {disputeWarnings.length ? (
                    <Surface kind="muted" padding="sm">
                      <p className="text-sm font-semibold text-text">Warnings</p>
                      <div className="mt-2 grid gap-1">
                        {disputeWarnings.map((warning, index) => (
                          <p key={`${warning}-${index}`} className="break-all font-mono text-xs text-text-muted">
                            {warning}
                          </p>
                        ))}
                      </div>
                    </Surface>
                  ) : null}
                </div>
              ) : null}

              {disputes.length ? (
                <div className="grid gap-3 md:grid-cols-2">
                  {disputes.map((dispute, index) => (
                    <Surface key={dispute.dispute_id || index} kind="row" padding="sm">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="font-semibold text-text">{text(dispute.topic)}</p>
                          <p className="mt-1 text-xs text-text-muted">
                            {text(dispute.dimension)} · {text(dispute.severity)} · positions {dispute.position_count ?? 0}
                          </p>
                          {dispute.agent_ids?.length ? (
                            <p className="mt-1 break-all font-mono text-xs text-text-muted">
                              agents: {dispute.agent_ids.join(', ')}
                            </p>
                          ) : null}
                          {dispute.evidence_ids?.length ? (
                            <p className="mt-1 break-all font-mono text-xs text-text-muted">
                              evidence: {dispute.evidence_ids.join(', ')}
                            </p>
                          ) : null}
                        </div>
                        <StatusBadge tone={dispute.resolved ? 'success' : 'warning'}>
                          {dispute.resolved ? '已解决' : '未解决'}
                        </StatusBadge>
                      </div>
                      {dispute.required_followups?.length ? (
                        <div className="mt-3 grid gap-1">
                          {dispute.required_followups.map((followup, followupIndex) => (
                            <p key={`${followup}-${followupIndex}`} className="break-all font-mono text-xs text-text-muted">
                              follow-up: {followup}
                            </p>
                          ))}
                        </div>
                      ) : null}
                      {dispute.chairman_ruling ? (
                        <pre className="mt-3 max-h-32 overflow-auto rounded-md bg-muted/60 p-2 text-xs text-text-muted">
                          {JSON.stringify(dispute.chairman_ruling, null, 2)}
                        </pre>
                      ) : null}
                    </Surface>
                  ))}
                </div>
              ) : (
                <EmptyState title="暂无显性分歧" description="项目包中没有 r1_5_disputes.json 摘要。" size="sm" />
              )}
            </div>
          </PageSection>

          <PageSection title="原始状态 JSON">
            <pre className="max-h-[520px] overflow-auto rounded-lg bg-muted/60 p-3 text-xs text-text-muted">
              {JSON.stringify(workflow, null, 2)}
            </pre>
          </PageSection>
        </>
      )}
    </PageShell>
  )
}
