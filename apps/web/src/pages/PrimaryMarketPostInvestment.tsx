import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  BriefcaseBusiness,
  CheckCircle2,
  ClipboardList,
  FileCheck2,
  GitBranch,
  Loader2,
  RefreshCw,
  TrendingUp,
} from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import type {
  DealAuditResponse,
  DealDecisionResponse,
  DealDetailResponse,
  DealEvidenceResponse,
  DealStatusResponse,
  DealSummary,
} from '@/lib/dealTypes'
import {
  fetchPrimaryMarketAudit,
  fetchPrimaryMarketDecision,
  fetchPrimaryMarketEvidence,
  fetchPrimaryMarketProject,
  fetchPrimaryMarketProjects,
  fetchPrimaryMarketProjectStatus,
} from '@/features/primary-market/primaryMarketApi'
import { formatTime, phaseLabel, statusTone, text } from '@/features/primary-market/primaryMarketViewModel'

function updateDealParam(setSearchParams: ReturnType<typeof useSearchParams>[1], dealId: string) {
  const next = new URLSearchParams()
  if (dealId) next.set('dealId', dealId)
  setSearchParams(next, { replace: true })
}

function selectedDecisionLabel(decision?: DealDecisionResponse | null, summary?: DealSummary | null) {
  const contract = decision?.contract
  return text(contract?.decision?.value || contract?.decision?.qualitative || summary?.final_decision, '未形成投决')
}

function selectedScoreLabel(decision?: DealDecisionResponse | null, summary?: DealSummary | null) {
  const score = decision?.contract?.scoring?.final_score ?? summary?.final_score
  return text(score, '-')
}

function confirmationLabel(decision?: DealDecisionResponse | null) {
  const confirmation = decision?.contract?.human_confirmation
  if (confirmation?.confirmed) return '已人工确认'
  if (confirmation?.status) return String(confirmation.status)
  return '待确认'
}

function evidenceCoverage(evidence?: DealEvidenceResponse | null) {
  const quality = evidence?.quality_report
  const dimensions = Array.isArray(quality?.dimensions) ? quality.dimensions.length : 0
  const missing = Array.isArray(quality?.missing_dimensions) ? quality.missing_dimensions.length : 0
  return { dimensions, missing, count: quality?.item_count || evidence?.total_item_count || 0 }
}

function auditCount(audit?: DealAuditResponse | null) {
  return audit?.summary?.counts?.events || audit?.audit?.events?.length || 0
}

