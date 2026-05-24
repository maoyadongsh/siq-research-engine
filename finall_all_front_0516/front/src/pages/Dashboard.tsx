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

interface WikiCompany {
  code: string
  name: string
  dir: string
  hasReport: boolean
  reportCount: number
  hasFactcheck: boolean
  factcheckCount: number
  hasTracking: boolean
  trackingCount: number
  hasLegal?: boolean
  legalCount?: number
  sourceReportCount?: number
  latestResultAt?: string | null
  latestWikiAt?: string | null
}

interface RecentResult {
  id: string
  type: string
  typeLabel: string
  code: string
  name: string
  filename: string
  pageUrl: string
  mtime: string
}

const steps = [
  { to: '/search', icon: Search, label: '搜索下载', desc: '查询并下载目标公司财报' },
  { to: '/parse', icon: FileText, label: '财报解析', desc: '智能提取结构化财务数据' },
  { to: '/analysis', icon: BarChart3, label: '智能分析', desc: 'AI 生成深度研究报告' },
  { to: '/verify', icon: ShieldCheck, label: '事实核查', desc: '交叉验证数据与证据链' },
  { to: '/tracking', icon: TrendingUp, label: '持续跟踪', desc: '订阅跟踪事项与预警' },
  { to: '/legal', icon: Scale, label: '法务合规', desc: '展示智能体出具的法律意见书' },
]

