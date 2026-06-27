import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  BarChart3,
  Building2,
  CheckCircle2,
  FileText,
  LayoutDashboard,
  Loader2,
  Scale,
  Search,
  ShieldCheck,
  TrendingUp,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { apiJson } from '../lib/apiClient'
import { useAuth } from '../hooks/useAuth'
import { isAuthenticatedSourceLink, openAuthenticatedSourceLink } from '../lib/authenticatedSourceLinks'

type Quota = { used: number; limit: number | null; remaining: number | null; resetAt: string }
type WorkspaceProject = {
  id: number
  name: string
  company_code?: string
  company_name?: string
  status: string
  created_at?: string
  updated_at: string
}
type WorkspaceArtifact = {
  id: number | string
  type: string
  key?: string
  title: string
  path: string
  source?: string
  globalArtifactId?: string
  created_at?: string
  createdAt?: string
}
type WorkspaceSummary = {
  quotas: { agentQuestion: Quota; parseJob: Quota; documentParse?: Quota }
  stats: { projects: number; artifacts: number; downloads: number; parses: number; documentParses?: number; reports: number }
  recentArtifacts: WorkspaceArtifact[]
  projects?: WorkspaceProject[]
  artifacts?: WorkspaceArtifact[]
}

const steps = [
  { to: '/search', icon: Search, label: '搜索下载', desc: '查询并下载目标公司财报' },
  { to: '/parse', icon: FileText, label: '财报解析', desc: '智能提取结构化财务数据' },
  { to: '/analysis', icon: BarChart3, label: '智能分析', desc: 'AI 生成深度研究报告' },
  { to: '/verify', icon: ShieldCheck, label: '事实核查', desc: '交叉验证数据与证据链' },
  { to: '/tracking', icon: TrendingUp, label: '持续跟踪', desc: '订阅跟踪事项与预警' },
  { to: '/legal', icon: Scale, label: '法务合规', desc: '展示智能体出具的法律意见书' },
]

const workflowChips = [
  { to: '/search', label: '下载' },
  { to: '/parse', label: '解析' },
  { to: '/analysis', label: '分析' },
  { to: '/verify', label: '核查' },
  { to: '/tracking', label: '跟踪' },
  { to: '/legal', label: '法务' },
]

function formatTime(value?: string | null) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function timeScore(value?: string | null) {
  if (!value) return 0
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 0 : date.getTime()
}

function artifactTime(item: WorkspaceArtifact) {
  return item.createdAt || item.created_at || ''
}

function artifactKind(item: WorkspaceArtifact) {
  const type = (item.type || '').toLowerCase()
  if (type.includes('download')) return 'download'
  if (type.includes('document_parse')) return 'document'
  if (type.includes('parse')) return 'parse'
  if (
    type.includes('report') ||
    type.includes('analysis') ||
    type.includes('fact') ||
    type.includes('tracking') ||
    type.includes('legal')
  ) return 'report'
  return type || 'artifact'
}

type ReportSection = 'analysis' | 'factcheck' | 'tracking' | 'legal'

const reportSectionLabels: Record<ReportSection, string> = {
  analysis: '智能分析',
  factcheck: '事实核查',
  tracking: '持续跟踪',
  legal: '法务合规',
}

const reportSectionRoutes: Record<ReportSection, string> = {
  analysis: '/analysis',
  factcheck: '/verify',
  tracking: '/tracking',
  legal: '/legal',
}

function reportPageTargetFromApiPath(path?: string) {
  if (!path) return ''
  try {
    const url = new URL(path, window.location.origin)
    const parts = url.pathname.split('/').filter(Boolean)
    const companiesIndex = parts.indexOf('companies')
    const companyDir = companiesIndex >= 0 ? parts[companiesIndex + 1] : ''
    const section = companiesIndex >= 0 ? parts[companiesIndex + 2] : ''
    const filename = companiesIndex >= 0 ? parts[companiesIndex + 3] : ''
    if (
      parts[0] !== 'api' ||
      parts[1] !== 'wiki' ||
      !companyDir ||
      !filename ||
      !['analysis', 'factcheck', 'tracking', 'legal'].includes(section)
    ) {
      return ''
    }
    const route = reportSectionRoutes[section as ReportSection]
    const params = new URLSearchParams({
      company: decodeURIComponent(companyDir),
      result: decodeURIComponent(filename),
    })
    return `${route}?${params.toString()}`
  } catch {
    return ''
  }
}

