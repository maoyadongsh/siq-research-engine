import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertCircle, BarChart3, Building2, ChartCandlestick, Download, FileText, Loader2, Scale, ShieldCheck, Share2, Trash2, TrendingUp, X } from 'lucide-react'
import PageWithAgentChat from '../agent/PageWithAgentChat'
import { Button, EmptyState, useToast } from '../ui'
import { copyText } from '../../lib/clipboard'

interface Company { code: string; name: string; dir: string; hasReport: boolean; reportCount: number; hasFactcheck?: boolean; factcheckCount?: number; hasTracking?: boolean; trackingCount?: number; hasLegal?: boolean; legalCount?: number }
interface ReportItem { filename: string; url: string; size: number; mtime: string }
interface Props {
  agentConfig: { apiPrefix: string; title: string; description: string; quickQuestions: string[] }
  pageTitle: string
  reportType: 'analysis' | 'factcheck' | 'tracking' | 'legal'
  reportApiSuffix: string
  iframeTitle: string
  emptyTitle: (companyName: string) => string
  emptyDescription: string
  infoFields: (company: Company) => { label: string; value: string }[]
}

const REPORT_TYPE_META = {
  analysis: {
    label: '智能分析',
    english: 'Smart Analysis',
    Icon: BarChart3,
    accent: 'from-primary to-primary-light',
    steps: ['分析', '洞察', '结论'],
  },
  factcheck: {
    label: '事实核查',
    english: 'Fact Check',
    Icon: ShieldCheck,
    accent: 'from-primary to-primary-light',
    steps: ['核查', '证据', '溯源'],
  },
  tracking: {
    label: '持续跟踪',
    english: 'Continuous Tracking',
    Icon: TrendingUp,
    accent: 'from-primary to-primary-light',
    steps: ['跟踪', '预警', '复盘'],
  },
  legal: {
    label: '法务合规',
    english: 'Legal Compliance',
    Icon: Scale,
    accent: 'from-primary to-primary-light',
    steps: ['检索', '审查', '意见'],
  },
} as const

