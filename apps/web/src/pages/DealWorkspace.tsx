import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, BriefcaseBusiness, FileSearch, FileText, FolderOpen, GitBranch, ShieldCheck } from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { fetchDeal } from '@/lib/dealApi'
import type { DealDetailResponse } from '@/lib/dealTypes'

function valueText(value: unknown) {
  if (value === null || value === undefined || value === '') return '未设置'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function statusTone(status?: string | null): 'neutral' | 'info' | 'success' | 'warning' | 'error' {
  if (!status || status === 'draft') return 'neutral'
  if (status === 'r4_completed' || status === 'archived' || status === 'closed') return 'success'
  if (status.includes('blocked') || status.includes('fail')) return 'error'
  if (status.includes('review') || status.includes('risk')) return 'warning'
  return 'info'
}

export default function DealWorkspace() {
  const { dealId = '' } = useParams()
  const [data, setData] = useState<DealDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      setLoading(true)
      setError('')
      try {
        const payload = await fetchDeal(dealId, controller.signal)
        setData(payload)
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
          <div className="grid gap-3 lg:grid-cols-4">
            <Surface kind="card" className="lg:col-span-2">
              <p className="text-sm text-text-muted">项目 ID</p>
              <p className="mt-1 break-all text-lg font-semibold text-text">{summary.deal_id}</p>
              {summary.legacy_project_id ? (
                <p className="mt-2 text-xs text-text-muted">OpenClaw: {summary.legacy_project_id}</p>
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

          <PageSection title="项目概览">
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
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
            <div className="grid gap-3 md:grid-cols-5">
              {[
                { to: `/deals/${encodeURIComponent(summary.deal_id)}/data-room`, title: 'Data Room', desc: '上传和管理项目文档', icon: FolderOpen },
                { to: `/deals/${encodeURIComponent(summary.deal_id)}/evidence`, title: 'Evidence', desc: '查看和构建证据包', icon: FileSearch },
                { to: `/deals/${encodeURIComponent(summary.deal_id)}/workflow`, title: 'Workflow', desc: '查看 R0-R4 状态', icon: GitBranch },
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