function artifactReportSection(item: WorkspaceArtifact): ReportSection | null {
  const haystack = `${item.type || ''} ${item.source || ''} ${item.key || ''} ${item.path || ''} ${item.title || ''}`.toLowerCase()
  if (haystack.includes('factchecker') || haystack.includes('factcheck') || haystack.includes('/verify')) return 'factcheck'
  if (haystack.includes('tracking') || haystack.includes('/tracking')) return 'tracking'
  if (haystack.includes('legal') || haystack.includes('/legal')) return 'legal'
  if (haystack.includes('analysis') || haystack.includes('/analysis') || artifactKind(item) === 'report') return 'analysis'
  return null
}

function artifactTypeLabel(item: WorkspaceArtifact) {
  const kind = artifactKind(item)
  if (kind === 'report') {
    const section = artifactReportSection(item)
    return section ? reportSectionLabels[section] : '生成报告'
  }
  return ({
    download: '下载材料',
    parse: '解析结果',
    document: '文档解析',
    report: '生成报告',
    artifact: '个人产物',
  } as Record<string, string>)[kind] || item.type || '个人产物'
}

function artifactTarget(item: WorkspaceArtifact) {
  const kind = artifactKind(item)
  if (kind === 'download') return `/api/downloads/report-file?path=${encodeURIComponent(item.path || item.key || '')}`
  if (kind === 'document') return `/documents?task=${encodeURIComponent(item.key || item.globalArtifactId || item.path || '')}`
  if (kind === 'parse') return `/parse?task=${encodeURIComponent(item.key || item.globalArtifactId || item.path || '')}`
  const reportPageTarget = reportPageTargetFromApiPath(item.path)
  if (kind === 'report' && reportPageTarget) return reportPageTarget
  if (kind === 'report' && item.path?.startsWith('/')) return item.path
  if (kind === 'report') return `/analysis?artifact=${encodeURIComponent(item.path || item.key || '')}`
  return '/'
}

function projectName(project?: WorkspaceProject) {
  return project?.company_name || project?.name || '暂无项目数据'
}

function projectCode(project?: WorkspaceProject) {
  return project?.company_code || (project ? `项目 ${project.id}` : '等待个人数据同步')
}

function projectCompanyDir(project?: WorkspaceProject) {
  if (!project) return ''
  const code = project.company_code?.trim()
  const name = project.company_name?.trim()
  if (code && name) return `${code}-${name}`
  const projectNameText = project.name?.trim()
  if (/^[A-Za-z0-9]+-.+/.test(projectNameText || '')) return projectNameText || ''
  return ''
}

function projectRoute(project: WorkspaceProject | undefined, route: string) {
  const companyDir = projectCompanyDir(project)
  return companyDir ? `${route}?company=${encodeURIComponent(companyDir)}` : route
}

function projectTokens(project: WorkspaceProject) {
  return [project.company_code, project.company_name, project.name]
    .filter((value): value is string => Boolean(value && value.trim()))
    .map((value) => value.toLowerCase())
}

function artifactMatchesProject(artifact: WorkspaceArtifact, project: WorkspaceProject) {
  const tokens = projectTokens(project)
  if (!tokens.length) return false
  const haystack = `${artifact.title || ''} ${artifact.path || ''} ${artifact.key || ''} ${artifact.source || ''}`.toLowerCase()
  return tokens.some((token) => haystack.includes(token))
}

