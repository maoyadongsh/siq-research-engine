import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, BriefcaseBusiness, FileJson, FileSearch, FileText, FolderOpen, GitBranch, ShieldCheck, UsersRound } from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { fetchDeal, fetchDealStatus } from '@/lib/dealApi'
import type { DealDetailResponse, DealStatusComponent, DealStatusResponse } from '@/lib/dealTypes'

function valueText(value: unknown) {
  if (value === null || value === undefined || value === '') return '未设置'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function statusTone(status?: string | null): 'neutral' | 'info' | 'success' | 'warning' | 'error' {
  if (!status || status === 'draft') return 'neutral'
  if (status === 'pass' || status === 'r4_completed' || status === 'archived' || status === 'closed') return 'success'
  if (status === 'fail' || status.includes('blocked') || status.includes('fail')) return 'error'
  if (status === 'warn' || status === 'missing' || status.includes('review') || status.includes('risk')) return 'warning'
  return 'info'
}

function compactList(values?: string[], limit = 2) {
  if (!Array.isArray(values) || values.length === 0) return ''
  const shown = values.slice(0, limit).join(', ')
  return values.length > limit ? `${shown} +${values.length - limit}` : shown
}

function componentPath(dealId: string, href?: string | null) {
  if (!href) return ''
  if (href.startsWith('/')) return href
  return `/deals/${encodeURIComponent(dealId)}/${href.replace(/^\/+/, '')}`
}

export default function DealWorkspace() {
  const { dealId = '' } = useParams()
  const [data, setData] = useState<DealDetailResponse | null>(null)
  const [statusData, setStatusData] = useState<DealStatusResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [statusError, setStatusError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      setLoading(true)
      setError('')
      setStatusError('')
      try {
        const [detailResult, statusResult] = await Promise.allSettled([
          fetchDeal(dealId, controller.signal),
          fetchDealStatus(dealId, controller.signal),
        ])
        if (detailResult.status === 'rejected') throw detailResult.reason
        setData(detailResult.value)
        if (statusResult.status === 'fulfilled') {
          setStatusData(statusResult.value)
        } else {
          setStatusData(null)
          setStatusError(statusResult.reason instanceof Error ? statusResult.reason.message : 'Deal Status 加载失败')
        }
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : '项目加载失败')
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false)
        }
      }
    })()
    return () => controller.abort()
  }, [dealId])

  const summary = data?.summary
  const workflow = data?.workflow
  const phases = workflow?.phases ? Object.entries(workflow.phases) : []
  const statusComponents = Array.isArray(statusData?.components) ? statusData.components : []

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={BriefcaseBusiness}
        eyebrow="Deal Workspace"
        title={summary?.company_name || dealId || '交易项目'}
        description="一级市场项目包、投委会阶段和归档材料的只读工作台。"
        actions={
          <Button asChild variant="secondary">
            <Link to="/deals">
              <ArrowLeft />
              返回列表
            </Link>
          </Button>
        }
      />

      {error ? (
        <PageSection>
          <EmptyState title="项目加载失败" description={error} />
        </PageSection>
      ) : loading ? (
        <div className="grid gap-3">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="h-20 animate-pulse rounded-lg bg-muted/60" />
          ))}
        </div>
      ) : !summary ? (
        <PageSection>
          <EmptyState title="项目不存在" description="没有找到对应的 deal package。" />
        </PageSection>
      ) : (
        <>
          <div className="primary-market-metric-grid primary-market-metric-grid-emphasis-first grid gap-3 lg:grid-cols-4">
            <Surface kind="card" className="lg:col-span-2">
              <p className="text-sm text-text-muted">项目 ID</p>
              <p className="mt-1 break-all text-lg font-semibold text-text">{summary.deal_id}</p>
              {summary.legacy_project_id ? (
                <p className="mt-2 text-xs text-text-muted">历史来源: {summary.legacy_project_id}</p>
              ) : null}
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">状态</p>
              <div className="mt-2">
                <StatusBadge tone={statusTone(summary.status)}>{valueText(summary.status)}</StatusBadge>
              </div>
            </Surface>
            <Surface kind="card">
              <p className="text-sm text-text-muted">投决</p>
              <p className="mt-1 text-lg font-semibold text-text">
                {summary.final_decision ? `${summary.final_decision}${typeof summary.final_score === 'number' ? ` · ${summary.final_score}` : ''}` : '未生成'}
              </p>
            </Surface>
          </div>

          <PageSection
            title="Deal Status"
            description={statusData ? `Generated: ${valueText(statusData.generated_at)}` : '聚合 preflight、R1-R4 和审计链状态。'}
            actions={statusData ? <StatusBadge tone={statusTone(statusData.status)}>{valueText(statusData.status)}</StatusBadge> : null}
          >
            {statusError ? (
              <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-text">
                {statusError}
              </div>
            ) : null}
            {statusData ? (
              <div className="space-y-3">
                <div className="grid gap-3 md:grid-cols-3">
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Ready</p>
                    <div className="mt-2">
                      <StatusBadge tone={statusData.ready_for_next_action ? 'success' : 'warning'}>
                        {statusData.ready_for_next_action ? 'ready' : 'blocked'}
                      </StatusBadge>
                    </div>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Next Action</p>
                    <p className="mt-1 break-all font-semibold text-text">{valueText(statusData.next_action)}</p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Blocking</p>
                    <p className="mt-1 text-2xl font-semibold text-text">{statusData.counts?.blocking ?? 0}</p>
                  </Surface>
                </div>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
                  {statusComponents.map((component: DealStatusComponent) => {
                    const to = componentPath(summary.deal_id, component.href)
                    const body = (
                      <Surface kind="row" padding="sm" className="h-full">
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <p className="font-semibold text-text">{valueText(component.label || component.id)}</p>
                            <p className="mt-1 text-xs text-text-muted">{valueText(component.message)}</p>
                          </div>
                          <StatusBadge tone={statusTone(component.status)}>{valueText(component.status)}</StatusBadge>
                        </div>
                        {component.blocking ? <p className="mt-2 text-xs font-medium text-warning">blocking</p> : null}
                        {compactList(component.warnings) ? (
                          <p className="mt-2 break-words text-xs text-text-muted">{compactList(component.warnings)}</p>
                        ) : null}
                      </Surface>
                    )
                    return to ? (
                      <Link key={component.id} to={to} className="block">
                        {body}
                      </Link>
                    ) : (
                      <div key={component.id}>{body}</div>
                    )
                  })}
                </div>
              </div>
            ) : (
              <EmptyState title="暂无 Deal Status" description="项目详情仍可继续查看。" size="sm" />
            )}
          </PageSection>

          <PageSection title="项目概览">
            <div className="primary-market-metric-grid grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              {[
                ['公司', summary.company_name],
                ['行业', summary.industry],
                ['阶段', summary.stage],
                ['当前轮次', summary.current_phase],
              ].map(([label, value]) => (
                <Surface key={label} kind="muted" padding="sm">
                  <p className="text-xs text-text-muted">{label}</p>
                  <p className="mt-1 font-semibold text-text">{valueText(value)}</p>
                </Surface>
              ))}
            </div>
          </PageSection>

          <PageSection title="投委会阶段">
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {phases.length ? phases.map(([phase, info]) => (
                <Surface key={phase} kind="row" padding="sm">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="font-semibold text-text">{phase}</p>
                      <p className="mt-1 text-xs text-text-muted">{valueText(info.status)}</p>
                    </div>
                    <StatusBadge tone={statusTone(String(info.status || ''))}>{valueText(info.status)}</StatusBadge>
                  </div>
                </Surface>
              )) : (
                <EmptyState title="暂无阶段信息" size="sm" />
              )}
            </div>
          </PageSection>

          <PageSection title="快捷入口">
            <div className="primary-market-shortcut-grid grid gap-3 md:grid-cols-3 xl:grid-cols-7">
              {[
                { to: `/deals/${encodeURIComponent(summary.deal_id)}/data-room`, title: 'Data Room', desc: '上传和管理项目文档', icon: FolderOpen },
                { to: `/deals/${encodeURIComponent(summary.deal_id)}/evidence`, title: 'Evidence', desc: '查看和构建证据包', icon: FileSearch },
                { to: `/deals/${encodeURIComponent(summary.deal_id)}/agents`, title: 'Agents', desc: '查看 IC profile 状态', icon: UsersRound },
                { to: `/deals/${encodeURIComponent(summary.deal_id)}/workflow`, title: 'Workflow', desc: '查看 R0-R4 状态', icon: GitBranch },
                { to: `/deals/${encodeURIComponent(summary.deal_id)}/reports`, title: 'Reports', desc: '查看报告与产物索引', icon: FileJson },
                { to: `/deals/${encodeURIComponent(summary.deal_id)}/decision`, title: 'Decision', desc: '查看最终投决报告', icon: FileText },
                { to: `/deals/${encodeURIComponent(summary.deal_id)}/audit`, title: 'Audit', desc: '查看审计事件链', icon: ShieldCheck },
              ].map((item) => {
                const Icon = item.icon
                return (
                  <Link key={item.to} to={item.to} className="block rounded-lg border border-border bg-card p-4 text-text transition hover:border-primary/30 hover:bg-bg">
                    <Icon className="h-5 w-5 text-primary" />
                    <p className="mt-3 font-semibold">{item.title}</p>
                    <p className="mt-1 text-sm text-text-muted">{item.desc}</p>
                  </Link>
                )
              })}
            </div>
          </PageSection>

          <PageSection title="项目包索引">
            <pre className="max-h-72 overflow-auto rounded-lg bg-muted/60 p-3 text-xs text-text-muted">
              {JSON.stringify(data.manifest, null, 2)}
            </pre>
          </PageSection>
        </>
      )}
    </PageShell>
  )
}