const REPORT_VIEWER_THEME = `
  :root{color-scheme:light!important}
  html,body{
    margin:0!important;
    min-width:0!important;
    background:#f5f7fb!important;
    color:#0f172a!important;
    font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif!important;
    line-height:1.65!important;
    -webkit-font-smoothing:antialiased!important;
    text-rendering:optimizeLegibility!important;
  }
  body{
    box-sizing:border-box!important;
    padding:26px!important;
  }
  body::before,body::after{display:none!important}
  *{box-sizing:border-box!important}
  main,article,section,.report,.container,.content,.markdown-body,.page,.wrapper,.card,.panel,[class*="card"],[class*="panel"],[class*="section"]{
    background:#ffffff!important;
    color:#0f172a!important;
    border-color:#e2e8f0!important;
    box-shadow:none!important;
  }
  main,article,.report,.container,.content,.markdown-body,.page,.wrapper{
    max-width:1080px!important;
    margin-left:auto!important;
    margin-right:auto!important;
    border-radius:24px!important;
    padding:30px!important;
    border:1px solid rgba(226,232,240,.95)!important;
    box-shadow:0 24px 70px rgba(15,23,42,.08),0 1px 0 rgba(255,255,255,.9) inset!important;
  }
  body>div:first-child:not(.report):not(.container):not(.content):not(.markdown-body):not(.page):not(.wrapper),
  body>section:first-child,
  body>header:first-child{
    max-width:1080px!important;
    margin:0 auto 18px!important;
    border-radius:24px!important;
    border:1px solid rgba(219,234,254,.95)!important;
    background:linear-gradient(135deg,#ffffff 0%,#f6faff 46%,#eef6ff 100%)!important;
    color:#0f172a!important;
    box-shadow:0 20px 60px rgba(15,23,42,.08),0 1px 0 rgba(255,255,255,.92) inset!important;
  }
  body>h1:first-child,
  main>h1:first-child,
  article>h1:first-child,
  .report>h1:first-child,
  .container>h1:first-child,
  .content>h1:first-child,
  .markdown-body>h1:first-child{
    margin:-30px -30px 26px!important;
    padding:34px 34px 30px!important;
    border-radius:24px 24px 0 0!important;
    border-bottom:1px solid rgba(191,219,254,.9)!important;
    background:linear-gradient(135deg,#ffffff 0%,#f5f9ff 45%,#eaf4ff 100%)!important;
    color:#0f172a!important;
    font-size:34px!important;
    font-weight:780!important;
    letter-spacing:0!important;
    box-shadow:0 1px 0 rgba(255,255,255,.92) inset!important;
  }
  body>h1:first-child+p,
  main>h1:first-child+p,
  article>h1:first-child+p,
  .report>h1:first-child+p,
  .container>h1:first-child+p,
  .content>h1:first-child+p,
  .markdown-body>h1:first-child+p{
    margin-top:-10px!important;
    padding:0 0 18px!important;
    color:#334155!important;
    border-bottom:1px solid rgba(226,232,240,.95)!important;
    font-size:16px!important;
    line-height:1.75!important;
  }
  h1,h2,h3,h4,h5,h6{
    color:#0f172a!important;
    letter-spacing:0!important;
    line-height:1.25!important;
  }
  h1{font-size:32px!important;margin:0 0 22px!important;font-weight:780!important}
  h2{font-size:23px!important;margin:32px 0 14px!important;font-weight:720!important}
  h2::before{
    content:""!important;
    display:inline-block!important;
    width:4px!important;
    height:1em!important;
    margin-right:10px!important;
    border-radius:999px!important;
    background:#0052ff!important;
    vertical-align:-.14em!important;
  }
  h3{font-size:18px!important;margin:24px 0 12px!important;font-weight:700!important}
  p,li,td,th,div,span,strong,em,label{
    border-color:#e2e8f0!important;
  }
  p,li,td,div,span,label{color:#1f2937!important}
  p{margin:10px 0!important;line-height:1.78!important}
  strong,b,th{color:#0f172a!important}
  a{color:#0052ff!important;text-decoration:none!important}
  a:hover{text-decoration:underline!important}
  .summary,.overview,.executive-summary,.abstract,.key-points,.metrics,.kpi,.risk-summary,
  [class*="summary"],[class*="overview"],[class*="metric"],[class*="kpi"],[class*="highlight"],[class*="insight"]{
    border:1px solid rgba(226,232,240,.95)!important;
    border-radius:18px!important;
    background:linear-gradient(180deg,#ffffff 0%,#f8fbff 100%)!important;
    color:#0f172a!important;
    box-shadow:0 12px 34px rgba(15,23,42,.06)!important;
  }
  .summary,.overview,.executive-summary,.abstract,.key-points,.risk-summary,
  [class*="summary"],[class*="overview"],[class*="highlight"],[class*="insight"]{
    padding:18px 20px!important;
    margin:16px 0 22px!important;
  }
  .muted,.secondary,.subtle,.desc,.description,.caption,.note,small,
  [class*="muted"],[class*="secondary"],[class*="desc"],[class*="caption"],[class*="note"]{
    color:#475569!important;
  }
  table{
    width:100%!important;
    border-collapse:separate!important;
    border-spacing:0!important;
    overflow:hidden!important;
    border:1px solid rgba(226,232,240,.98)!important;
    border-radius:14px!important;
    background:#ffffff!important;
    margin:16px 0 24px!important;
    box-shadow:0 10px 30px rgba(15,23,42,.05)!important;
  }
  th,td{
    border-color:#e2e8f0!important;
    border-width:0 0 1px!important;
    padding:11px 13px!important;
    background:#ffffff!important;
  }
  th{
    background:#f1f5f9!important;
    color:#1e293b!important;
    font-weight:700!important;
  }
  tr:nth-child(even) td{background:#fbfdff!important}
  pre,code,kbd,samp{
    background:#f8fafc!important;
    color:#0f172a!important;
    border-color:#e2e8f0!important;
  }
  pre{
    border:1px solid #e2e8f0!important;
    border-radius:14px!important;
    padding:16px!important;
    overflow:auto!important;
    box-shadow:0 10px 30px rgba(15,23,42,.04)!important;
  }
  blockquote{
    margin:18px 0!important;
    border-left:4px solid #0052ff!important;
    background:#f8fbff!important;
    color:#1f2937!important;
    border-radius:0 16px 16px 0!important;
    padding:14px 18px!important;
  }
  ul,ol{padding-left:1.35rem!important}
  li{margin:.35rem 0!important}
  hr{border:0!important;border-top:1px solid #e2e8f0!important}
  img,svg,canvas{max-width:100%!important}
  [class*="hero"],[class*="cover"],[class*="header"],[class*="banner"],[class*="title"],
  [style*="#0f172a"],[style*="#111827"],[style*="#020617"],[style*="#12312b"],[style*="#315c4f"],
  [style*="rgb(15, 23, 42)"],[style*="rgb(17, 24, 39)"],[style*="linear-gradient"]{
    background:linear-gradient(135deg,#ffffff 0%,#f6faff 52%,#eef6ff 100%)!important;
    background-color:#ffffff!important;
    color:#0f172a!important;
    border-color:#dbeafe!important;
    box-shadow:0 18px 46px rgba(15,23,42,.07)!important;
  }
  button,.btn,[role="button"]{
    border-radius:10px!important;
    border:1px solid #dbe4ef!important;
    background:#ffffff!important;
    color:#0f172a!important;
  }
  .dark,[data-theme="dark"],[class*="dark"],[style*="background:#000"],[style*="background: #000"],[style*="background-color:#000"],[style*="background-color: #000"]{
    background:#ffffff!important;
    color:#0f172a!important;
  }
`