function projectArtifactStats(project: WorkspaceProject, artifacts: WorkspaceArtifact[]) {
  const matched = artifacts.filter((item) => artifactMatchesProject(item, project))
  const reportSections = matched.reduce((acc, item) => {
    const section = artifactReportSection(item)
    if (section) acc[section] += 1
    return acc
  }, { analysis: 0, factcheck: 0, tracking: 0, legal: 0 } as Record<ReportSection, number>)
  return {
    total: matched.length,
    download: matched.filter((item) => artifactKind(item) === 'download').length,
    parse: matched.filter((item) => artifactKind(item) === 'parse').length,
    report: matched.filter((item) => artifactKind(item) === 'report').length,
    analysis: reportSections.analysis,
    factcheck: reportSections.factcheck,
    tracking: reportSections.tracking,
    legal: reportSections.legal,
  }
}

function compareProjectsByUpdate(a: WorkspaceProject, b: WorkspaceProject) {
  return timeScore(b.updated_at || b.created_at) - timeScore(a.updated_at || a.created_at)
}

function nextWorkspaceAction(project?: WorkspaceProject, counts?: ReturnType<typeof projectArtifactStats>) {
  if (!project) return { to: '/search', label: '开始搜索报告', hint: '先选择一家公司，建立个人研究对象。' }
  if (!counts?.analysis) return { to: '/search', label: '补齐财报数据', hint: '暂无智能分析，建议先下载并解析财报。' }
  if (!counts.factcheck) return { to: projectRoute(project, '/verify'), label: '进行事实核查', hint: '已有智能分析，下一步检查数据、公式和证据链。' }
  if (!counts.tracking) return { to: projectRoute(project, '/tracking'), label: '开启持续跟踪', hint: '结论已核查，可继续生成跟踪事项和预警。' }
  return { to: projectRoute(project, '/analysis'), label: '查看研究结论', hint: '分析、核查和跟踪材料均已有个人记录。' }
}

