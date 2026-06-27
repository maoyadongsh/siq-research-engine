import { useEffect, useState } from 'react'
import { Bot, FileText, ShieldCheck } from 'lucide-react'
import { apiJson } from '../lib/apiClient'
import { useAuth, type User } from '../hooks/useAuth'
import { PageState } from '@/components/research/PageState'
import { PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'

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
    <PageShell>
      <PageHeader
        icon={ShieldCheck}
        eyebrow="Account Center"
        title="我的账户"
        description={`查看 ${account?.full_name || account?.username || '当前用户'} 的账号资料、权限状态和今日使用额度。`}
        meta={[
          <StatusBadge key="role" tone="info">{roleLabel(account?.role)}</StatusBadge>,
          <StatusBadge
            key="status"
            tone={account?.approval_status === 'approved' ? 'success' : 'neutral'}
          >
            {account?.approval_status === 'approved' ? '已通过审核' : account?.approval_status || '已登录'}
          </StatusBadge>,
        ]}
      />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_420px]">
        <PageSection title="基本信息" description="账户资料、角色和登录时间。">
          <dl className="mt-5 grid gap-4 md:grid-cols-2">
            {[
              ['用户名', account?.username],
              ['邮箱', account?.email],
              ['姓名', account?.full_name],
              ['角色', roleLabel(account?.role)],
              ['创建时间', formatDate(summary?.user.created_at)],
              ['上次登录', formatDate(summary?.user.last_login)],
            ].map(([label, value]) => (
              <Surface key={label} kind="row" padding="md">
                <dt className="text-xs font-semibold text-text-muted">{label}</dt>
                <dd className="mt-1 break-all text-sm font-semibold text-text">{value || '暂无'}</dd>
              </Surface>
            ))}
          </dl>
        </PageSection>

        <aside className="space-y-5">
          <PageSection
            title="权限状态"
            description="普通用户只能访问自己的工作区数据。"
            actions={<ShieldCheck className="h-5 w-5 text-primary" />}
          >
            <Surface kind="muted" padding="md" className="text-sm leading-6 text-text-muted">
              你生成的产物可以下载；共享财报和解析结果会作为系统缓存复用，但不会展示其他用户的操作记录。
            </Surface>
          </PageSection>

          {summary && (
            <PageSection title="今日额度" description="智能体问答和新解析任务的日用量。">
              <div className="mt-4 space-y-3">
                {[
                  {
                    label: '智能体问答',
                    value: summary.quotas.agentQuestion.limit == null ? '不限' : `${summary.quotas.agentQuestion.used}/${summary.quotas.agentQuestion.limit}`,
                    icon: Bot,
                    trend: summary.quotas.agentQuestion.limit == null ? '管理员不受日额度限制' : `${formatDate(summary.quotas.agentQuestion.resetAt)} 恢复`,
                  },
                  {
                    label: '新解析任务',
                    value: summary.quotas.parseJob.limit == null ? '不限' : `${summary.quotas.parseJob.used}/${summary.quotas.parseJob.limit}`,
                    icon: FileText,
                    trend: summary.quotas.parseJob.limit == null ? '管理员不受日额度限制' : `${formatDate(summary.quotas.parseJob.resetAt)} 恢复`,
                  },
                ].map((item) => (
                  <Surface key={item.label} kind="card" padding="md">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-text-muted">{item.label}</p>
                        <p className="mt-2 text-3xl font-bold text-text">{item.value}</p>
                        <p className="mt-1 text-sm leading-5 text-text-muted">{item.trend}</p>
                      </div>
                      <span className="premium-icon h-10 w-10 shrink-0 rounded-2xl">
                        <item.icon className="h-5 w-5" />
                      </span>
                    </div>
                  </Surface>
                ))}
              </div>
            </PageSection>
          )}
        </aside>
      </div>
    </PageShell>
  )
}