function colorLuminance(r: number, g: number, b: number) {
  return r * 0.299 + g * 0.587 + b * 0.114
}

function hexToRgb(value: string) {
  const raw = value.trim().toLowerCase()
  if (!raw.startsWith('#')) return null
  const hex = raw.length === 4
    ? raw.slice(1).split('').map((ch) => ch + ch).join('')
    : raw.slice(1, 7)
  const valueNum = Number.parseInt(hex, 16)
  if (Number.isNaN(valueNum)) return null
  return {
    r: (valueNum >> 16) & 255,
    g: (valueNum >> 8) & 255,
    b: valueNum & 255,
  }
}

function rgbStringToRgb(value: string) {
  const match = value.trim().toLowerCase().match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?/)
  if (!match) return null
  if (match[4] !== undefined && Number(match[4]) <= 0.05) return null
  return { r: Number(match[1]), g: Number(match[2]), b: Number(match[3]) }
}

function extractCssColors(value: string) {
  const color = value.trim().toLowerCase()
  const tokens = [
    ...color.matchAll(/#[0-9a-f]{3,8}\b/g),
    ...color.matchAll(/rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+(?:\s*,\s*[\d.]+)?\s*\)/g),
  ].map((match) => match[0])

  if (color.includes('black')) tokens.push('rgb(0,0,0)')
  return tokens
    .map((token) => hexToRgb(token) || rgbStringToRgb(token))
    .filter((rgb): rgb is { r: number; g: number; b: number } => Boolean(rgb))
}

function isDarkColor(value: string) {
  const color = value.trim().toLowerCase()
  if (!color || color === 'transparent' || color === 'rgba(0, 0, 0, 0)') return false
  const colors = extractCssColors(color)
  if (!colors.length) {
    const transparentBlack = color.replace(/\s/g, '').includes('rgba(0,0,0,0)')
    return color.includes('black') || (color.includes('rgb(0') && !transparentBlack)
  }
  return colors.some(({ r, g, b }) => colorLuminance(r, g, b) < 112)
}

function isLowContrastText(value: string) {
  const color = value.trim().toLowerCase()
  if (!color || color === 'transparent' || color === 'rgba(0, 0, 0, 0)') return false
  const colors = extractCssColors(color)
  if (!colors.length) return color.includes('white')
  return colors.some(({ r, g, b }) => colorLuminance(r, g, b) > 150)
}

function looksLikeTitleBlock(el: HTMLElement) {
  const classAndId = `${el.className || ''} ${el.id || ''}`.toLowerCase()
  if (/(hero|cover|header|banner|title|masthead|report-header|header-card)/.test(classAndId)) return true
  if (el.parentElement === el.ownerDocument.body && el.getBoundingClientRect().top < 260) return true
  return false
}

