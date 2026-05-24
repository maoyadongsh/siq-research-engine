import { useState, useCallback, useEffect } from 'react'
import {
  Search,
  Download,
  FileText,
  CheckCircle2,
  Loader2,
  Filter,
  ExternalLink,
  FolderOpen,
  RefreshCw,
  Trash2,
} from 'lucide-react'
import { useToast } from '../components/ui'

interface ReportItem {
  title: string
  report_type: string
  report_end: string
  published_at: string
  document_url: string
  file_name?: string
  saved_path?: string
  size_bytes?: number
  success?: boolean
}

interface DownloadFileResult {
  document_url?: string
  title: string
  report_type: string
  report_end: string
  file_name: string
  saved_path: string
  size_bytes: number
  success: boolean
  cache_hit: boolean
}

interface DownloadedPdf {
  id: string
  company: string
  category: string
  filename: string
  relativePath: string
  size: number
  mtime: string
  url: string
}

const typeLabels: Record<string, string> = {
  annual: '年报',
  semiannual: '半年报',
  q1: '一季报',
  q3: '三季报',
}

const typeStyles: Record<string, string> = {
  annual: 'secondary-table-chip',
  semiannual: 'secondary-table-chip',
  q1: 'secondary-table-chip',
  q3: 'secondary-table-chip',
}

