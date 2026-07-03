import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, FileText } from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { fetchDealDecision } from '@/lib/dealApi'
import type { DealDecisionResponse } from '@/lib/dealTypes'

function asText(value: unknown) {
  if (value === null || value === undefined || value === '') return '未记录'
  return String(value)
}
export default function DealDecision() {
  const { dealId = '' } = useParams()
  const [data, setData] = useState<DealDecisionResponse | null>(null)
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
          <div className="grid gap-3 md:grid-cols-3">
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
