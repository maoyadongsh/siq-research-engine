import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, FileText, Loader2 } from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { fetchDealDecision, postDealDecisionHumanConfirmation } from '@/lib/dealApi'
import type {
  DealDecisionContractArtifact,
  DealDecisionHumanConfirmation,
  DealDecisionHumanConfirmationUpdateResponse,
  DealDecisionResponse,
} from '@/lib/dealTypes'

function asText(value: unknown, fallback = '未记录') {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

function statusTone(status?: string | null): 'neutral' | 'info' | 'success' | 'warning' | 'error' {
  if (status === 'pass' || status === 'available' || status === 'confirmed') return 'success'
  if (status === 'warn' || status === 'missing' || status === 'pending') return 'warning'
  if (status === 'error' || status === 'failed' || status === 'unavailable') return 'error'
  return 'neutral'
}

function compactList(values?: string[]) {
  if (!Array.isArray(values)) return '未记录'
  if (values.length === 0) return '无'
  return values.join(', ')
}

function artifactAvailable(artifact?: DealDecisionContractArtifact | null) {
  if (!artifact) return undefined
  if (typeof artifact.available === 'boolean') return artifact.available
  if (typeof artifact.exists === 'boolean') return artifact.exists
  return undefined
}

function artifactStatus(artifact?: DealDecisionContractArtifact | null) {
  const available = artifactAvailable(artifact)
  if (available === undefined) return '未记录'
  return available ? 'available' : 'missing'
}

function artifactTone(artifact?: DealDecisionContractArtifact | null) {
  const available = artifactAvailable(artifact)
  if (available === undefined) return 'neutral'
  return available ? 'success' : 'warning'
}

function formatBytes(value?: number | null) {
  if (!value || value < 0) return ''
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function artifactMeta(artifact?: DealDecisionContractArtifact | null) {
  if (!artifact) return '未记录'
  const details = [asText(artifact.path)]
  const size = formatBytes(artifact.size_bytes)
  if (size) details.push(size)
  if (artifact.sha256) details.push(`sha ${artifact.sha256.slice(0, 12)}`)
  return details.join(' · ')
}

function confirmationStatus(confirmation?: DealDecisionHumanConfirmation | null) {
  if (!confirmation) return '未记录'
  if (confirmation.status) return confirmation.status
  if (confirmation.confirmed === true) return 'confirmed'
  if (confirmation.confirmed === false) return 'pending'
  return '未记录'
}

export default function DealDecision() {
  const { dealId = '' } = useParams()
  const [data, setData] = useState<DealDecisionResponse | null>(null)
  const [confirmationPreview, setConfirmationPreview] = useState<DealDecisionHumanConfirmationUpdateResponse | null>(null)
  const [confirmationBusy, setConfirmationBusy] = useState(false)
  const [confirmationError, setConfirmationError] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      setLoading(true)
      setError('')
      try {
        setData(await fetchDealDecision(dealId, controller.signal))
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : '投决报告加载失败')
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    })()
    return () => controller.abort()
  }, [dealId])

  const decision = data?.decision || {}
  const contract = data?.contract

  const previewConfirmation = async () => {
    setConfirmationBusy(true)
    setConfirmationError('')
    setConfirmationPreview(null)
    try {
      setConfirmationPreview(await postDealDecisionHumanConfirmation(dealId, {
        status: 'confirmed',
        dry_run: true,
      }))
    } catch (err) {
      setConfirmationError(err instanceof Error ? err.message : '人工确认 dry-run 失败')
    } finally {
      setConfirmationBusy(false)
    }
  }

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={FileText}
        eyebrow="IC Decision"
        title="最终投决报告"
        description="R4 决策、评分、条款条件和投委会报告归档。"
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
          <EmptyState title="投决报告加载失败" description={error} />
        </PageSection>
      ) : loading ? (
        <div className="h-40 animate-pulse rounded-lg bg-muted/60" />
      ) : !data ? (
        <PageSection>
          <EmptyState title="暂无投决报告" description="项目尚未生成 R4 决策。" />
        </PageSection>
      ) : (
        <>
          <div className="primary-market-metric-grid primary-market-metric-grid-emphasis-first grid gap-3 md:grid-cols-3">
            <Surface kind="card">
              <p className="text-sm text-text-muted">决策</p>
              <div className="mt-2">
                <StatusBadge tone={decision.decision === 'pass' ? 'success' : 'neutral'}>{asText(decision.decision)}</StatusBadge>
              </div>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">最终分数</p>
              <p className="mt-1 text-2xl font-semibold text-text">{asText(decision.final_score)}</p>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">报告路径</p>
              <p className="mt-1 break-all text-sm font-semibold text-text">{asText(data.report_path)}</p>
            </Surface>
          </div>

          <PageSection
            title="Decision Contract"
            description={contract ? `Generated: ${asText(contract.generated_at)}` : '后端尚未返回 R4 决策合同摘要。'}
          >
            {contract ? (
              <div className="space-y-3">
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Status</p>
                    <div className="mt-2">
                      <StatusBadge tone={statusTone(contract.status)}>{asText(contract.status)}</StatusBadge>
                    </div>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Human Confirmation</p>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <StatusBadge tone={statusTone(confirmationStatus(contract.human_confirmation))}>
                        {confirmationStatus(contract.human_confirmation)}
                      </StatusBadge>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => void previewConfirmation()}
                        disabled={confirmationBusy}
                      >
                        {confirmationBusy ? <Loader2 className="animate-spin" /> : <FileText />}
                        Dry-run
                      </Button>
                    </div>
                    <p className="mt-2 text-xs text-text-muted">{asText(contract.human_confirmation?.confirmed_at)}</p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Markdown Artifact</p>
                    <div className="mt-2">
                      <StatusBadge tone={artifactTone(contract.artifacts?.markdown)}>
                        {artifactStatus(contract.artifacts?.markdown)}
                      </StatusBadge>
                    </div>
                    <p className="mt-2 break-all text-xs text-text-muted">{artifactMeta(contract.artifacts?.markdown)}</p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">HTML Artifact</p>
                    <div className="mt-2">
                      <StatusBadge tone={artifactTone(contract.artifacts?.html)}>
                        {artifactStatus(contract.artifacts?.html)}
                      </StatusBadge>
                    </div>
                    <p className="mt-2 break-all text-xs text-text-muted">{artifactMeta(contract.artifacts?.html)}</p>
                  </Surface>
                </div>

                {confirmationError ? (
                  <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-text">
                    {confirmationError}
                  </div>
                ) : null}

                {confirmationPreview ? (
                  <Surface kind="row" padding="sm">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-text">Human Confirmation Dry-run</p>
                        <p className="mt-1 text-xs text-text-muted">
                          {confirmationPreview.dry_run ? 'No files were written.' : 'Write requested.'}
                        </p>
                      </div>
                      <StatusBadge tone={confirmationPreview.dry_run ? 'info' : 'warning'}>
                        {confirmationPreview.dry_run ? 'dry-run' : 'write'}
                      </StatusBadge>
                    </div>
                    <pre className="mt-3 max-h-56 overflow-auto rounded-md bg-muted/60 p-3 text-xs text-text-muted">
                      {JSON.stringify(confirmationPreview.human_confirmation, null, 2)}
                    </pre>
                  </Surface>
                ) : null}

                <div className="grid gap-3 lg:grid-cols-2">
                  <Surface kind="row" padding="sm">
                    <p className="text-sm font-semibold text-text">Missing Fields</p>
                    <div className="mt-3 grid gap-2 text-sm text-text-muted">
                      <p>
                        <span className="font-medium text-text">Required:</span>{' '}
                        {compactList(contract.missing_required_fields)}
                      </p>
                      <p>
                        <span className="font-medium text-text">Advisory:</span>{' '}
                        {compactList(contract.missing_advisory_fields)}
                      </p>
                    </div>
                  </Surface>

                  <Surface kind="row" padding="sm">
                    <p className="text-sm font-semibold text-text">Scoring & Decision</p>
                    <div className="mt-3 grid gap-2 text-sm text-text-muted sm:grid-cols-2">
                      <p>
                        <span className="font-medium text-text">Weighted:</span>{' '}
                        {asText(contract.scoring?.weighted_agent_score)}
                      </p>
                      <p>
                        <span className="font-medium text-text">Chairman:</span>{' '}
                        {asText(contract.scoring?.chairman_dimension_score)}
                      </p>
                      <p>
                        <span className="font-medium text-text">Final:</span> {asText(contract.scoring?.final_score)}
                      </p>
                      <p>
                        <span className="font-medium text-text">Value:</span> {asText(contract.decision?.value)}
                      </p>
                      <p className="sm:col-span-2">
                        <span className="font-medium text-text">Qualitative:</span>{' '}
                        {asText(contract.decision?.qualitative)}
                      </p>
                    </div>
                  </Surface>
                </div>
              </div>
            ) : (
              <EmptyState title="暂无 Decision Contract" description="等待后端返回 contract 字段。" size="sm" />
            )}
          </PageSection>

          <PageSection title="报告正文">
            {data.report_markdown ? (
              <pre className="max-h-[640px] whitespace-pre-wrap overflow-auto rounded-lg bg-muted/60 p-4 text-sm leading-6 text-text">
                {data.report_markdown}
              </pre>
            ) : (
              <EmptyState title="没有 Markdown 报告" description="项目包中未找到 decision/IC_DECISION_REPORT.md。" />
            )}
          </PageSection>

          <PageSection title="结构化决策 JSON">
            <pre className="max-h-[420px] overflow-auto rounded-lg bg-muted/60 p-3 text-xs text-text-muted">
              {JSON.stringify(decision, null, 2)}
            </pre>
          </PageSection>
        </>
      )}
    </PageShell>
  )
}