export default function MyWorkspace() {
  const { user } = useAuth()
  const [summary, setSummary] = useState<WorkspaceSummary | null>(null)
  const [projects, setProjects] = useState<WorkspaceProject[]>([])
  const [artifacts, setArtifacts] = useState<WorkspaceArtifact[]>([])
  const [loading, setLoading] = useState(true)
  const [recentLoading, setRecentLoading] = useState(true)

  useEffect(() => {
    let ignore = false

    async function load(options: { showLoading?: boolean } = {}) {
      if (options.showLoading !== false) setLoading(true)
      if (options.showLoading !== false) setRecentLoading(true)
      const summaryResult = await apiJson<WorkspaceSummary>('/api/workspace/summary')

      if (ignore) return

      setSummary(summaryResult)
      setProjects(summaryResult.projects || [])
      setArtifacts(summaryResult.artifacts || summaryResult.recentArtifacts || [])

      setLoading(false)
      setRecentLoading(false)
    }

    load().catch(() => {
      if (!ignore) {
        setLoading(false)
        setRecentLoading(false)
      }
    })

    const refresh = () => {
      if (document.visibilityState === 'visible') {
        load({ showLoading: false }).catch(() => {})
      }
    }
    const timer = window.setInterval(refresh, 30000)
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)

    return () => {
      ignore = true
      window.clearInterval(timer)
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [])

  const personalArtifacts = useMemo(() => {
    const source = artifacts.length ? artifacts : (summary?.recentArtifacts || [])
    return [...source].sort((a, b) => timeScore(artifactTime(b)) - timeScore(artifactTime(a)))
  }, [artifacts, summary])

  const stats = useMemo(() => {
    const counts = personalArtifacts.reduce((acc, item) => {
      const kind = artifactKind(item)
      acc[kind] = (acc[kind] || 0) + 1
      return acc
    }, {} as Record<string, number>)

    return {
      projects: Math.max(summary?.stats.projects || 0, projects.length),
      artifacts: Math.max(summary?.stats.artifacts || 0, personalArtifacts.length),
      downloads: Math.max(summary?.stats.downloads || 0, counts.download || 0),
      parses: Math.max(summary?.stats.parses || 0, counts.parse || 0),
      documentParses: Math.max(summary?.stats.documentParses || 0, counts.document || 0),
      reports: Math.max(summary?.stats.reports || 0, counts.report || 0),
    }
  }, [personalArtifacts, projects.length, summary])

  const statCards = useMemo(() => ([
    { label: '我的项目', value: stats.projects, unit: '个' },
    { label: '个人产物', value: stats.artifacts, unit: '份' },
    { label: '下载材料', value: stats.downloads, unit: '份' },
    { label: '解析结果', value: stats.parses, unit: '份' },
    { label: '文档解析', value: stats.documentParses, unit: '份' },
    { label: '生成报告', value: stats.reports, unit: '份' },
  ]), [stats])

  const featuredProjects = useMemo(
    () => [...projects].sort(compareProjectsByUpdate).slice(0, 6),
    [projects]
  )
  const overviewProjects = useMemo(
    () => [...projects].sort(compareProjectsByUpdate).slice(0, 8),
    [projects]
  )
  const recent = useMemo(() => personalArtifacts.slice(0, 8), [personalArtifacts])

  const activeProject = featuredProjects[0]
  const activeCounts = useMemo(
    () => activeProject ? projectArtifactStats(activeProject, personalArtifacts) : undefined,
    [activeProject, personalArtifacts]
  )
  const action = nextWorkspaceAction(activeProject, activeCounts)
  const readiness = useMemo(() => {
    if (!activeProject || !activeCounts) return []
    return [
      { label: '智能分析', ready: activeCounts.analysis > 0, count: activeCounts.analysis, to: projectRoute(activeProject, '/analysis') },
      { label: '事实核查', ready: activeCounts.factcheck > 0, count: activeCounts.factcheck, to: projectRoute(activeProject, '/verify') },
      { label: '持续跟踪', ready: activeCounts.tracking > 0, count: activeCounts.tracking, to: projectRoute(activeProject, '/tracking') },
      { label: '法务合规', ready: activeCounts.legal > 0, count: activeCounts.legal, to: projectRoute(activeProject, '/legal') },
    ]
  }, [activeCounts, activeProject])

  if (loading && !summary) {
    return <div className="flex min-h-[360px] items-center justify-center text-text-muted"><Loader2 className="mr-2 h-5 w-5 animate-spin" />正在加载工作平台...</div>
  }

  return (
    <div className="space-y-6 sm:space-y-8">
      {/* ── Hero + Stats + Active Project ── */}
      <section className="premium-shell hero-band dashboard-hero overflow-hidden rounded-[var(--radius-panel)]">
        <div className="border-b border-border/80 bg-white/48 px-4 py-3 backdrop-blur sm:px-6 sm:py-4">
          <div className="dashboard-hero-header grid items-center gap-3 sm:grid-cols-[1fr_auto] xl:grid-cols-[1fr_360px] 2xl:gap-6 2xl:grid-cols-[1fr_390px]">
            <div className="secondary-kicker w-fit justify-self-start">
              <LayoutDashboard className="h-3.5 w-3.5" />
              Workspace
            </div>
            <div className="secondary-step-row -mx-1 w-full justify-start overflow-x-auto px-1 sm:mx-0 sm:w-auto sm:justify-center sm:overflow-visible sm:px-0 sm:justify-self-end xl:w-full">
              {workflowChips.map((chip) => (
                <Link key={chip.to} to={chip.to} className="secondary-step-chip shrink-0">
                  {chip.label}
                </Link>
              ))}
            </div>
          </div>
        </div>
        <div className="dashboard-hero-body grid gap-4 px-4 py-4 sm:gap-5 sm:px-6 sm:py-5 xl:grid-cols-[1fr_360px] 2xl:gap-6 2xl:grid-cols-[1fr_390px]">
          {/* Left: Title + Stats */}
          <div className="dashboard-hero-main flex flex-col justify-between gap-5 2xl:gap-7">
            <div>
              <div className="page-title-tag"><h1 className="text-[1.45rem] font-bold leading-tight tracking-tight text-text sm:text-[1.75rem] md:text-[2.35rem]">
                工作平台
              </h1></div>
              <p className="mt-3 max-w-3xl text-base leading-7 text-text-muted">
                {user?.full_name || user?.username || '当前用户'} 的项目、材料和智能体产物只在这里汇总，系统已有公开财报会被复用。
              </p>
            </div>

            <div className="hero-illustration-wrap">
              <img
                src="/illustrations/siq-system-map-hero.svg?v=2"
                alt="金融科技个人研究工作台插画"
                className="block h-full w-full object-contain opacity-95"
              />
            </div>

            <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 2xl:grid-cols-6 2xl:gap-4">
              {statCards.map((stat) => (
                <div
                  key={stat.label}
                  className="metric-tile p-3 sm:p-4"
                >
                  <p className="text-xs font-semibold text-text-muted sm:text-sm">{stat.label}</p>
                  <p className="mt-1.5 font-mono text-[1.45rem] font-bold tabular-nums text-text sm:mt-2 sm:text-[1.8rem] md:text-[2rem]">
                    {stat.value}
                    <span className="ml-1 text-sm font-normal text-text-muted sm:ml-1.5 sm:text-base">{stat.unit}</span>
                  </p>
                </div>
              ))}
            </div>
          </div>

          {/* Right: Active Project */}
          <aside className="premium-card dashboard-active-card flex flex-col p-4 sm:p-5">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm font-medium text-text-muted">当前优先研究对象</p>
                <h2 className="mt-2 truncate text-[1.75rem] font-bold tracking-tight text-text">
                  {projectName(activeProject)}
                </h2>
                <p className="mt-1 font-mono text-sm text-text-muted">
                  {projectCode(activeProject)}
                </p>
              </div>
              <span
                className={`premium-icon h-12 w-12 shrink-0 rounded-2xl ${
                  activeProject ? 'text-primary' : 'text-amber-600'
                }`}
              >
                {activeProject ? <Building2 className="h-6 w-6" /> : <AlertTriangle className="h-6 w-6" />}
              </span>
            </div>

            <div className="mt-6 flex-1 space-y-3">
              {readiness.length ? (
                readiness.map((item) => (
                  <Link
                    key={item.label}
                    to={item.to}
                    className="premium-row flex items-center justify-between gap-3 rounded-[16px] px-4 py-3"
                  >
                    <span className="flex items-center gap-2.5 text-sm font-semibold text-text">
                      {item.ready ? (
                        <CheckCircle2 className="h-5 w-5 text-success" />
                      ) : (
                        <AlertTriangle className="h-5 w-5 text-amber-500" />
                      )}
                      {item.label}
                    </span>
                    <span className="font-mono text-sm font-semibold text-text-muted">{item.count || 0}</span>
                  </Link>
                ))
              ) : (
                <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
                  暂无可展示的个人研究状态
                </div>
              )}
            </div>

            <div className="mt-6">
              <p className="text-sm leading-6 text-text-muted">{action.hint}</p>
              <Link
                to={action.to}
                className="mt-4 inline-flex h-11 w-full items-center justify-center rounded-[14px] accent-gradient px-5 text-sm font-semibold text-white shadow-[0_12px_28px_rgba(0,113,227,0.18)] transition-all hover:-translate-y-0.5 hover:brightness-110"
              >
                {action.label}
                <ArrowRight className="ml-2 h-5 w-5" />
              </Link>
            </div>
          </aside>
        </div>
      </section>

      {/* ── Workflow Steps ── */}
      <section className="workflow-step-grid grid grid-cols-2 gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-3 2xl:grid-cols-6">
        {steps.map((step, index) => (
          <Link
            key={step.to}
            to={step.to}
            className="workflow-step-card premium-card group relative flex min-h-[118px] min-w-0 flex-col p-3 text-left transition-all duration-300 hover:-translate-y-0.5 hover:border-primary/25 sm:min-h-[160px] sm:p-5 sm:text-center"
          >
            <div className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-full bg-white font-mono text-[0.68rem] font-bold text-text-muted shadow-md ring-2 ring-card sm:-right-2 sm:-top-2 sm:h-7 sm:w-7 sm:text-xs">
              {index + 1}
            </div>
            <div className="premium-icon h-9 w-9 rounded-xl transition-colors group-hover:text-primary-dark sm:mx-auto sm:h-12 sm:w-12 sm:rounded-2xl">
              <step.icon className="h-5 w-5 sm:h-6 sm:w-6" />
            </div>
            <p className="mt-3 pr-6 text-sm font-bold leading-tight text-text sm:mt-4 sm:pr-0 sm:text-base">{step.label}</p>
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-text-muted sm:mt-1.5 sm:text-sm sm:leading-relaxed">{step.desc}</p>
            <ArrowRight className="mt-auto h-5 w-5 pt-3 text-text-muted opacity-0 transition-all group-hover:translate-x-1 group-hover:text-primary group-hover:opacity-100 sm:mx-auto sm:pt-4" />
          </Link>
        ))}
      </section>

      {/* ── Featured Projects ── */}
      {featuredProjects.length > 0 && (
        <section className="premium-shell rounded-[var(--radius-panel)] p-4 sm:p-6">
          <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 className="text-2xl font-bold tracking-tight text-text">研究对象状态</h2>
              <p className="mt-1.5 text-base text-text-muted">按个人项目最近更新时间排序，优先展示你处理过的研究对象。</p>
            </div>
            <Link
              to="/search"
              className="inline-flex h-11 items-center justify-center rounded-xl border border-border bg-card px-5 text-sm font-semibold text-text shadow-sm transition-colors hover:bg-bg"
            >
              新增研究对象
            </Link>
          </div>
          <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
            {featuredProjects.map((project) => {
              const counts = projectArtifactStats(project, personalArtifacts)
              const projectAction = nextWorkspaceAction(project, counts)
              const items = [
                { label: '分析', ready: counts.analysis > 0, value: counts.analysis },
                { label: '核查', ready: counts.factcheck > 0, value: counts.factcheck },
                { label: '跟踪', ready: counts.tracking > 0, value: counts.tracking },
              ]
              return (
                <div
                  key={project.id}
                  className="premium-card p-4 sm:p-5"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className="truncate text-lg font-bold text-text">{projectName(project)}</h3>
                      <p className="mt-1 font-mono text-sm text-text-muted">{projectCode(project)}</p>
                    </div>
                    <Link
                      to={projectAction.to}
                      className="shrink-0 rounded-xl bg-primary/10 px-3 py-1.5 text-sm font-semibold text-primary transition-colors hover:bg-primary/15"
                    >
                      {projectAction.label}
                    </Link>
                  </div>
                  <div className="mt-5 grid grid-cols-3 gap-3">
                    {items.map((item) => (
                      <div
                        key={item.label}
                        className="rounded-2xl border border-border bg-white/64 px-3 py-3 text-center shadow-sm"
                      >
                        <span
                          className={`flex items-center justify-center gap-1 text-xs font-bold ${
                            item.ready ? 'text-success' : 'text-amber-500'
                          }`}
                        >
                          {item.ready ? (
                            <CheckCircle2 className="h-3.5 w-3.5" />
                          ) : (
                            <AlertTriangle className="h-3.5 w-3.5" />
                          )}
                          {item.label}
                        </span>
                        <p className="mt-2 font-mono text-xl font-bold text-text">{item.value || 0}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {/* ── Recent Tasks + Project Overview ── */}
      <section className="grid gap-4 sm:gap-6 xl:grid-cols-[1fr_380px]">
        <div className="premium-shell rounded-[var(--radius-panel)] p-4 sm:p-6">
          <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 className="text-2xl font-bold tracking-tight text-text">近期任务列表</h2>
              <p className="mt-1.5 text-base text-text-muted">你最近处理或复用的下载、解析和智能体产物。</p>
            </div>
            <span className="inline-flex h-8 items-center rounded-full bg-primary/10 px-4 text-sm font-bold text-primary">
              {recent.length} 条结果
            </span>
          </div>

          {recentLoading ? (
            <div className="flex justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
            </div>
          ) : recent.length === 0 ? (
            <p className="rounded-2xl border border-border bg-bg/60 px-6 py-10 text-center text-text-muted">
              暂无个人任务结果
            </p>
          ) : (
            <div className="overflow-hidden rounded-[20px] border border-border bg-white/70">
              {recent.map((item) => {
                const target = artifactTarget(item)
                const className = 'group flex w-full items-center gap-4 border-b border-border/70 px-5 py-4 text-left transition-colors last:border-b-0 hover:bg-primary/[0.035]'
                const content = (
                  <>
                    <span className="premium-icon h-11 w-11 shrink-0 rounded-xl">
                      <FileText className="h-5 w-5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-base font-bold text-text">
                        {item.title} <span className="font-mono text-sm font-normal text-text-muted">{artifactTypeLabel(item)}</span>
                      </span>
                      <span className="mt-0.5 block truncate text-sm text-text-muted">
                        {artifactTypeLabel(item)} · {item.source || 'workspace'}
                      </span>
                    </span>
                    <span className="hidden shrink-0 rounded-full bg-bg px-3 py-1 text-sm font-semibold text-text-muted md:inline-flex">
                      {formatTime(artifactTime(item))}
                    </span>
                    <ArrowRight className="h-5 w-5 shrink-0 text-text-muted transition-all group-hover:translate-x-1 group-hover:text-primary" />
                  </>
                )
                return isAuthenticatedSourceLink(target) ? (
                  <button
                    key={`${item.type}:${item.id}`}
                    type="button"
                    onClick={() => {
                      void openAuthenticatedSourceLink(target)
                    }}
                    className={className}
                  >
                    {content}
                  </button>
                ) : (
                  <Link
                    key={`${item.type}:${item.id}`}
                    to={target}
                    className={className}
                  >
                    {content}
                  </Link>
                )
              })}
            </div>
          )}
        </div>

        <aside className="premium-shell rounded-[var(--radius-panel)] p-4 sm:p-5 2xl:p-6">
          <div className="mb-6 flex items-center gap-4">
            <div className="premium-icon h-12 w-12 rounded-2xl">
              <Building2 className="h-6 w-6" />
            </div>
            <div>
              <h2 className="text-xl font-bold tracking-tight text-text">公司概览</h2>
              <p className="text-sm text-text-muted">按个人项目最近更新展示</p>
            </div>
          </div>
          {loading ? (
            <Loader2 className="h-6 w-6 animate-spin text-primary" />
          ) : overviewProjects.length === 0 ? (
            <p className="rounded-2xl border border-border bg-bg/60 px-5 py-8 text-center text-sm text-text-muted">
              暂无个人研究对象
            </p>
          ) : (
            <div className="space-y-3">
              {overviewProjects.map((project) => {
                const counts = projectArtifactStats(project, personalArtifacts)
                return (
                  <Link
                    key={project.id}
                    to={projectRoute(project, '/analysis')}
                    className="premium-row block rounded-2xl px-5 py-4"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="truncate text-sm font-bold text-text">{projectName(project)}</span>
                      <span className="shrink-0 font-mono text-sm text-text-muted">{projectCode(project)}</span>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2 text-xs font-bold">
                      <span className="rounded-full bg-primary/10 px-2.5 py-1 text-primary">分析 {counts.analysis}</span>
                      <span className="rounded-full bg-amber-50 px-2.5 py-1 text-amber-600">核查 {counts.factcheck}</span>
                      <span className="rounded-full bg-rose-50 px-2.5 py-1 text-rose-700">跟踪 {counts.tracking}</span>
                      <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600">法务 {counts.legal}</span>
                    </div>
                    <div className="mt-3 text-xs font-semibold text-text-muted">
                      最近更新 {formatTime(project.updated_at || project.created_at) || '暂无'}
                    </div>
                  </Link>
                )
              })}
            </div>
          )}
        </aside>
      </section>
    </div>
  )
}