export default function PrimaryMarketPostInvestment() {
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedDealId = searchParams.get('dealId') || ''
  const [deals, setDeals] = useState<DealSummary[]>([])
  const [dealsLoading, setDealsLoading] = useState(true)
  const [dealsError, setDealsError] = useState('')
  const [detail, setDetail] = useState<DealDetailResponse | null>(null)
  const [status, setStatus] = useState<DealStatusResponse | null>(null)
  const [decision, setDecision] = useState<DealDecisionResponse | null>(null)
  const [audit, setAudit] = useState<DealAuditResponse | null>(null)
  const [evidence, setEvidence] = useState<DealEvidenceResponse | null>(null)
  const [loadedDealId, setLoadedDealId] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [partialErrors, setPartialErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      setDealsLoading(true)
      setDealsError('')
      try {
        const payload = await fetchPrimaryMarketProjects({}, controller.signal)
        const nextDeals = Array.isArray(payload.deals) ? payload.deals : []
        setDeals(nextDeals)
        if (!selectedDealId && nextDeals[0]?.deal_id) updateDealParam(setSearchParams, nextDeals[0].deal_id)
      } catch (err) {
        if (!controller.signal.aborted) setDealsError(err instanceof Error ? err.message : '项目列表加载失败')
      } finally {
        if (!controller.signal.aborted) setDealsLoading(false)
      }
    })()
    return () => controller.abort()
  }, [selectedDealId, setSearchParams])

  const loadPostInvestment = useCallback(async (signal?: AbortSignal) => {
    if (!selectedDealId) return
    setLoading(true)
    setError('')
    setPartialErrors({})
    try {
      const [detailResult, statusResult, decisionResult, auditResult, evidenceResult] = await Promise.allSettled([
        fetchPrimaryMarketProject(selectedDealId, signal),
        fetchPrimaryMarketProjectStatus(selectedDealId, signal),
        fetchPrimaryMarketDecision(selectedDealId, signal),
        fetchPrimaryMarketAudit(selectedDealId, signal),
        fetchPrimaryMarketEvidence(selectedDealId, { limit: 8 }, signal),
      ])
      if (detailResult.status === 'rejected') throw detailResult.reason
      const errors: Record<string, string> = {}
      setDetail(detailResult.value)
      setStatus(statusResult.status === 'fulfilled' ? statusResult.value : null)
      setDecision(decisionResult.status === 'fulfilled' ? decisionResult.value : null)
      setAudit(auditResult.status === 'fulfilled' ? auditResult.value : null)
      setEvidence(evidenceResult.status === 'fulfilled' ? evidenceResult.value : null)
      setLoadedDealId(selectedDealId)
      if (statusResult.status === 'rejected') errors.status = statusResult.reason instanceof Error ? statusResult.reason.message : '项目状态加载失败'
      if (decisionResult.status === 'rejected') errors.decision = decisionResult.reason instanceof Error ? decisionResult.reason.message : '投决数据加载失败'
      if (auditResult.status === 'rejected') errors.audit = auditResult.reason instanceof Error ? auditResult.reason.message : '审计数据加载失败'
      if (evidenceResult.status === 'rejected') errors.evidence = evidenceResult.reason instanceof Error ? evidenceResult.reason.message : '证据覆盖加载失败'
      setPartialErrors(errors)
    } catch (err) {
      if (!signal?.aborted) setError(err instanceof Error ? err.message : '投后管理状态加载失败')
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [selectedDealId])

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      await Promise.resolve()
      if (!controller.signal.aborted) await loadPostInvestment(controller.signal)
    })()
    return () => controller.abort()
  }, [loadPostInvestment])

  const dataReady = loadedDealId === selectedDealId
  const currentDetail = dataReady ? detail : null
  const currentStatus = dataReady ? status : null
  const currentDecision = dataReady ? decision : null
  const currentAudit = dataReady ? audit : null
  const currentEvidence = dataReady ? evidence : null
  const currentPartialErrors = dataReady ? partialErrors : {}

  const selectedDeal = useMemo(
    () => deals.find((deal) => deal.deal_id === selectedDealId) || currentDetail?.summary || null,
    [deals, currentDetail, selectedDealId],
  )
  const coverage = evidenceCoverage(currentEvidence)
  const blockers = currentStatus?.counts?.blocking || 0
  const warnings = currentStatus?.counts?.warn || 0
  const currentPhase = String(selectedDeal?.current_phase || currentStatus?.current_phase || currentDetail?.workflow?.current_phase || '-')
  const decisionText = selectedDecisionLabel(currentDecision, selectedDeal)
  const scoreText = selectedScoreLabel(currentDecision, selectedDeal)
  const confirmationText = confirmationLabel(currentDecision)
  const recentAudit = currentAudit?.summary?.latest_event

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={TrendingUp}
        eyebrow="Primary Market Post Investment"
        title="一级市场投后管理"
        description="围绕单个项目延续投决后的跟踪、风险、材料和审计闭环。"
        actions={
          <Button type="button" variant="secondary" onClick={() => void loadPostInvestment()} disabled={loading || !selectedDealId}>
            {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            刷新
          </Button>
        }
      />

      <PageSection title="项目推进上下文" compact>
        <div className="primary-market-project-context-grid grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(220px,0.38fr)_minmax(220px,0.38fr)]">
          <label className="min-w-0 space-y-1.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">项目</span>
            <select
              value={selectedDealId}
              onChange={(event) => updateDealParam(setSearchParams, event.target.value)}
              disabled={dealsLoading || !deals.length}
              className="h-10 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
              aria-label="选择一级市场投后项目"
            >
              <option value="">选择项目</option>
              {deals.map((deal) => <option key={deal.deal_id} value={deal.deal_id}>{deal.company_name || deal.deal_id}</option>)}
            </select>
            {dealsError ? <p className="text-xs text-destructive">{dealsError}</p> : null}
          </label>
          <Surface kind="muted" padding="sm">
            <p className="text-xs text-text-muted">当前阶段</p>
            <p className="mt-1 text-xl font-semibold text-text">{phaseLabel(currentPhase)}</p>
            <p className="mt-2 text-xs text-text-muted">{selectedDeal?.company_name || selectedDealId || '-'}</p>
          </Surface>
          <Surface kind="muted" padding="sm">
            <p className="text-xs text-text-muted">投后门禁</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <StatusBadge tone={blockers ? 'error' : warnings ? 'warning' : 'success'}>{blockers ? `${blockers} blocking` : warnings ? `${warnings} warnings` : 'ready'}</StatusBadge>
              <StatusBadge tone={confirmationText === '已人工确认' ? 'success' : 'warning'}>{confirmationText}</StatusBadge>
            </div>
          </Surface>
        </div>
      </PageSection>

      {!selectedDealId ? (
        <PageSection>
          <EmptyState icon={BriefcaseBusiness} title="请选择项目" description="选择一个项目后即可进入投后管理视图。" />
        </PageSection>
      ) : error ? (
        <PageSection>
          <EmptyState title="投后管理状态加载失败" description={error} action={<Button onClick={() => void loadPostInvestment()}>重试</Button>} />
        </PageSection>
      ) : (
        <>
          <div className="primary-market-metric-grid grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <Surface kind="card">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm text-text-muted">最终投决</p>
                  <p className="mt-1 truncate text-2xl font-semibold text-text">{decisionText}</p>
                  <p className="mt-2 text-xs text-text-muted">score {scoreText}</p>
                </div>
                <span className="premium-icon h-10 w-10 rounded-[10px]"><CheckCircle2 className="h-5 w-5" /></span>
              </div>
            </Surface>
            <Surface kind="card">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm text-text-muted">证据覆盖</p>
                  <p className="mt-1 text-2xl font-semibold text-text">{coverage.dimensions}</p>
                  <p className="mt-2 text-xs text-text-muted">{coverage.count} 条 evidence · 缺口 {coverage.missing}</p>
                </div>
                <span className="premium-icon h-10 w-10 rounded-[10px]"><FileCheck2 className="h-5 w-5" /></span>
              </div>
            </Surface>
            <Surface kind="card">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm text-text-muted">状态阻断</p>
                  <p className="mt-1 text-2xl font-semibold text-text">{blockers}</p>
                  <p className="mt-2 text-xs text-text-muted">warning {warnings}</p>
                </div>
                <span className="premium-icon h-10 w-10 rounded-[10px]"><AlertTriangle className="h-5 w-5" /></span>
              </div>
            </Surface>
            <Surface kind="card">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm text-text-muted">审计事件</p>
                  <p className="mt-1 text-2xl font-semibold text-text">{auditCount(currentAudit)}</p>
                  <p className="mt-2 text-xs text-text-muted">{formatTime(recentAudit?.created_at)}</p>
                </div>
                <span className="premium-icon h-10 w-10 rounded-[10px]"><ClipboardList className="h-5 w-5" /></span>
              </div>
            </Surface>
          </div>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
            <PageSection title="投后任务链" description="以当前项目为中心，承接投决后的材料、风险和审计闭环。">
              <div className="grid gap-3 md:grid-cols-2">
                <Surface kind="row" padding="sm">
                  <div className="flex items-start gap-3">
                    <GitBranch className="mt-0.5 h-5 w-5 text-primary" />
                    <div className="min-w-0">
                      <p className="font-semibold text-text">确认投决边界</p>
                      <p className="mt-1 text-sm text-text-muted">核对最终投决、附带条件、人工 override 和投后触发项。</p>
                    </div>
                  </div>
                </Surface>
                <Surface kind="row" padding="sm">
                  <div className="flex items-start gap-3">
                    <FileCheck2 className="mt-0.5 h-5 w-5 text-primary" />
                    <div className="min-w-0">
                      <p className="font-semibold text-text">补齐投后材料</p>
                      <p className="mt-1 text-sm text-text-muted">沉淀交割文件、投后报告、经营数据和后续访谈纪要。</p>
                    </div>
                  </div>
                </Surface>
                <Surface kind="row" padding="sm">
                  <div className="flex items-start gap-3">
                    <AlertTriangle className="mt-0.5 h-5 w-5 text-primary" />
                    <div className="min-w-0">
                      <p className="font-semibold text-text">跟踪风险门禁</p>
                      <p className="mt-1 text-sm text-text-muted">把 R1-R4 风险意见转成投后观察项、阈值和责任人。</p>
                    </div>
                  </div>
                </Surface>
                <Surface kind="row" padding="sm">
                  <div className="flex items-start gap-3">
                    <ClipboardList className="mt-0.5 h-5 w-5 text-primary" />
                    <div className="min-w-0">
                      <p className="font-semibold text-text">保留审计闭环</p>
                      <p className="mt-1 text-sm text-text-muted">将人工确认、补证、重大事项和复盘结论串回项目包。</p>
                    </div>
                  </div>
                </Surface>
              </div>
            </PageSection>

            <div className="space-y-5">
              <PageSection title="快捷入口" compact>
                <div className="grid gap-2">
                  <Button asChild variant="secondary" className="justify-start">
                    <Link to={`/primary-market/materials?dealId=${encodeURIComponent(selectedDealId)}`}>
                      <FileCheck2 />
                      更新项目材料
                    </Link>
                  </Button>
                  <Button asChild variant="secondary" className="justify-start">
                    <Link to={`/primary-market/meeting?dealId=${encodeURIComponent(selectedDealId)}`}>
                      <GitBranch />
                      回到投研决策
                    </Link>
                  </Button>
                  <Button asChild variant="outline" className="justify-start">
                    <Link to={`/deals/${encodeURIComponent(selectedDealId)}/audit`}>
                      <ArrowRight />
                      查看项目审计
                    </Link>
                  </Button>
                </div>
              </PageSection>

              <PageSection title="状态来源" compact>
                <div className="space-y-2 text-sm">
                  <div className="flex items-center justify-between gap-3 rounded-lg bg-muted/40 px-3 py-2">
                    <span className="text-text-muted">Deal status</span>
                    <StatusBadge tone={statusTone(currentStatus?.status)}>{text(currentStatus?.status, '-')}</StatusBadge>
                  </div>
                  <div className="flex items-center justify-between gap-3 rounded-lg bg-muted/40 px-3 py-2">
                    <span className="text-text-muted">Evidence</span>
                    <StatusBadge tone={statusTone(currentEvidence?.quality_report?.status)}>{text(currentEvidence?.quality_report?.status, '-')}</StatusBadge>
                  </div>
                  <div className="flex items-center justify-between gap-3 rounded-lg bg-muted/40 px-3 py-2">
                    <span className="text-text-muted">Audit</span>
                    <StatusBadge tone={statusTone(currentAudit?.summary?.status)}>{text(currentAudit?.summary?.status, '-')}</StatusBadge>
                  </div>
                </div>
                {Object.keys(currentPartialErrors).length ? (
                  <div className="mt-3 space-y-1">
                    {Object.entries(currentPartialErrors).map(([key, message]) => (
                      <p key={key} className="text-xs text-warning">{key}: {message}</p>
                    ))}
                  </div>
                ) : null}
              </PageSection>
            </div>
          </div>
        </>
      )}
    </PageShell>
  )
}