function applyReportViewerTheme(doc: Document) {
  doc.documentElement.classList.remove('dark')
  doc.documentElement.setAttribute('data-theme', 'light')
  doc.documentElement.style.setProperty('color-scheme', 'light', 'important')
  doc.body?.classList.remove('dark')
  doc.body?.setAttribute('data-theme', 'light')

  if (doc.head && !doc.getElementById('finsight-report-light-theme')) {
    const style = doc.createElement('style')
    style.id = 'finsight-report-light-theme'
    style.textContent = REPORT_VIEWER_THEME
    doc.head.appendChild(style)
  }

  doc.querySelectorAll<HTMLElement>('*').forEach((el) => {
    const inlineBg = el.style.background || el.style.backgroundColor
    const computedStyle = doc.defaultView?.getComputedStyle(el)
    const computedBg = `${computedStyle?.backgroundImage || ''} ${computedStyle?.backgroundColor || ''}`
    const shouldLightenBackground = (inlineBg && isDarkColor(inlineBg)) || isDarkColor(computedBg) || looksLikeTitleBlock(el)
    if (shouldLightenBackground) {
      const bg = el === doc.body ? '#f6f8fb' : looksLikeTitleBlock(el) ? 'linear-gradient(135deg,#ffffff 0%,#f6faff 52%,#eef6ff 100%)' : '#ffffff'
      el.style.setProperty('background', bg, 'important')
      el.style.setProperty('background-color', el === doc.body ? '#f6f8fb' : '#ffffff', 'important')
    }

    const inlineColor = el.style.color
    const computedColor = computedStyle?.color || ''
    if ((inlineColor && (isDarkColor(inlineColor) || isLowContrastText(inlineColor))) || isLowContrastText(computedColor)) {
      el.style.setProperty('color', el.matches('h1,h2,h3,h4,h5,h6,strong,b,th') ? '#0f172a' : '#1f2937', 'important')
    }

    if (el.style.boxShadow) {
      el.style.setProperty('box-shadow', 'none', 'important')
    }
  })
}

