import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  Bot,
  CheckCircle2,
  Clock3,
  Database,
  FileText,
  FolderKanban,
  FolderOpen,
  History,
  Inbox,
  Loader2,
  ShieldCheck,
  UserRound,
  XCircle,
} from 'lucide-react'
import { apiJson } from '@/shared/api/client'
import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'

type ApprovalStatus = 'pending' | 'approved' | 'rejected'
type Quota = { used: number; limit: number | null; remaining: number | null; resetAt: string }

type UserInfo = {
  id: number
  username: string
  email: string
  full_name: string
  role: string
  approval_status: ApprovalStatus
  approval_note?: string | null
  approved_by?: number | null
  approved_at?: string | null
  is_active: boolean
  created_at?: string | null
  last_login?: string | null
}

type ProjectItem = {
  id: number
  name: string
  company_code?: string | null
  company_name?: string | null
  status: string
  updated_at?: string | null
}

type ArtifactItem = {
  id: number
  type: string
  title: string
  path: string
  source?: string | null
  created_at?: string | null
}

type AuditItem = {
  id: number
  action: string
  resource_type: string
  resource_id: string
  details?: string | null
  ip_address?: string | null
  created_at?: string | null
}

type UserDetailPayload = {
  user: UserInfo
  usage: {
    agentQuestion: Quota
    parseJob: Quota
    totals: Record<string, number>
  }
  workspace: {
    projects: number
    artifacts: number
    recentProjects: ProjectItem[]
    recentArtifacts: ArtifactItem[]
  }
  audit: {
    recentLogs: AuditItem[]
  }
}

const roleLabels: Record<string, string> = {
  super_admin: '超级管理员',
  admin: '管理员',
  analyst: '分析师',
  reviewer: '复核员',
  viewer: '查看者',
}

const statusLabels: Record<ApprovalStatus, string> = {
  pending: '待审核',
  approved: '已通过',
  rejected: '已拒绝',
}

function formatDate(value?: string | null) {
  if (!value) return '暂无'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '暂无'
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function usageText(quota: Quota) {
  return quota.limit == null ? `${quota.used} 次` : `${quota.used}/${quota.limit}`
}

function parseDetails(value?: string | null) {
  if (!value) return ''
  try {
    const parsed = JSON.parse(value)
    return JSON.stringify(parsed, null, 2)
  } catch {
    return value
  }
}

function AuditDetails({ details }: { details?: string | null }) {
  const [expanded, setExpanded] = useState(false)
  const parsed = parseDetails(details)
  const lines = parsed.split('\n')
  const isLong = lines.length > 4
  const display = isLong && !expanded ? lines.slice(0, 4).join('\n') : parsed

  return (
    <>
      <pre className="mt-2 max-h-96 overflow-auto rounded-xl bg-bg px-3 py-2 text-xs leading-5 text-text-muted">{display}</pre>
      {isLong && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="mt-2 text-xs font-semibold text-primary hover:underline"
        >
          {expanded ? '收起' : '展开'}
        </button>
      )}
    </>
  )
}

function StatTile({ label, value, icon: Icon }: { label: string; value: string | number; icon: typeof UserRound }) {
  return (
    <div className="metric-tile p-4 2xl:p-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-semibold text-text-muted">{label}</p>
        <span className="premium-icon h-8 w-8 rounded-xl">
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <p className="mt-3 min-h-[2.35rem] break-words text-[1.8rem] font-bold leading-tight tracking-tight text-text">
        {value}
      </p>
    </div>
  )
}

function QuotaBlock({ title, quota, icon: Icon }: { title: string; quota: Quota; icon: typeof Bot }) {
  const unlimited = quota.limit == null
  return (
    <div className="surface-row px-4 py-3">
      <div className="flex items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-3">
          <span className="premium-icon h-10 w-10 shrink-0 rounded-2xl">
            <Icon className="h-5 w-5" />
          </span>
          <div>
            <p className="text-sm font-semibold text-text">{title}</p>
            <p className="mt-1 text-xs text-text-muted">{unlimited ? '管理员不受日额度限制' : `${formatDate(quota.resetAt)} 恢复`}</p>
          </div>
        </div>
        <p className="text-xl font-bold text-text">{unlimited ? '不限' : usageText(quota)}</p>
      </div>
    </div>
  )
}