function formatTime(value: string) {
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

function latestCompanyTime(company: WikiCompany) {
  return company.latestResultAt || company.latestWikiAt || ''
}

function compareCompaniesByReality(a: WikiCompany, b: WikiCompany) {
  const resultDiff = timeScore(b.latestResultAt) - timeScore(a.latestResultAt)
  if (resultDiff !== 0) return resultDiff
  return timeScore(b.latestWikiAt) - timeScore(a.latestWikiAt)
}

function nextAction(company?: WikiCompany) {
  if (!company) return { to: '/search', label: '开始搜索报告', hint: '先选择一家公司，建立研究对象。' }
  if (!company.hasReport) return { to: '/search', label: '补齐财报数据', hint: '暂无智能分析，建议先下载并解析财报。' }
  if (!company.hasFactcheck) return { to: `/verify?company=${encodeURIComponent(company.dir)}`, label: '进行事实核查', hint: '已有智能分析，下一步检查数据、公式和证据链。' }
  if (!company.hasTracking) return { to: `/tracking?company=${encodeURIComponent(company.dir)}`, label: '开启持续跟踪', hint: '结论已核查，可继续生成跟踪事项和预警。' }
  return { to: `/analysis?company=${encodeURIComponent(company.dir)}`, label: '查看研究结论', hint: '分析、核查和跟踪材料均已存在。' }
}

export default function Dashboard() {
  const [companies, setCompanies] = useState<WikiCompany[]>([])
  const [recent, setRecent] = useState<RecentResult[]>([])
  const [loading, setLoading] = useState(true)
  const [recentLoading, setRecentLoading] = useState(true)

  useEffect(() => {
    fetch('/api/wiki/companies/list')
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status))
        return r.json()
      })
      .then((data) => setCompanies(data.companies || []))
      .catch(() => {})
      .finally(() => setLoading(false))

    const limit = localStorage.getItem('recent_task_limit') || '8'
    fetch(`/api/wiki/companies/recent-results?limit=${encodeURIComponent(limit)}`)
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status))
        return r.json()
      })
      .then((data) => setRecent(data.results || []))
      .catch(() => setRecent([]))
      .finally(() => setRecentLoading(false))
  }, [])

  const stats = useMemo(() => {
    const reports = companies.reduce((sum, c) => sum + (c.reportCount || 0), 0)
    const factchecks = companies.reduce((sum, c) => sum + (c.factcheckCount || 0), 0)
    const trackings = companies.reduce((sum, c) => sum + (c.trackingCount || 0), 0)
    const legals = companies.reduce((sum, c) => sum + (c.legalCount || 0), 0)
    return [
      { label: 'Wiki 公司', value: companies.length || 0, unit: '家' },
      { label: '智能分析', value: reports || 0, unit: '篇' },
      { label: '事实核查', value: factchecks || 0, unit: '份' },
      { label: '持续跟踪', value: trackings || 0, unit: '份' },
      { label: '法务合规', value: legals || 0, unit: '份' },
    ]
  }, [companies])

  const featuredCompanies = useMemo(
    () => [...companies].sort(compareCompaniesByReality).slice(0, 6),
    [companies]
  )
  const overviewCompanies = useMemo(
    () => [...companies].sort((a, b) => {
      const resultDiff = timeScore(b.latestResultAt) - timeScore(a.latestResultAt)
      if (resultDiff !== 0) return resultDiff
      return timeScore(b.latestWikiAt) - timeScore(a.latestWikiAt)
    }).slice(0, 8),
    [companies]
  )

  const activeCompany = featuredCompanies[0]
  const action = nextAction(activeCompany)
  const readiness = useMemo(() => {
    if (!activeCompany) return []
    return [
      { label: '智能分析', ready: activeCompany.hasReport, count: activeCompany.reportCount, to: `/analysis?company=${encodeURIComponent(activeCompany.dir)}` },
      { label: '事实核查', ready: Boolean(activeCompany.hasFactcheck), count: activeCompany.factcheckCount, to: `/verify?company=${encodeURIComponent(activeCompany.dir)}` },
      { label: '持续跟踪', ready: Boolean(activeCompany.hasTracking), count: activeCompany.trackingCount, to: `/tracking?company=${encodeURIComponent(activeCompany.dir)}` },
      { label: '法务合规', ready: Boolean(activeCompany.hasLegal), count: activeCompany.legalCount || 0, to: `/legal?company=${encodeURIComponent(activeCompany.dir)}` },
    ]
  }, [activeCompany])

  return (
    <div className="space-y-8">
      {/* ── Hero + Stats + Active Company ── */}
      <section className="premium-shell hero-band overflow-hidden rounded-[28px]">
        <div className="border-b border-border/80 bg-white/48 px-5 py-4 backdrop-blur">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="secondary-kicker">
              <LayoutDashboard className="h-3.5 w-3.5" />
              Workspace
            </div>
            <div className="secondary-step-row">
              <span className="secondary-step-chip">下载</span>
              <span className="secondary-step-chip">解析</span>
              <span className="secondary-step-chip">分析</span>
              <span className="secondary-step-chip">核查</span>
              <span className="secondary-step-chip">跟踪</span>
              <span className="secondary-step-chip">法务</span>
            </div>
          </div>
        </div>
        <div className="grid gap-6 px-5 py-5 sm:px-6 sm:py-6 xl:grid-cols-[1fr_390px]">
          {/* Left: Title + Stats */}
          <div className="flex flex-col justify-between gap-7">
            <div>
              <div className="page-title-tag"><h1 className="text-[1.75rem] font-bold leading-tight tracking-tight text-text md:text-[2.35rem]">
                公司研究工作台
              </h1></div>
              <p className="mt-4 max-w-3xl text-base leading-7 text-text-muted md:text-lg">
                围绕一家公司推进财报下载、解析、分析、核查和持续跟踪，优先处理还缺材料的研究对象。
              </p>
            </div>

            <div className="hero-illustration-wrap">
              <img
                src="/illustrations/finsight-system-map-hero.svg"
                alt="金融科技公司研究工作台插画"
                className="h-full w-full object-cover"
              />
            </div>

            <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
              {stats.map((stat) => (
                <div
                  key={stat.label}
                  className="metric-tile rounded-[20px] p-5"
                >
                  <p className="text-sm font-semibold text-text-muted">{stat.label}</p>
                  <p className="mt-2 font-mono text-[2rem] font-bold tabular-nums tracking-tight text-text md:text-[2.25rem]">
                    {stat.value}
                    <span className="ml-1.5 text-base font-normal text-text-muted">{stat.unit}</span>
                  </p>
                </div>
              ))}
            </div>
          </div>

          {/* Right: Active Company */}
          <aside className="premium-card flex flex-col rounded-[24px] p-5">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm font-medium text-text-muted">当前优先研究对象</p>
                <h2 className="mt-2 truncate text-[1.75rem] font-bold tracking-tight text-text">
                  {activeCompany ? activeCompany.name : '暂无公司数据'}
                </h2>
                <p className="mt-1 font-mono text-sm text-text-muted">
                  {activeCompany?.code || '等待 Wiki 数据同步'}
                </p>
              </div>
              <span
                className={`premium-icon h-12 w-12 shrink-0 rounded-2xl ${
                  activeCompany ? 'text-primary' : 'text-amber-600'
                }`}
              >
                {activeCompany ? <Building2 className="h-6 w-6" /> : <AlertTriangle className="h-6 w-6" />}
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
                  暂无可展示的公司研究状态
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
      <section className="grid grid-cols-2 gap-4 lg:grid-cols-3 2xl:grid-cols-6">
        {steps.map((step, index) => (
          <Link
            key={step.to}
            to={step.to}
            className="premium-card group relative flex min-h-[172px] flex-col rounded-[20px] p-5 text-center transition-all duration-300 hover:-translate-y-0.5 hover:border-primary/25"
          >
            <div className="absolute -right-2 -top-2 flex h-7 w-7 items-center justify-center rounded-full bg-white font-mono text-xs font-bold text-text-muted shadow-md ring-2 ring-card">
              {index + 1}
            </div>
            <div className="premium-icon mx-auto h-12 w-12 rounded-2xl transition-colors group-hover:text-primary-dark">
              <step.icon className="h-6 w-6" />
            </div>
            <p className="mt-4 text-base font-bold text-text">{step.label}</p>
            <p className="mt-1.5 text-sm leading-relaxed text-text-muted">{step.desc}</p>
            <ArrowRight className="mx-auto mt-auto pt-4 h-5 w-5 text-text-muted opacity-0 transition-all group-hover:translate-x-1 group-hover:text-primary group-hover:opacity-100" />
          </Link>
        ))}
      </section>

      {/* ── Featured Companies ── */}
      {featuredCompanies.length > 0 && (
        <section className="premium-shell rounded-[28px] p-6">
          <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 className="text-2xl font-bold tracking-tight text-text">研究对象状态</h2>
              <p className="mt-1.5 text-base text-text-muted">按分析、核查和跟踪报告的最近生成时间排序，优先展示最新产出的研究对象。</p>
            </div>
            <Link
              to="/search"
              className="inline-flex h-11 items-center justify-center rounded-xl border border-border bg-card px-5 text-sm font-semibold text-text shadow-sm transition-colors hover:bg-bg"
            >
              新增研究对象
            </Link>
          </div>
          <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
            {featuredCompanies.map((company) => {
              const companyAction = nextAction(company)
              const items = [
                { label: '分析', ready: company.hasReport, value: company.reportCount },
                { label: '核查', ready: company.hasFactcheck, value: company.factcheckCount },
                { label: '跟踪', ready: company.hasTracking, value: company.trackingCount },
              ]
              return (
                <div
                  key={company.dir}
                  className="premium-card rounded-[20px] p-5"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className="truncate text-lg font-bold text-text">{company.name}</h3>
                      <p className="mt-1 font-mono text-sm text-text-muted">{company.code}</p>
                    </div>
                    <Link
                      to={companyAction.to}
                      className="shrink-0 rounded-xl bg-primary/10 px-3 py-1.5 text-sm font-semibold text-primary transition-colors hover:bg-primary/15"
                    >
                      {companyAction.label}
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

      {/* ── Recent Tasks + Company Overview ── */}
      <section className="grid gap-6 xl:grid-cols-[1fr_380px]">
        <div className="premium-shell rounded-[28px] p-6">
          <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 className="text-2xl font-bold tracking-tight text-text">近期任务列表</h2>
              <p className="mt-1.5 text-base text-text-muted">Agent 最近完成的任务，不包含普通对话。</p>
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
              暂无已生成任务结果
            </p>
          ) : (
            <div className="overflow-hidden rounded-[20px] border border-border bg-white/70">
              {recent.map((item) => (
                <Link
                  key={item.id}
                  to={item.pageUrl}
                  className="group flex items-center gap-4 border-b border-border/70 px-5 py-4 transition-colors last:border-b-0 hover:bg-primary/[0.035]"
                >
                  <span className="premium-icon h-11 w-11 shrink-0 rounded-xl">
                    <FileText className="h-5 w-5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-base font-bold text-text">
                      {item.name} <span className="font-mono text-sm font-normal text-text-muted">{item.code}</span>
                    </span>
                    <span className="mt-0.5 block truncate text-sm text-text-muted">
                      {item.typeLabel} · {item.filename}
                    </span>
                  </span>
                  <span className="hidden shrink-0 rounded-full bg-bg px-3 py-1 text-sm font-semibold text-text-muted md:inline-flex">
                    {formatTime(item.mtime)}
                  </span>
                  <ArrowRight className="h-5 w-5 shrink-0 text-text-muted transition-all group-hover:translate-x-1 group-hover:text-primary" />
                </Link>
              ))}
            </div>
          )}
        </div>

        <aside className="premium-shell rounded-[28px] p-6">
          <div className="mb-6 flex items-center gap-4">
            <div className="premium-icon h-12 w-12 rounded-2xl">
              <Building2 className="h-6 w-6" />
            </div>
            <div>
              <h2 className="text-xl font-bold tracking-tight text-text">公司概览</h2>
              <p className="text-sm text-text-muted">按最近更新展示</p>
            </div>
          </div>
          {loading ? (
            <Loader2 className="h-6 w-6 animate-spin text-primary" />
          ) : (
            <div className="space-y-3">
              {overviewCompanies.map((company) => (
                <Link
                  key={company.dir}
                  to={`/analysis?company=${encodeURIComponent(company.dir)}`}
                  className="premium-row block rounded-2xl px-5 py-4"
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="truncate text-sm font-bold text-text">{company.name}</span>
                    <span className="shrink-0 font-mono text-sm text-text-muted">{company.code}</span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs font-bold">
                    <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600">源报 {company.sourceReportCount || 0}</span>
                    <span className="rounded-full bg-primary/10 px-2.5 py-1 text-primary">报告 {company.reportCount}</span>
                    <span className="rounded-full bg-amber-50 px-2.5 py-1 text-amber-600">核查 {company.factcheckCount}</span>
                    <span className="rounded-full bg-rose-50 px-2.5 py-1 text-rose-700">跟踪 {company.trackingCount}</span>
                  </div>
                  <div className="mt-3 text-xs font-semibold text-text-muted">
                    最近更新 {formatTime(latestCompanyTime(company)) || '暂无'}
                  </div>
                </Link>
              ))}
            </div>
          )}
        </aside>
      </section>
    </div>
  )
}