export default function ReportViewer({ agentConfig, pageTitle, reportType, reportApiSuffix, iframeTitle, emptyTitle, emptyDescription, infoFields }: Props) {
  const { toast } = useToast()
  const [searchParams] = useSearchParams()
  const requestedCompany = searchParams.get('company') || ''
  const requestedResult = searchParams.get('result') || ''
  const [companies, setCompanies] = useState<Company[]>([])
  const [selectedDir, setSelectedDir] = useState('')
  const [reports, setReports] = useState<ReportItem[]>([])
  const [selectedReportUrl, setSelectedReportUrl] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [iframeHeight, setIframeHeight] = useState('70vh')
  const iframeRef = useRef<HTMLIFrameElement>(null)

  useEffect(() => {
    fetch('/api/wiki/companies/list').then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() }).then((data) => {
      const list: Company[] = data.companies || []
      setCompanies(list)
      const requested = list.find((c) => c.dir === requestedCompany)
      const first = list.find((c) => c.hasReport || c.hasFactcheck || c.hasTracking || c.hasLegal)
      if (requested) setSelectedDir(requested.dir)
      else if (first) setSelectedDir(first.dir)
      else if (list[0]) setSelectedDir(list[0].dir)
      setLoading(false)
    }).catch(() => { setError('无法加载公司列表，请确认后端服务正常运行。'); setLoading(false) })
  }, [requestedCompany])

  useEffect(() => {
    if (!selectedDir) return
    setConfirmDelete(false)
    setReports([]); setSelectedReportUrl('')
    fetch(`/api/wiki/companies/${encodeURIComponent(selectedDir)}/${reportApiSuffix}`).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() }).then((data) => {
      const list: ReportItem[] = data[reportApiSuffix] || data.reports || []
      setReports(list)
      if (list.length > 0) {
        const selected = list.find((r) => r.filename === requestedResult) || list[0]
        setSelectedReportUrl(`/api/wiki/companies/${encodeURIComponent(selectedDir)}/${reportType}/${encodeURIComponent(selected.filename)}`)
      }
    }).catch(() => setReports([]))
  }, [selectedDir, requestedResult, reportApiSuffix, reportType])

  const handleIframeLoad = () => {
    try {
      const doc = iframeRef.current?.contentDocument
      if (doc) applyReportViewerTheme(doc)
      if (doc?.body) setIframeHeight(Math.max(doc.body.scrollHeight + 40, 600) + 'px')
    } catch { setIframeHeight('70vh') }
  }

  const selectedCompany = companies.find((c) => c.dir === selectedDir)
  const selectedReport = reports.find((report) => `/api/wiki/companies/${encodeURIComponent(selectedDir)}/${reportType}/${encodeURIComponent(report.filename)}` === selectedReportUrl)
  const hasReports = reports.length > 0
  const meta = REPORT_TYPE_META[reportType]
  const KickerIcon = meta.Icon
  const cleanCompanyName = selectedCompany?.name.split('_')[0] || '请选择公司'
  const agentContext = useMemo(() => ({
    company: selectedCompany
      ? {
          code: selectedCompany.code,
          name: cleanCompanyName,
          dir: selectedCompany.dir,
        }
      : undefined,
    report: selectedReport
      ? {
          type: reportType,
          title: meta.label,
          filename: selectedReport.filename,
          url: selectedReportUrl,
          mtime: selectedReport.mtime,
        }
      : {
          type: reportType,
          title: meta.label,
        },
    page: {
      title: pageTitle,
    },
  }), [cleanCompanyName, meta.label, pageTitle, reportType, selectedCompany, selectedReport, selectedReportUrl])
  const updatedAt = selectedReport?.mtime
    ? new Date(selectedReport.mtime).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    : '--'
  const share = async () => {
    if (await copyText(window.location.origin + selectedReportUrl)) {
      toast({ type: 'success', title: '链接已复制', description: '可以直接粘贴给协作者查看这份报告。' })
    } else {
      toast({ type: 'error', title: '复制失败', description: '浏览器未授权剪贴板访问，请手动复制地址栏链接。' })
    }
  }

  const deleteSelectedReport = async () => {
    if (!selectedReport) return
    setDeleting(true)
    try {
      const res = await fetch(`/api/wiki/companies/${encodeURIComponent(selectedDir)}/${reportType}/${encodeURIComponent(selectedReport.filename)}`, { method: 'DELETE' })
      if (!res.ok) {
        const detail = await res.json().catch(() => null)
        throw new Error(detail?.detail || `HTTP ${res.status}`)
      }
      const nextReports = reports.filter((report) => report.filename !== selectedReport.filename)
      setReports(nextReports)
      setSelectedReportUrl(nextReports[0] ? `/api/wiki/companies/${encodeURIComponent(selectedDir)}/${reportType}/${encodeURIComponent(nextReports[0].filename)}` : '')
      setConfirmDelete(false)
      toast({ type: 'success', title: '报告已删除', description: selectedReport.filename })
      fetch('/api/wiki/companies/list').then((r) => r.ok ? r.json() : null).then((data) => {
        if (data?.companies) setCompanies(data.companies)
      }).catch(() => {})
    } catch (err) {
      toast({ type: 'error', title: '删除失败', description: (err as Error).message })
    } finally {
      setDeleting(false)
    }
  }

  if (loading) return <PageWithAgentChat {...agentConfig}><div className="flex items-center justify-center py-32"><Loader2 className="h-8 w-8 animate-spin text-primary" /><span className="ml-3 text-text-muted">加载公司列表...</span></div></PageWithAgentChat>
  if (error) return <PageWithAgentChat {...agentConfig}><div className="rounded-2xl border border-error/20 bg-error/5 p-6 text-center"><AlertCircle className="mx-auto mb-3 h-8 w-8 text-error" /><p className="text-base text-error">{error}</p></div></PageWithAgentChat>

  return (
    <PageWithAgentChat {...agentConfig} context={agentContext}>
      <div className="secondary-page">
        <section className="secondary-hero">
          <div className="secondary-hero-inner">
            <div className="min-w-0">
              <div className="secondary-kicker">
                <KickerIcon className="h-3.5 w-3.5" />
                {meta.english}
              </div>
              <h1 className="secondary-title">{pageTitle}</h1>
              <p className="secondary-description">选择公司和报告版本，系统会以统一阅读样式展示 HTML 结果。</p>
            </div>
            <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center lg:flex-col lg:items-end">
              <div className="secondary-step-row">
                {meta.steps.map((step, index) => (
                  <span key={step} className={`secondary-step-chip ${index === 0 ? 'is-active' : ''}`}>{step}</span>
                ))}
              </div>
              {selectedCompany && (
                <div className="secondary-company-card" title={`${cleanCompanyName} ${selectedCompany.code}`}>
                  <div className="secondary-company-icon">
                    <ChartCandlestick className="h-4 w-4" />
                  </div>
                  <div className="secondary-company-text">
                    <span className="secondary-company-name">{cleanCompanyName}</span>
                    <span className="secondary-company-code">{selectedCompany.code}</span>
                  </div>
                </div>
              )}
            </div>
          </div>
          <div className="flex flex-col gap-4 border-t border-border/70 px-5 py-4 lg:flex-row lg:flex-wrap lg:items-center lg:justify-between">
            <div className="flex flex-wrap items-center gap-3">
              <label className="space-y-1">
                <span className="secondary-label">公司</span>
                <span className="relative block">
                  <Building2 className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
                  <select value={selectedDir} onChange={(e) => setSelectedDir(e.target.value)} className="form-control min-w-[240px] appearance-none py-0 pl-10 pr-9 text-sm font-medium">{companies.map((c) => <option key={c.dir} value={c.dir}>{c.code} {c.name}</option>)}</select>
                </span>
              </label>
              {hasReports && (
                <label className="space-y-1">
                  <span className="secondary-label">报告版本</span>
                  <select value={selectedReportUrl} onChange={(e) => { setSelectedReportUrl(e.target.value); setConfirmDelete(false) }} className="form-control min-w-[280px] appearance-none px-3 pr-9 text-sm">{reports.map((r) => <option key={r.filename} value={`/api/wiki/companies/${encodeURIComponent(selectedDir)}/${reportType}/${encodeURIComponent(r.filename)}`}>{r.filename}</option>)}</select>
                </label>
              )}
            </div>
            {selectedReportUrl && <div className="flex flex-wrap gap-2">
              <a href={selectedReportUrl} download className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text shadow-sm hover:bg-bg"><Download className="h-4 w-4" />下载</a>
              <Button variant="secondary" size="sm" leftIcon={<Share2 className="h-4 w-4" />} onClick={share}>分享</Button>
              {confirmDelete ? <>
                <Button variant="danger" size="sm" leftIcon={deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />} onClick={deleteSelectedReport} disabled={deleting}>确认删除</Button>
                <Button variant="secondary" size="sm" leftIcon={<X className="h-4 w-4" />} onClick={() => setConfirmDelete(false)} disabled={deleting}>取消</Button>
              </> : <Button variant="secondary" size="sm" leftIcon={<Trash2 className="h-4 w-4" />} onClick={() => setConfirmDelete(true)}>删除</Button>}
            </div>}
          </div>
        </section>
        {selectedReportUrl ? <div className="secondary-panel overflow-hidden">
          {selectedReport && <div className="border-b border-border bg-card">
            <div className="flex flex-col gap-4 px-5 py-4 md:flex-row md:items-center md:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`h-2.5 w-2.5 rounded-full bg-gradient-to-br ${meta.accent}`} />
                  <p className="truncate text-base font-bold text-text">{selectedReport.filename}</p>
                  <span className="secondary-status">{updatedAt}</span>
                </div>
                <p className="mt-1 text-sm text-text-muted">已套用 FinSight 阅读样式，可在右侧助手中追问结论、数据来源和风险点。</p>
              </div>
              <div className="flex shrink-0 flex-wrap gap-2">
                <span className="secondary-status secondary-status-info">{Math.max(1, Math.round(selectedReport.size / 1024))} KB</span>
                <span className="secondary-status secondary-status-success">已加载</span>
              </div>
            </div>
          </div>}
          <iframe ref={iframeRef} src={selectedReportUrl} onLoad={handleIframeLoad} style={{ width: '100%', height: iframeHeight, border: 'none', display: 'block', background: '#fff' }} title={iframeTitle} />
        </div> : selectedDir && !hasReports ? <EmptyState icon={<FileText className="h-16 w-16" />} title={emptyTitle(selectedCompany?.name || '该公司')} description={emptyDescription} /> : <EmptyState icon={<Building2 className="h-16 w-16" />} title="选择公司查看报告" description="从上方下拉框中选择一家公司，查看其报告内容。" />}
        {selectedCompany && <div className="secondary-panel px-5 py-4"><div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{infoFields(selectedCompany).map((field) => <div key={field.label}><span className="secondary-label">{field.label}</span><p className="mt-1 text-base font-semibold text-text">{field.value}</p></div>)}</div></div>}
      </div>
    </PageWithAgentChat>
  )
}