export default function UserDetail() {
  const { userId } = useParams()
  const [data, setData] = useState<UserDetailPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let ignore = false
    async function load() {
      setLoading(true)
      setError('')
      try {
        const payload = await apiJson<UserDetailPayload>(`/api/auth/users/${encodeURIComponent(userId || '')}/detail`)
        if (!ignore) setData(payload)
      } catch (err) {
        if (!ignore) setError(err instanceof Error ? err.message : '加载用户详情失败')
      } finally {
        if (!ignore) setLoading(false)
      }
    }
    load()
    return () => { ignore = true }
  }, [userId])

  const usageTotal = useMemo(() => {
    if (!data) return 0
    return Object.values(data.usage.totals || {}).reduce((sum, value) => sum + Number(value || 0), 0)
  }, [data])

  if (loading) {
    return <div className="flex min-h-[360px] items-center justify-center text-text-muted"><Loader2 className="mr-2 h-5 w-5 animate-spin" />正在加载用户详情...</div>
  }

  if (error || !data) {
    return (
      <div className="space-y-5">
        <Link to="/admin/users" className="inline-flex h-11 items-center gap-2 rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text hover:bg-bg">
          <ArrowLeft className="h-4 w-4" />返回用户审批
        </Link>
        <div className="rounded-[22px] border border-error/20 bg-error/5 px-5 py-8 text-sm font-medium text-error">
          {error || '用户详情不存在'}
        </div>
      </div>
    )
  }

  const user = data.user
  const statusIcon = user.approval_status === 'approved'
    ? <CheckCircle2 className="h-4 w-4" />
    : user.approval_status === 'rejected'
      ? <XCircle className="h-4 w-4" />
      : <Clock3 className="h-4 w-4" />

  return (
    <PageShell>
      <PageHeader
        icon={UserRound}
        eyebrow="User Detail"
        title={user.full_name || user.username}
        description={`${user.username} · ${user.email}`}
        meta={
          <>
            <StatusBadge tone="info">{roleLabels[user.role] || user.role}</StatusBadge>
            <StatusBadge tone={user.approval_status === 'approved' ? 'success' : user.approval_status === 'rejected' ? 'error' : 'warning'}>
              {statusIcon}
              {statusLabels[user.approval_status]}
            </StatusBadge>
            <StatusBadge tone={user.is_active ? 'info' : 'neutral'}>{user.is_active ? '启用中' : '已停用'}</StatusBadge>
          </>
        }
        actions={
          <Link to="/admin/users" className="inline-flex h-11 items-center gap-2 rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text hover:bg-bg">
            <ArrowLeft className="h-4 w-4" />返回用户审批
          </Link>
        }
      />

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatTile label="角色" value={roleLabels[user.role] || user.role} icon={ShieldCheck} />
        <StatTile label="项目" value={data.workspace.projects} icon={FolderKanban} />
        <StatTile label="产物" value={data.workspace.artifacts} icon={Database} />
        <StatTile label="累计用量" value={usageTotal} icon={Bot} />
        <StatTile label="审计记录" value={data.audit.recentLogs.length} icon={History} />
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
        <PageSection
          title="账户信息"
          description="注册资料、审批备注和登录时间。"
          actions={<ShieldCheck className="h-5 w-5 text-primary" />}
        >
          <dl className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-2">
            {[
              ['用户名', user.username],
              ['邮箱', user.email],
              ['姓名', user.full_name],
              ['角色', roleLabels[user.role] || user.role],
              ['审批备注', user.approval_note || '暂无'],
              ['审批时间', formatDate(user.approved_at)],
              ['创建时间', formatDate(user.created_at)],
              ['上次登录', formatDate(user.last_login)],
            ].map(([label, value]) => (
              <Surface key={String(label)} kind="row" padding="md">
                <dt className="text-xs font-semibold text-text-muted">{label}</dt>
                <dd className="mt-1 break-all text-sm font-semibold text-text">{String(value || '暂无')}</dd>
              </Surface>
            ))}
          </dl>

          <details className="mt-4">
            <summary className="cursor-pointer text-xs font-semibold text-text-muted hover:text-text">展开技术信息</summary>
            <dl>
              <Surface kind="row" padding="md" className="mt-3">
                <dt className="text-xs font-semibold text-text-muted">用户 ID</dt>
                <dd className="mt-1 break-all text-sm font-semibold text-text">{user.id}</dd>
              </Surface>
            </dl>
          </details>

          <div className="mt-6">
            <h3 className="text-base font-bold text-text">最近项目</h3>
            <div className="mt-3 grid gap-3">
              {data.workspace.recentProjects.length ? data.workspace.recentProjects.map((item) => (
                <Surface key={item.id} kind="row" padding="md">
                  <p className="font-semibold text-text">{item.name}</p>
                  <p className="mt-1 text-sm text-text-muted">{item.company_name || item.company_code || '未绑定公司'} · {formatDate(item.updated_at)}</p>
                </Surface>
              )) : (
                <Surface kind="muted" padding="md" className="border-dashed">
                  <EmptyState icon={FolderOpen} title="暂无项目" description="该用户尚未创建或参与任何项目。" size="sm" />
                </Surface>
              )}
            </div>
          </div>

          <div className="mt-6">
            <h3 className="text-base font-bold text-text">最近产物</h3>
            <div className="mt-3 grid gap-3">
              {data.workspace.recentArtifacts.length ? data.workspace.recentArtifacts.map((item) => (
                <Surface key={item.id} kind="row" padding="md">
                  <div className="flex items-start gap-3">
                    <span className="premium-icon mt-0.5 h-9 w-9 shrink-0 rounded-xl">
                      <FileText className="h-4 w-4" />
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-text">{item.title}</p>
                      <p className="mt-1 break-all text-xs text-text-muted">{item.type} · {item.source || 'workspace'} · {formatDate(item.created_at)}</p>
                    </div>
                  </div>
                </Surface>
              )) : (
                <Surface kind="muted" padding="md" className="border-dashed">
                  <EmptyState icon={Inbox} title="暂无产物" description="该用户尚未生成任何分析产物。" size="sm" />
                </Surface>
              )}
            </div>
          </div>
        </PageSection>

        <aside className="space-y-5">
          <PageSection
            title="今日额度"
            description="智能体问答和解析任务使用情况。"
            actions={<Bot className="h-5 w-5 text-primary" />}
          >
            <div className="mt-4 space-y-3">
              <QuotaBlock title="智能体问答" quota={data.usage.agentQuestion} icon={Bot} />
              <QuotaBlock title="新解析任务" quota={data.usage.parseJob} icon={FileText} />
            </div>
          </PageSection>

          <PageSection
            title="审计时间线"
            description={`最近 ${data.audit.recentLogs.length} 条账户操作记录。`}
            actions={<History className="h-5 w-5 text-primary" />}
          >
            <div className="mt-5 space-y-4">
              {data.audit.recentLogs.length ? data.audit.recentLogs.map((item) => (
                <div key={item.id} className="relative pl-6">
                  <span className="absolute left-0 top-1.5 h-2.5 w-2.5 rounded-full bg-primary" />
                  <Surface kind="row" padding="md">
                    <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                      <p className="text-sm font-semibold text-text">{item.action}</p>
                      <p className="text-xs text-text-muted">{formatDate(item.created_at)}</p>
                    </div>
                    <p className="mt-1 break-all text-xs text-text-muted">{item.resource_type} · {item.resource_id}</p>
                    {item.ip_address && <p className="mt-1 text-xs text-text-muted">IP：{item.ip_address}</p>}
                    {item.details && <AuditDetails details={item.details} />}
                  </Surface>
                </div>
              )) : (
                <Surface kind="muted" padding="md" className="border-dashed">
                  <EmptyState icon={History} title="暂无审计记录" description="该用户近期没有可审计的操作记录。" size="sm" />
                </Surface>
              )}
            </div>
          </PageSection>
        </aside>
      </div>
    </PageShell>
  )
}
