import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, GitBranch } from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { fetchDealPreflight, fetchDealWorkflow } from '@/lib/dealApi'
import type { DealPreflight, DealWorkflowResponse } from '@/lib/dealTypes'

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

function text(value: unknown) {
  if (value === null || value === undefined || value === '') return '未记录'
  return String(value)
}

function evidenceText(count?: number) {
  return typeof count === 'number' ? String(count) : '0'
}

function listPreview(values?: unknown[]) {
  if (!Array.isArray(values) || values.length === 0) return '无'
  return values.map((value) => text(value)).join(' / ')
}

export default function DealWorkflow() {
  const { dealId = '' } = useParams()
  const [data, setData] = useState<DealWorkflowResponse | null>(null)
  const [preflight, setPreflight] = useState<DealPreflight | null>(null)
  const [preflightError, setPreflightError] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      setLoading(true)
      setError('')
      setPreflight(null)
      setPreflightError('')
      try {
        const [workflowResult, preflightResult] = await Promise.allSettled([
          fetchDealWorkflow(dealId, controller.signal),
          fetchDealPreflight(dealId, controller.signal),
        ])
        if (workflowResult.status === 'rejected') {
          throw workflowResult.reason
        }
        setData(workflowResult.value)
        if (preflightResult.status === 'fulfilled') {
          setPreflight(preflightResult.value.preflight)
        } else {
          setPreflight(null)
          setPreflightError(preflightResult.reason instanceof Error ? preflightResult.reason.message : 'Preflight 加载失败')
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
  }, [dealId])

  const workflow = data?.workflow
  const phases = workflow?.phases ? Object.entries(workflow.phases) : []
  const agentReports = data?.agent_reports || []
  const startupReceipts = data?.startup_receipts
  const disputes = data?.disputes || []
  const preflightFindings = preflight?.checks.filter((check) => check.status !== 'pass') || []

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={GitBranch}
        eyebrow="Deal Workflow"
        title={workflow?.company_name || dealId || '投委会流程'}
        description="R0-R4 阶段状态、门禁结果和归档状态。"
        actions={
          <Button asChild variant="secondary">
            <Link to={`/deals/${encodeURIComponent(dealId)}`}>
              <ArrowLeft />
              返回项目
            </Link>
          </Button>
        }
      />

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
              startupReceipts ? (
                <StatusBadge tone={startupReceipts.count > 0 ? 'success' : 'neutral'}>
                  Startup receipts · {startupReceipts.count}
                </StatusBadge>
              ) : null
            }
          >
            {agentReports.length ? (
              <div className="grid gap-3 xl:grid-cols-2">
                {agentReports.map((report) => (
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
                        </div>
                        <p className="mt-1 break-all font-mono text-xs text-text-muted">{report.agent_id}</p>
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
                    </div>
                    <div className="mt-3 grid gap-2 text-xs text-text-muted md:grid-cols-2">
                      <p className="min-w-0">Open questions: {listPreview(report.open_questions)}</p>
                      <p className="min-w-0">Risk flags: {listPreview(report.risk_flags)}</p>
                    </div>
                  </Surface>
                ))}
              </div>
            ) : (
              <EmptyState title="暂无专家摘要" description="项目包中还没有 R1 reports 或 startup receipts。" size="sm" />
            )}
          </PageSection>

          <PageSection title="分歧摘要">
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
                      </div>
                      <StatusBadge tone={dispute.resolved ? 'success' : 'warning'}>
                        {dispute.resolved ? '已解决' : '未解决'}
                      </StatusBadge>
                    </div>
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
