import { useEffect, useState } from 'react'
import { Bot, FileText, ShieldCheck } from 'lucide-react'
import { apiJson } from '../lib/apiClient'
import { useAuth, type User } from '../hooks/useAuth'
import { PageHeader } from '@/components/research/PageHeader'
import { MetricCard } from '@/components/research/MetricCard'
import { PageState } from '@/components/research/PageState'

type Quota = { used: number; limit: number | null; remaining: number | null; resetAt: string }
type WorkspaceSummary = {
  user: User & { created_at?: string; last_login?: string; approval_note?: string }
  quotas: { agentQuestion: Quota; parseJob: Quota }
  stats: { projects: number; artifacts: number; downloads: number; parses: number; reports: number }
}

function formatDate(value?: string) {
  if (!value) return '暂无'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '暂无'
  return date.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function roleLabel(role?: string) {
  return ({
    super_admin: '超级管理员',
    admin: '管理员',
    analyst: '分析师',
    reviewer: '复核员',
    viewer: '普通用户',
  } as Record<string, string>)[role || ''] || role || '未知角色'
}

export default function Account() {
  const { user } = useAuth()
  const [summary, setSummary] = useState<WorkspaceSummary | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiJson<WorkspaceSummary>('/api/workspace/me')
      .then(setSummary)
      .finally(() => setLoading(false))
  }, [])

  const account = summary?.user || user

  if (loading && !account) {
    return (
      <PageState
        state="loading"
        title="正在加载账户信息"
        description="请稍候，正在从后端获取账户详情和使用额度。"
      />
    )
  }

  return (
    <div className="secondary-page min-w-0 overflow-x-hidden">
      <PageHeader
        eyebrow={
          <>
            <ShieldCheck className="h-3.5 w-3.5" />
            Account Center
          </>
        }
        title="我的账户"
        description={`查看 ${account?.full_name || account?.username || '当前用户'} 的账号资料、权限状态和今日使用额度。`}
        meta={[
          <span key="role" className="secondary-step-chip">{roleLabel(account?.role)}</span>,
          <span
            key="status"
            className={`secondary-step-chip ${account?.approval_status === 'approved' ? 'secondary-status-success' : ''}`}
          >
            {account?.approval_status === 'approved' ? '已通过审核' : account?.approval_status || '已登录'}
          </span>,
        ]}
      />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_420px]">
        <section className="apple-card rounded-[28px] p-6">
          <h2 className="text-lg font-semibold text-text">基本信息</h2>
          <dl className="mt-5 grid gap-4 md:grid-cols-2">
            {[
              ['用户名', account?.username],
              ['邮箱', account?.email],
              ['姓名', account?.full_name],
              ['角色', roleLabel(account?.role)],
              ['创建时间', formatDate(summary?.user.created_at)],
              ['上次登录', formatDate(summary?.user.last_login)],
            ].map(([label, value]) => (
              <div key={label} className="rounded-[18px] border border-border bg-card px-4 py-3">
                <dt className="text-xs font-semibold text-text-muted">{label}</dt>
                <dd className="mt-1 break-all text-sm font-semibold text-text">{value || '暂无'}</dd>
              </div>
            ))}
          </dl>
        </section>

        <aside className="space-y-5">
          <section className="apple-card rounded-[28px] p-6">
            <div className="mb-4 flex items-center gap-3">
              <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                <ShieldCheck className="h-5 w-5" />
              </span>
              <div>
                <h2 className="text-lg font-semibold text-text">权限状态</h2>
                <p className="text-sm text-text-muted">普通用户只能访问自己的工作区数据。</p>
              </div>
            </div>
            <div className="rounded-[18px] border border-border bg-card px-4 py-3 text-sm text-text-muted">
              你生成的产物可以下载；共享财报和解析结果会作为系统缓存复用，但不会展示其他用户的操作记录。
            </div>
          </section>

          {summary && (
            <section className="apple-card rounded-[28px] p-6">
              <h2 className="text-lg font-semibold text-text">今日额度</h2>
              <div className="mt-4 space-y-3">
                <MetricCard
                  label="智能体问答"
                  value={summary.quotas.agentQuestion.limit == null ? '不限' : `${summary.quotas.agentQuestion.used}/${summary.quotas.agentQuestion.limit}`}
                  icon={Bot}
                  trend={summary.quotas.agentQuestion.limit == null ? '管理员不受日额度限制' : `${formatDate(summary.quotas.agentQuestion.resetAt)} 恢复`}
                />
                <MetricCard
                  label="新解析任务"
                  value={summary.quotas.parseJob.limit == null ? '不限' : `${summary.quotas.parseJob.used}/${summary.quotas.parseJob.limit}`}
                  icon={FileText}
                  trend={summary.quotas.parseJob.limit == null ? '管理员不受日额度限制' : `${formatDate(summary.quotas.parseJob.resetAt)} 恢复`}
                />
              </div>
            </section>
          )}
        </aside>
      </div>
    </div>
  )
}
