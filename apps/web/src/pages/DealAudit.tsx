import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, ShieldCheck } from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { fetchDealAudit } from '@/lib/dealApi'
import type { DealAuditEvent, DealAuditResponse } from '@/lib/dealTypes'

function formatEventTime(value?: string) {
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
function eventTitle(event: DealAuditEvent) {
  return String(event.event_type || event.type || 'audit_event')
}

export default function DealAudit() {
  const { dealId = '' } = useParams()
  const [data, setData] = useState<DealAuditResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      setLoading(true)
      setError('')
      try {
        setData(await fetchDealAudit(dealId, controller.signal))
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : '审计链加载失败')
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    })()
    return () => controller.abort()
  }, [dealId])

  const events = Array.isArray(data?.audit.events) ? data.audit.events : []

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={ShieldCheck}
        eyebrow="Deal Audit"
        title="审计链"
        description="项目导入、阶段推进、人工确认和 override 的可追溯事件。"
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
          <EmptyState title="审计链加载失败" description={error} />
        </PageSection>
      ) : loading ? (
        <div className="h-40 animate-pulse rounded-lg bg-muted/60" />
      ) : !data ? (
        <PageSection>
          <EmptyState title="暂无审计链" description="项目包中没有 audit_log.json。" />
        </PageSection>
      ) : (
        <>
          <PageSection title="事件列表">
            {events.length ? (
              <div className="space-y-3">
                {events.map((event, index) => (
                  <Surface key={`${eventTitle(event)}-${index}`} kind="row" padding="sm">
                    <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                      <div className="min-w-0">
                        <p className="font-semibold text-text">{eventTitle(event)}</p>
                        <p className="mt-1 text-xs text-text-muted">{formatEventTime(event.created_at)}</p>
                      </div>
                      <pre className="max-h-36 min-w-0 overflow-auto rounded-md bg-muted/60 p-2 text-xs text-text-muted md:max-w-xl">
                        {JSON.stringify(event, null, 2)}
                      </pre>
                    </div>
                  </Surface>
                ))}
              </div>
            ) : (
              <EmptyState title="暂无审计事件" description="后续导入、运行 agent 和人工确认会写入这里。" />
            )}
          </PageSection>

          <PageSection title="原始审计 JSON">
            <pre className="max-h-[520px] overflow-auto rounded-lg bg-muted/60 p-3 text-xs text-text-muted">
              {JSON.stringify(data.audit, null, 2)}
            </pre>
          </PageSection>
        </>
      )}
    </PageShell>
  )
}