function uniqueBy<T>(items: T[], getKey: (item: T) => string) {
  const seen = new Set<string>()
  return items.filter((item) => {
    const key = getKey(item)
    if (!key || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export default function SearchDownload() {
  const { toast } = useToast()
  const [query, setQuery] = useState('')
  const [year, setYear] = useState('2025')
  const [exchange, setExchange] = useState('')
  const [loading, setLoading] = useState(false)
  const [annualReports, setAnnualReports] = useState<ReportItem[]>([])
  const [financialReports, setFinancialReports] = useState<ReportItem[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [downloading, setDownloading] = useState(false)
  const [downloadResults, setDownloadResults] = useState<DownloadFileResult[]>([])
  const [logs, setLogs] = useState<{ time: string; msg: string; type: string }[]>([])
  const [companyInfo, setCompanyInfo] = useState<{ name: string; ticker: string } | null>(null)
  const [downloadedReports, setDownloadedReports] = useState<DownloadedPdf[]>([])
  const [downloadedLoading, setDownloadedLoading] = useState(false)
  const [downloadedQuery, setDownloadedQuery] = useState('')
  const [confirmDeletePath, setConfirmDeletePath] = useState('')
  const [deletingPath, setDeletingPath] = useState('')

  const currentYear = new Date().getFullYear()
  const years = Array.from({ length: 10 }, (_, i) => String(currentYear - i))

  const addLog = useCallback((msg: string, type = 'info') => {
    const time = new Date().toLocaleTimeString('zh-CN')
    setLogs((prev) => [...prev, { time, msg, type }])
  }, [])

  const loadDownloadedReports = useCallback(async (text: string) => {
    setDownloadedLoading(true)
    try {
      const res = await fetch(`/api/downloads/reports?q=${encodeURIComponent(text.trim())}&limit=120`)
      if (!res.ok) throw new Error(String(res.status))
      const data = await res.json()
      setDownloadedReports(data.reports || [])
    } catch {
      setDownloadedReports([])
    } finally {
      setDownloadedLoading(false)
    }
  }, [])

  useEffect(() => {
    loadDownloadedReports('')
  }, [loadDownloadedReports])

  const handleSearch = async () => {
    if (!query.trim()) return
    setLoading(true)
    setSelected(new Set())
    setDownloadResults([])
    setAnnualReports([])
    setFinancialReports([])
    setCompanyInfo(null)
    addLog(`正在查询: ${query} (${year})`, 'info')

    try {
      // Step 1: Resolve company
      const resolveRes = await fetch('/api/v1/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          company_name: query,
          exchange_hint: exchange || undefined,
        }),
      })

      if (!resolveRes.ok) throw new Error(`解析公司失败: ${resolveRes.status}`)
      const resolved = await resolveRes.json()
      const resolvedCompany = resolved.resolved || resolved
      const companyName = resolvedCompany.canonical_name || resolvedCompany.display_name || resolved.company_name || resolved.name || query
      const ticker = resolvedCompany.ticker || resolved.ticker || resolved.code || ''
      setCompanyInfo({ name: companyName, ticker })
      addLog(`已解析: ${companyName} (${ticker})`, 'success')

      // Step 2: Query annual reports
      const annualRes = await fetch('/api/v1/reports/recent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          company_name: companyName,
          ticker: ticker || undefined,
          exchange_hint: exchange || undefined,
          target: 'annual_report',
          report_year: parseInt(year),
          limit: 10,
        }),
      })
      const annualData = annualRes.ok ? await annualRes.json() : { reports: [] }
      const annual = Array.isArray(annualData) ? annualData : annualData.reports || annualData.items || []
      setAnnualReports(annual)
      addLog(`找到 ${annual.length} 份年报`, 'success')

      // Step 3: Query financial reports
      const finRes = await fetch('/api/v1/reports/recent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          company_name: companyName,
          ticker: ticker || undefined,
          exchange_hint: exchange || undefined,
          target: 'financial_report',
          report_year: parseInt(year),
          limit: 20,
        }),
      })
      const finData = finRes.ok ? await finRes.json() : { reports: [] }
      const financial = Array.isArray(finData) ? finData : finData.reports || finData.items || []
      setFinancialReports(financial)
      addLog(`找到 ${financial.length} 份财报`, 'success')
    } catch (e) {
      addLog(`查询失败: ${(e as Error).message}`, 'error')
    } finally {
      setLoading(false)
    }
  }

  const toggleSelect = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const toggleAll = (reports: ReportItem[]) => {
    const keys = reports.map((r) => r.document_url)
    setSelected((prev) => {
      const next = new Set(prev)
      const allSelected = keys.every((k) => next.has(k))
      if (allSelected) keys.forEach((k) => next.delete(k))
      else keys.forEach((k) => next.add(k))
      return next
    })
  }

  const handleDownload = async () => {
    if (selected.size === 0 || !companyInfo) return
    setDownloading(true)
    setDownloadResults([])
    addLog(`开始下载 ${selected.size} 份财报...`, 'info')

    const allReports = [...annualReports, ...financialReports]
    const selectedReports = uniqueBy(
      allReports.filter((r) => selected.has(r.document_url)),
      (report) => report.document_url,
    )

    // Group selected by report_type for select-download API
    try {
      // Use batch-download with the selected URLs
      const items = selectedReports.map((r) => ({
        document_url: r.document_url,
        company_name: companyInfo.name,
        title: r.title,
        ticker: companyInfo.ticker,
        report_type: r.report_type,
        report_end: r.report_end,
        published_at: r.published_at,
      }))

      const res = await fetch('/api/v1/reports/batch-download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          default_company_name: companyInfo.name,
          items,
        }),
      })

      if (!res.ok) throw new Error(`下载失败: ${res.status}`)
      const data = await res.json()

      setDownloadResults(uniqueBy(data.results || data.files || [], (item: DownloadFileResult) => item.document_url || item.file_name))
      addLog(`下载完成: 成功 ${data.succeeded || 0}, 失败 ${data.failed || 0}`, 'success')
      loadDownloadedReports(downloadedQuery)
    } catch (e) {
      addLog(`批量下载失败: ${(e as Error).message}, 尝试逐个下载...`, 'warn')

      // Fallback: download one by one
      for (const report of selectedReports) {
        try {
          const res = await fetch('/api/v1/reports/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              company_name: companyInfo.name,
              document_url: report.document_url,
              title: report.title,
            }),
          })
          if (res.ok) {
            addLog(`下载成功: ${report.title}`, 'success')
          } else {
            throw new Error(`${res.status}`)
          }
        } catch {
          addLog(`下载失败: ${report.title}`, 'error')
        }
      }
    } finally {
      setDownloading(false)
      addLog('全部下载任务完成', 'success')
    }
  }

  // Quick download: use select-download API for specific types
  const handleQuickDownload = async (reportTypes: string[]) => {
    if (!companyInfo) return
    setDownloading(true)
    setDownloadResults([])
    addLog(`快速下载 ${reportTypes.map((t) => typeLabels[t]).join('+')}...`, 'info')

    try {
      const res = await fetch('/api/v1/reports/select-download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          company_name: companyInfo.name,
          ticker: companyInfo.ticker || undefined,
          exchange_hint: exchange || undefined,
          report_types: reportTypes,
          report_year: parseInt(year),
        }),
      })

      if (!res.ok) throw new Error(`下载失败: ${res.status}`)
      const data = await res.json()

      setDownloadResults(data.files || [])
      addLog(`下载完成: ${data.company_name} 成功 ${data.succeeded}/${data.total}`, 'success')
      loadDownloadedReports(downloadedQuery)
    } catch (e) {
      addLog(`下载失败: ${(e as Error).message}`, 'error')
    } finally {
      setDownloading(false)
    }
  }

  const renderTable = (
    reports: ReportItem[],
    title: string,
    icon: React.ReactNode,
  ) => {
    if (reports.length === 0) return null
    const allChecked = reports.every((r) => selected.has(r.document_url))

    return (
      <div className="overflow-hidden rounded-[20px] border border-border bg-card shadow-sm">
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <h3 className="flex items-center gap-2 text-base font-semibold text-text">
            {icon}
            {title}
          </h3>
          <label className="flex cursor-pointer items-center gap-2 text-sm text-text-muted">
            全选
            <input
              type="checkbox"
              checked={allChecked}
              onChange={() => toggleAll(reports)}
              className="h-[18px] w-[18px] cursor-pointer rounded accent-primary"
            />
          </label>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-bg/50">
              <th className="w-10 px-4 py-3"></th>
              <th className="px-4 py-3 text-left font-semibold text-text-muted">报告标题</th>
              <th className="w-24 px-4 py-3 text-left font-semibold text-text-muted">类型</th>
              <th className="w-28 px-4 py-3 text-left font-semibold text-text-muted">报告期</th>
              <th className="w-28 px-4 py-3 text-left font-semibold text-text-muted">披露日期</th>
            </tr>
          </thead>
          <tbody>
            {reports.map((report, idx) => (
              <tr
                key={report.document_url || idx}
                className="border-b border-border/50 transition-colors last:border-0 hover:bg-bg/50"
              >
                <td className="px-4 py-3">
                  <input
                    type="checkbox"
                    checked={selected.has(report.document_url)}
                    onChange={() => toggleSelect(report.document_url)}
                    className="h-[18px] w-[18px] cursor-pointer rounded accent-primary"
                  />
                </td>
                <td className="px-4 py-3 font-medium leading-6 text-text">{report.title}</td>
                <td className="px-4 py-3">
                  <span
                    className={typeStyles[report.report_type] || 'secondary-table-chip'}
                  >
                    {typeLabels[report.report_type] || report.report_type}
                  </span>
                </td>
                <td className="px-4 py-3 font-mono text-xs tabular-nums text-text-muted">
                  {report.report_end}
                </td>
                <td className="px-4 py-3 font-mono text-xs tabular-nums text-text-muted">
                  {report.published_at}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return bytes + ' B'
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
  }

  const formatDateTime = (value: string) => {
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return ''
    return date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const deleteDownloadedReport = async (report: DownloadedPdf) => {
    setDeletingPath(report.relativePath)
    try {
      const res = await fetch(`/api/downloads/report-file?path=${encodeURIComponent(report.relativePath)}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(String(res.status))
      setDownloadedReports((current) => current.filter((item) => item.id !== report.id))
      setConfirmDeletePath('')
      toast({ type: 'success', title: 'PDF 已删除', description: report.filename })
    } catch {
      toast({ type: 'error', title: '删除失败', description: '请确认后端服务可用，且文件仍在 downloads 目录内。' })
    } finally {
      setDeletingPath('')
    }
  }

  return (
    <div className="secondary-page">

      {/* Search Card */}
      <div className="secondary-hero">
        <div className="secondary-hero-inner">
          <div className="max-w-3xl">
            <div className="secondary-kicker">
              <Search className="h-3.5 w-3.5" />
              Search & Download
            </div>
            <h1 className="secondary-title">搜索下载</h1>
            <p className="secondary-description">按公司名或股票代码检索公告财报，选择目标文件后进入解析与分析流程。</p>
          </div>
          <div className="secondary-step-row">
            <span className="secondary-step-chip is-active">检索</span>
            <span className="secondary-step-chip">下载</span>
            <span className="secondary-step-chip">入库</span>
          </div>
        </div>
        <div className="border-t border-border/70 px-5 py-4">
        <h3 className="mb-4 flex items-center gap-2 text-base font-semibold text-text">
          <Filter className="h-5 w-5 text-primary" />
          查询公司财报
        </h3>
        <div className="secondary-panel-muted flex flex-wrap items-end gap-3 p-4">
          <div className="flex min-w-[200px] flex-1 flex-col gap-1.5">
            <label className="secondary-label">公司名称 / 股票代码</label>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              placeholder="如：宁德时代 或 300750"
              className="form-control px-4 text-base placeholder:text-text-muted"
            />
          </div>
          <div className="flex w-[120px] flex-col gap-1.5">
            <label className="secondary-label">年份</label>
            <select
              value={year}
              onChange={(e) => setYear(e.target.value)}
              className="form-control px-4 text-base"
            >
              {years.map((y) => (
                <option key={y} value={y}>{y}</option>
              ))}
            </select>
          </div>
          <div className="flex w-[120px] flex-col gap-1.5">
            <label className="secondary-label">交易所</label>
            <select
              value={exchange}
              onChange={(e) => setExchange(e.target.value)}
              className="form-control px-4 text-base"
            >
              <option value="">自动</option>
              <option value="SZSE">深市</option>
              <option value="SSE">沪市</option>
            </select>
          </div>
          <button
            onClick={handleSearch}
            disabled={loading || !query.trim()}
            className="flex h-11 items-center gap-2 rounded-xl accent-gradient px-5 text-sm font-semibold text-white shadow-md shadow-blue-900/12 transition-all hover:-translate-y-0.5 hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Search className="h-4 w-4" />
            )}
            查询列表
          </button>
        </div>
        </div>
      </div>

      {/* Quick Download */}
      {companyInfo && (annualReports.length > 0 || financialReports.length > 0) && (
        <div className="secondary-panel flex flex-wrap gap-2 px-4 py-3">
          <span className="self-center text-sm font-semibold text-text-muted">快捷下载:</span>
          {['annual', 'semiannual', 'q1', 'q3'].map((type) => (
            <button
              key={type}
              onClick={() => handleQuickDownload([type])}
              disabled={downloading}
              className="flex items-center gap-2 rounded-full border border-primary/20 bg-primary/5 px-3.5 py-2 text-sm font-semibold text-primary transition-colors hover:bg-primary/10 disabled:opacity-50"
            >
              <Download className="h-3 w-3" />
              {typeLabels[type]}
            </button>
          ))}
          <button
            onClick={() => handleQuickDownload(['annual', 'semiannual', 'q1', 'q3'])}
            disabled={downloading}
            className="flex items-center gap-2 rounded-full accent-gradient px-3.5 py-2 text-sm font-semibold text-white shadow-sm hover:brightness-110 disabled:opacity-50"
          >
            <Download className="h-3 w-3" />
            全部下载
          </button>
        </div>
      )}

      {/* Report Tables */}
      {renderTable(annualReports, '年报列表', <FileText className="h-4 w-4 text-primary" />)}
      {renderTable(financialReports, '财报列表（半年报 / 季报）', <FileText className="h-4 w-4 text-primary" />)}

      {/* Download Selected Bar */}
      {(annualReports.length > 0 || financialReports.length > 0) && (
        <div className="flex flex-wrap items-center gap-3 rounded-[18px] border border-border bg-card/95 px-5 py-4 shadow-sm">
          <span className="text-sm text-text-muted">
            已选择 <strong className="text-text">{selected.size}</strong> 份
          </span>
          <button
            onClick={handleDownload}
            disabled={downloading || selected.size === 0}
            className="flex h-10 items-center gap-2 rounded-xl accent-gradient px-4 text-sm font-semibold text-white transition-all hover:-translate-y-0.5 hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {downloading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Download className="h-4 w-4" />
            )}
            下载选中财报
          </button>
        </div>
      )}

      {/* Download Results */}
      {downloadResults.length > 0 && (
        <div className="apple-card rounded-[24px] p-5">
          <h3 className="mb-4 flex items-center gap-2 text-base font-semibold text-text">
            <CheckCircle2 className="h-4 w-4 text-success" />
            下载结果
          </h3>
          <div className="space-y-2">
            {downloadResults.map((r, i) => (
              <div
                key={i}
                  className={`flex items-center justify-between gap-3 rounded-[14px] border px-4 py-3 text-sm ${
                  r.success !== false
                    ? 'border-border bg-card'
                    : 'border-error/20 bg-error/5'
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className={typeStyles[r.report_type] || 'secondary-table-chip'}>
                    {typeLabels[r.report_type] || r.report_type}
                  </span>
                  <span className="font-medium text-text">{r.title || r.file_name}</span>
                </div>
                <div className="flex items-center gap-3">
                  {r.size_bytes > 0 && (
                    <span className="text-xs text-text-muted">{formatBytes(r.size_bytes)}</span>
                  )}
                  {r.cache_hit && (
                    <span className="text-sm font-semibold text-primary">缓存命中</span>
                  )}
                  <span className={`text-xs font-semibold ${r.success !== false ? 'text-success' : 'text-error'}`}>
                    {r.success !== false ? '下载成功' : '失败'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Log Panel */}
      {logs.length > 0 && (
        <div className="apple-card rounded-[24px] p-5">
          <h3 className="mb-3 text-base font-semibold text-text">处理日志</h3>
          <div className="max-h-[200px] overflow-y-auto rounded-lg border border-border bg-bg/50 p-3 font-mono text-xs leading-relaxed">
            {logs.map((log, i) => (
              <div key={i} className="flex gap-3 border-b border-border/50 py-1 last:border-0">
                <span className="shrink-0 text-text-muted">{log.time}</span>
                <span
                  className={
                    log.type === 'success'
                      ? 'text-success'
                      : log.type === 'error'
                        ? 'text-error'
                        : log.type === 'warn'
                          ? 'text-warning'
                          : 'text-text'
                  }
                >
                  {log.msg}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Empty State */}
      {annualReports.length === 0 && financialReports.length === 0 && !loading && (
        <div className="rounded-[24px] border border-dashed border-border bg-card px-6 py-12 text-center text-text-muted shadow-sm">
          <Search className="mx-auto mb-3 h-10 w-10 opacity-35" />
          <p className="text-sm font-semibold text-text">输入公司名称或股票代码开始检索</p>
          <p className="mt-1 text-xs">支持 A 股公司，例如宁德时代、300750。</p>
        </div>
      )}

      <div className="apple-card rounded-[24px] p-6">
        <div className="mb-5 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h2 className="flex items-center gap-2 text-xl font-semibold text-text">
              <FolderOpen className="h-5 w-5 text-primary" />
              已下载 PDF 财报
            </h2>
            <p className="mt-1 text-base text-text-muted">来自本地 downloads 目录，点击文件可在浏览器新标签中打开 PDF。</p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="relative min-w-[240px]">
              <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
              <input
                value={downloadedQuery}
                onChange={(e) => setDownloadedQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && loadDownloadedReports(downloadedQuery)}
                placeholder="搜索公司或文件名"
                className="form-control min-h-10 w-full rounded-xl py-0 pl-10 pr-3 text-sm"
              />
            </div>
            <button
              onClick={() => loadDownloadedReports(downloadedQuery)}
              disabled={downloadedLoading}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text shadow-sm hover:bg-bg disabled:opacity-60"
            >
              {downloadedLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              刷新
            </button>
          </div>
        </div>
        {downloadedLoading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-primary" />
          </div>
        ) : downloadedReports.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-border bg-bg/40 px-5 py-8 text-center text-sm text-text-muted">
            暂无已下载 PDF。完成上方下载后，这里会自动汇总本地文件。
          </div>
        ) : (
          <div className="divide-y divide-border overflow-hidden rounded-2xl border border-border">
            {downloadedReports.map((report) => (
              <div
                key={report.id}
                className="group flex items-center gap-4 bg-card px-5 py-4 transition-colors hover:bg-primary/[0.035]"
              >
                <a href={report.url} target="_blank" rel="noreferrer" className="flex min-w-0 flex-1 items-center gap-4">
                  <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                    <FileText className="h-5 w-5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-base font-semibold text-text">{report.filename}</span>
                    <span className="mt-1 block truncate text-sm text-text-muted">{report.company} · {report.category} · {report.relativePath}</span>
                  </span>
                </a>
                <span className="hidden shrink-0 text-right text-sm text-text-muted md:block">
                  <span className="block font-mono">{formatBytes(report.size)}</span>
                  <span className="mt-1 block">{formatDateTime(report.mtime)}</span>
                </span>
                <a href={report.url} target="_blank" rel="noreferrer" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-text-muted transition-colors hover:bg-primary/10 hover:text-primary" aria-label="打开 PDF">
                  <ExternalLink className="h-5 w-5" />
                </a>
                {confirmDeletePath === report.relativePath ? (
                  <span className="flex shrink-0 items-center gap-2">
                    <button
                      onClick={() => deleteDownloadedReport(report)}
                      disabled={deletingPath === report.relativePath}
                      className="inline-flex h-10 items-center justify-center rounded-xl bg-error px-3 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-60"
                    >
                      {deletingPath === report.relativePath ? <Loader2 className="h-4 w-4 animate-spin" /> : '确认'}
                    </button>
                    <button
                      onClick={() => setConfirmDeletePath('')}
                      disabled={Boolean(deletingPath)}
                      className="inline-flex h-10 items-center justify-center rounded-xl border border-border bg-card px-3 text-sm font-semibold text-text hover:bg-bg disabled:opacity-60"
                    >
                      取消
                    </button>
                  </span>
                ) : (
                  <button
                    onClick={() => setConfirmDeletePath(report.relativePath)}
                    className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-text-muted transition-colors hover:bg-error/10 hover:text-error"
                    aria-label="删除 PDF"
                  >
                    <Trash2 className="h-5 w-5" />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
