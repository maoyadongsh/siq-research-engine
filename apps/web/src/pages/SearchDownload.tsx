import { useState, useCallback, useEffect, useMemo, useDeferredValue } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Search,
  Download,
  FileText,
  CheckCircle2,
  Loader2,
  Filter,
  Sparkles,
  AlertTriangle,
} from 'lucide-react'
import { useToast } from '../hooks/useToast'
import { EmptyState, MobileActionBar } from '@/components/page'
import { openAuthenticatedSourceLink } from '../lib/authenticatedSourceLinks'
import {
  DISCLOSURE_MARKET_ORDER,
  DISCLOSURE_MARKETS,
} from '../lib/marketMetadata'
import { loadDownloadedReports as loadDownloadedReportsApi } from '../features/pdf-parsing/api'
import {
  deleteDownloadedReport as deleteDownloadedReportApi,
  fetchCuratedAnnuals,
  fetchMarketReportHealth,
  requestReportAssist,
} from '../features/search-download/api'
import {
  logMessageClassName,
  marketSourceDisplay,
  missingMarketSourceConfig,
  reportTableTitlesForMarket,
  smartSearchPlaceholderForMarket,
} from '../features/search-download/display'
import { DownloadedReportsPanel } from '../features/search-download/DownloadedReportsPanel'
import {
  batchDownloadSelectedReports,
  downloadReportsIndividually,
  fetchReportCandidates,
  quickDownloadReportTypes,
  resolveSearchCompany,
  searchFilterForReportSearch,
  searchParamsForReportSearch,
  selectedReportsForDownload,
  type SearchDownloadCompanyInfo,
} from '../features/search-download/flows'
import { ReportTableSection } from '../features/search-download/ReportTableSection'
import {
  MARKET_CONFIGS,
  explanationMap,
  formatBytes,
  friendlyRemoteConfigError,
  isMarketCode,
  isRemoteConfigError,
  reportTypeLabel,
  typeLabels,
  typeStyles,
  uniqueBy,
  type AssistResult,
  type CandidateExplanation,
  type DownloadFileResult,
  type DownloadedPdf,
  type MarketCode,
  type MarketReportHealth,
  type ReportItem,
} from '../features/search-download/model'

export default function SearchDownload() {
  const { toast } = useToast()
  const [searchParams, setSearchParams] = useSearchParams()
  const [market, setMarket] = useState<MarketCode>(() => {
    const value = searchParams.get('market')
    return isMarketCode(value) ? value : 'CN'
  })
  const [query, setQuery] = useState(() => searchParams.get('q') || '')
  const [year, setYear] = useState(() => searchParams.get('year') || '2025')
  const [marketFilter, setMarketFilter] = useState(() => searchParams.get('exchange') || searchParams.get('form') || searchParams.get('country') || '')
  const [loading, setLoading] = useState(false)
  const [annualReports, setAnnualReports] = useState<ReportItem[]>([])
  const [financialReports, setFinancialReports] = useState<ReportItem[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [downloading, setDownloading] = useState(false)
  const [downloadResults, setDownloadResults] = useState<DownloadFileResult[]>([])
  const [logs, setLogs] = useState<{ time: string; msg: string; type: string }[]>([])
  const [companyInfo, setCompanyInfo] = useState<SearchDownloadCompanyInfo | null>(null)
  const [downloadedReports, setDownloadedReports] = useState<DownloadedPdf[]>([])
  const [downloadedLoading, setDownloadedLoading] = useState(false)
  const [downloadedQuery, setDownloadedQuery] = useState(() => searchParams.get('downloaded') || '')
  const [confirmDeletePath, setConfirmDeletePath] = useState('')
  const [deletingPath, setDeletingPath] = useState('')
  const [curatedLoading, setCuratedLoading] = useState(false)
  const [smartPrompt, setSmartPrompt] = useState(() => searchParams.get('ask') || '')
  const [assistLoading, setAssistLoading] = useState(false)
  const [assistResult, setAssistResult] = useState<AssistResult | null>(null)
  const [candidateExplanations, setCandidateExplanations] = useState<CandidateExplanation[]>([])
  const [marketHealth, setMarketHealth] = useState<MarketReportHealth | null>(null)
  const [marketHealthLoading, setMarketHealthLoading] = useState(false)
  const [marketConfigWarning, setMarketConfigWarning] = useState<string | null>(null)
  const [logsExpanded, setLogsExpanded] = useState(false)

  const currentYear = new Date().getFullYear()
  const years = Array.from({ length: 10 }, (_, i) => String(currentYear - i))
  const deferredDownloadResults = useDeferredValue(downloadResults)
  const deferredLogs = useDeferredValue(logs)
  const deferredDownloadedReports = useDeferredValue(downloadedReports)

  const marketConfig = MARKET_CONFIGS[market]
  const activeMarketSource = marketHealth?.report_finder?.markets?.[market]
  const activeMarketSourceDisplay = marketSourceDisplay({
    market,
    source: activeMarketSource,
    loading: marketHealthLoading,
  })

  const syncSearchParams = useCallback((next: { market?: MarketCode; q?: string; year?: string; exchange?: string; form?: string; country?: string; downloaded?: string; ask?: string }, replace = true) => {
    const params = new URLSearchParams(searchParams)
    for (const [key, value] of Object.entries(next)) {
      const trimmed = String(value || '').trim()
      if (trimmed) params.set(key, trimmed)
      else params.delete(key)
    }
    setSearchParams(params, { replace })
  }, [searchParams, setSearchParams])

  const setQueryAndUrl = useCallback((value: string) => {
    setQuery(value)
    syncSearchParams({ q: value })
  }, [syncSearchParams])

  const setYearAndUrl = useCallback((value: string) => {
    setYear(value)
    syncSearchParams({ year: value })
  }, [syncSearchParams])

  const setMarketAndUrl = useCallback((value: MarketCode) => {
    setMarket(value)
    setMarketFilter('')
    setSelected(new Set())
    setDownloadResults([])
    setAnnualReports([])
    setFinancialReports([])
    setCompanyInfo(null)
    setAssistResult(null)
    setCandidateExplanations([])
    setMarketConfigWarning(null)
    syncSearchParams({ market: value, exchange: '', form: '', country: '' })
  }, [syncSearchParams])

  const setMarketFilterAndUrl = useCallback((value: string) => {
    setMarketFilter(value)
    const key = MARKET_CONFIGS[market].filterParam || 'exchange'
    if (key === 'form') syncSearchParams({ form: value, exchange: '', country: '' })
    else if (key === 'country') syncSearchParams({ country: value, exchange: '', form: '' })
    else syncSearchParams({ exchange: value, form: '', country: '' })
  }, [market, syncSearchParams])

  const quickDownloadOptions = marketConfig.quickOptions
  const { annualTitle, financialTitle } = reportTableTitlesForMarket(market)

  const setDownloadedQueryAndUrl = useCallback((value: string) => {
    setDownloadedQuery(value)
    syncSearchParams({ downloaded: value })
  }, [syncSearchParams])

  const setSmartPromptAndUrl = useCallback((value: string) => {
    setSmartPrompt(value)
    syncSearchParams({ ask: value })
  }, [syncSearchParams])

  const visibleDownloadResults = useMemo(() => deferredDownloadResults, [deferredDownloadResults])
  const visibleLogs = useMemo(() => deferredLogs.slice(-200), [deferredLogs])
  const visibleDownloadedReports = useMemo(() => deferredDownloadedReports, [deferredDownloadedReports])
  const candidateExplanationMap = useMemo(() => explanationMap(candidateExplanations), [candidateExplanations])
  const hasProblemLogs = useMemo(() => logs.some((log) => log.type === 'error' || log.type === 'warn'), [logs])
  const totalCandidates = annualReports.length + financialReports.length

  const addLog = useCallback((msg: string, type = 'info') => {
    const time = new Date().toLocaleTimeString('zh-CN')
    setLogs((prev) => [...prev, { time, msg, type }])
  }, [])

  const fetchMarketHealth = useCallback(async () => {
    setMarketHealthLoading(true)
    try {
      const data = await fetchMarketReportHealth<MarketReportHealth>()
      setMarketHealth(data)
      return data
    } catch (e) {
      addLog(`官方源状态检查失败: ${(e as Error).message}`, 'warn')
      return null
    } finally {
      setMarketHealthLoading(false)
    }
  }, [addLog])

  const ensureOfficialReportSearchReady = useCallback(async (targetMarket: MarketCode) => {
    if (targetMarket !== 'JP' && targetMarket !== 'KR') {
      setMarketConfigWarning(null)
      return true
    }
    const health = marketHealth || await fetchMarketHealth()
    const source = health?.report_finder?.markets?.[targetMarket]
    if (!source && targetMarket === 'JP') {
      const message = '暂未获取到日股官方源状态；将继续尝试公司 IR 官方 PDF 与免费的 TDnet 官方近期披露列表。'
      setMarketConfigWarning(message)
      addLog(message, 'warn')
      return true
    }
    const missing = missingMarketSourceConfig(targetMarket, source)
    if (source?.report_search_ready === false && missing.length > 0) {
      const sourceName = source?.official_source || (targetMarket === 'JP' ? 'EDINET' : 'DART')
      const message = `${MARKET_CONFIGS[targetMarket].label}${sourceName} 增强源需要配置 ${missing.join('、')}；将继续使用当前可用的官方 fallback。`
      setMarketConfigWarning(message)
      addLog(message, 'warn')
      toast({ type: 'warning', title: '官方源配置缺失', description: message })
      return false
    }
    if (missing.length > 0) {
      const message = `${MARKET_CONFIGS[targetMarket].label}部分官方源缺少 ${missing.join('、')}；将优先使用可用的免费官方源查询，法定报告全量可能不完整。`
      setMarketConfigWarning(message)
      addLog(message, 'warn')
      return true
    }
    setMarketConfigWarning(null)
    return true
  }, [addLog, fetchMarketHealth, marketHealth, toast])

  useEffect(() => {
    queueMicrotask(() => {
      void fetchMarketHealth()
    })
  }, [fetchMarketHealth])

  const requestAssist = useCallback(async (payload: Record<string, unknown>) => {
    return requestReportAssist<AssistResult>(payload)
  }, [])

  const handleSmartParse = async () => {
    if (!smartPrompt.trim()) return
    setAssistLoading(true)
    addLog(`智能解析: ${smartPrompt}`, 'info')
    try {
      const result = await requestAssist({
        prompt: smartPrompt,
        market,
        report_year: parseInt(year),
      })
      const intent = result.intent || {}
      if (intent.market && isMarketCode(intent.market)) setMarketAndUrl(intent.market)
      if (intent.report_year) setYearAndUrl(String(intent.report_year))
      const nextQuery = intent.ticker || intent.company_id || intent.company_query || ''
      if (nextQuery) setQueryAndUrl(nextQuery)
      setAssistResult(result)
      const targetMarket = intent.market && isMarketCode(intent.market) ? intent.market : market
      const targetYear = String(intent.report_year || year)
      const targetQuery = String(intent.company_query || intent.ticker || intent.company_id || nextQuery || smartPrompt).trim()
      const codeText = [intent.ticker, intent.company_id].filter(Boolean).join(' / ')
      addLog(
        `已理解: ${MARKET_CONFIGS[targetMarket].label} · ${intent.company_query || targetQuery || '公司待确认'}${codeText ? ` · ${codeText}` : ''} / ${(intent.report_types || []).join('+') || '年报'}`,
        'success',
      )
      if (targetQuery) {
        const targetCountry = targetMarket === 'EU' ? marketFilter : undefined
        await runSearch({
          targetMarket,
          targetQuery,
          targetYear,
          targetTicker: intent.ticker,
          targetCompanyId: intent.company_id,
          targetCountry,
          reportTypes: intent.report_types || [],
          source: 'smart',
        })
      }
    } catch (e) {
      addLog((e as Error).message, 'error')
    } finally {
      setAssistLoading(false)
    }
  }

  const explainCandidates = useCallback(async (
    reports: ReportItem[],
    companyName: string,
    ticker: string,
    options?: { targetMarket?: MarketCode; targetYear?: string; reportTypes?: string[] },
  ) => {
    if (reports.length === 0) {
      setCandidateExplanations([])
      return
    }
    try {
      const result = await requestAssist({
        prompt: smartPrompt || undefined,
        market: options?.targetMarket || market,
        company_name: companyName,
        ticker: ticker || undefined,
        report_year: parseInt(options?.targetYear || year),
        report_types: options?.reportTypes || [],
        candidates: reports.map((report) => ({
          document_url: report.document_url,
          title: report.title,
          report_type: report.report_type,
          report_family: report.report_family,
          form: report.form,
          report_end: report.report_end,
          published_at: report.published_at,
          landing_url: report.landing_url,
        })),
      })
      const explanations = result.candidate_explanations || []
      setCandidateExplanations(explanations)
      setAssistResult((current) => ({
        ...(current || result),
        intent: result.intent || current?.intent || {},
        assistant_mode: result.assistant_mode || current?.assistant_mode,
      }))
      const recommended = explanations.filter((item) => item.recommended).map((item) => item.document_url)
      if (recommended.length > 0) {
        setSelected(new Set(recommended))
        addLog(`智能推荐 ${recommended.length} 份官方候选，已自动勾选`, 'success')
      }
    } catch (e) {
      setCandidateExplanations([])
      addLog(`候选解释失败，已保留原始列表: ${(e as Error).message}`, 'warn')
    }
  }, [market, requestAssist, smartPrompt, year, addLog])

  const loadDownloadedReports = useCallback(async (text: string) => {
    setDownloadedLoading(true)
    try {
      const data = await loadDownloadedReportsApi(text, market)
      setDownloadedReports(data.reports || [])
    } catch {
      setDownloadedReports([])
    } finally {
      setDownloadedLoading(false)
    }
  }, [market])

  useEffect(() => {
    async function init() {
      await loadDownloadedReports('')
    }
    init()
  }, [loadDownloadedReports])

  const runSearch = async ({
    targetMarket,
    targetQuery,
    targetYear,
    targetTicker,
    targetCompanyId,
    targetCountry,
    reportTypes = [],
    source = 'manual',
  }: {
    targetMarket: MarketCode
    targetQuery: string
    targetYear: string
    targetTicker?: string
    targetCompanyId?: string
    targetCountry?: string
    reportTypes?: string[]
    source?: 'manual' | 'smart'
  }) => {
    if (!targetQuery.trim() && !targetTicker && !targetCompanyId) return
    const targetConfig = MARKET_CONFIGS[targetMarket]
    const targetFilter = searchFilterForReportSearch({
      targetMarket,
      source,
      marketFilter,
      targetCountry,
    })
    syncSearchParams(searchParamsForReportSearch({
      targetMarket,
      targetQuery,
      targetYear,
      targetFilter,
    }), false)
    setMarket(targetMarket)
    setQuery(targetQuery)
    setYear(targetYear)
    if (source === 'smart') setMarketFilter('')
    setLoading(true)
    setSelected(new Set())
    setDownloadResults([])
    setAnnualReports([])
    setFinancialReports([])
    setCompanyInfo(null)
    setCandidateExplanations([])
    setMarketConfigWarning(null)
    addLog(`正在查询: ${targetConfig.label} ${targetQuery || targetTicker || targetCompanyId} (${targetYear} / ${targetFilter || '自动'})`, 'info')

    try {
      const resolvedCompany = await resolveSearchCompany({
        targetMarket,
        targetQuery,
        targetTicker,
        targetCompanyId,
        targetFilter,
      })
      const companyName = resolvedCompany.name
      const ticker = resolvedCompany.ticker
      setCompanyInfo(resolvedCompany)
      addLog(`已解析: ${companyName}${ticker ? ` (${ticker})` : ''}`, 'success')

      if (!(await ensureOfficialReportSearchReady(targetMarket))) return

      const {
        annualReports: annual,
        financialReports: financial,
        candidateReports,
      } = await fetchReportCandidates({
        targetMarket,
        targetQuery,
        targetYear,
        targetTicker,
        targetCompanyId,
        targetFilter,
        companyName,
        ticker,
      })
      setAnnualReports(annual)
      addLog(`找到 ${annual.length} 份${targetMarket === 'US' ? '年度披露' : targetMarket === 'JP' ? '有价证券报告书' : '年报'}`, 'success')
      setFinancialReports(financial)
      addLog(`找到 ${financial.length} 份定期披露`, 'success')
      await explainCandidates(
        candidateReports,
        companyName,
        ticker,
        { targetMarket, targetYear, reportTypes },
      )
    } catch (e) {
      const rawMessage = (e as Error).message
      const message = isRemoteConfigError(rawMessage) ? friendlyRemoteConfigError(rawMessage) : rawMessage
      if (isRemoteConfigError(rawMessage)) setMarketConfigWarning(message)
      addLog(`查询失败: ${message}`, isRemoteConfigError(rawMessage) ? 'warn' : 'error')
    } finally {
      setLoading(false)
    }
  }

  const handleSearch = async () => {
    await runSearch({
      targetMarket: market,
      targetQuery: query,
      targetYear: year,
      targetCountry: market === 'EU' ? marketFilter : undefined,
      source: 'manual',
    })
  }

  const handleLoadCuratedAnnuals = async () => {
    if (market !== 'JP' && market !== 'KR') return
    setCuratedLoading(true)
    setLoading(true)
    setSelected(new Set())
    setDownloadResults([])
    setAnnualReports([])
    setFinancialReports([])
    setCompanyInfo(null)
    setCandidateExplanations([])
    setAssistResult(null)
    setMarketConfigWarning(null)
    addLog(`正在载入 ${marketConfig.label} 主流 10 家年报样本 (${year})`, 'info')
    try {
      const params = new URLSearchParams({ market, report_year: year, limit: '10' })
      const data = await fetchCuratedAnnuals<{ reports?: ReportItem[] }>(params)
      const reports = uniqueBy((data.reports || []) as ReportItem[], (report) => report.document_url)
      setAnnualReports(reports)
      setFinancialReports([])
      setSelected(new Set(reports.map((report) => report.document_url)))
      setCompanyInfo({ name: `${marketConfig.label}主流公司年报样本`, ticker: '', curated: true })
      addLog(`已载入 ${reports.length} 家${marketConfig.label}主流公司年报，并自动勾选`, 'success')
    } catch (e) {
      addLog((e as Error).message, 'error')
    } finally {
      setCuratedLoading(false)
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
    addLog(`开始下载 ${selected.size} 份${marketConfig.label}披露文件...`, 'info')

    const allReports = [...annualReports, ...financialReports]
    const selectedReports = selectedReportsForDownload(allReports, selected)

    try {
      const data = await batchDownloadSelectedReports({
        market,
        companyInfo,
        marketFilter,
        reports: selectedReports,
      })
      setDownloadResults(data.results)
      addLog(`下载完成: 成功 ${data.succeeded}, 失败 ${data.failed}`, 'success')
      void loadDownloadedReports(downloadedQuery)
    } catch (err) {
      addLog(`批量下载失败: ${(err as Error).message}, 尝试逐个下载...`, 'warn')

      const fallbackResults = await downloadReportsIndividually({
        market,
        companyInfo,
        reports: selectedReports,
      })
      for (const result of fallbackResults) {
        addLog(`${result.success ? '下载成功' : '下载失败'}: ${result.report.title}`, result.success ? 'success' : 'error')
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
    addLog(`快速下载 ${reportTypes.map((t) => typeLabels[t] || t).join('+')}...`, 'info')

    try {
      const data = await quickDownloadReportTypes({
        market,
        companyInfo,
        marketFilter,
        reportTypes,
        year,
      })
      setDownloadResults(data.files)
      addLog(`下载完成: ${data.companyName} 成功 ${data.succeeded}/${data.total}`, 'success')
      void loadDownloadedReports(downloadedQuery)
    } catch (e) {
      addLog(`下载失败: ${(e as Error).message}`, 'error')
    } finally {
      setDownloading(false)
    }
  }

  const deleteDownloadedReport = async (report: DownloadedPdf) => {
    setDeletingPath(report.relativePath)
    try {
      await deleteDownloadedReportApi(report.relativePath)
      setDownloadedReports((current) => current.filter((item) => item.id !== report.id))
      setConfirmDeletePath('')
      toast({ type: 'success', title: '文件已删除', description: report.filename })
    } catch {
      toast({ type: 'error', title: '删除失败', description: '请确认后端服务可用，且文件仍在 downloads 目录内。' })
    } finally {
      setDeletingPath('')
    }
  }

  const openDownloadedReport = async (report: DownloadedPdf) => {
    try {
      await openAuthenticatedSourceLink(report.url || `/api/downloads/report-file?path=${encodeURIComponent(report.relativePath)}`)
    } catch {
      toast({ type: 'error', title: '打开失败', description: '请确认登录状态有效，且文件仍在 downloads 目录内。' })
    }
  }

  const MarketSegmentedControl = (
    <nav className="grid grid-cols-2 gap-2 rounded-[20px] border border-border bg-card p-2 shadow-sm xl:grid-cols-6" aria-label="选择披露市场">
      {DISCLOSURE_MARKET_ORDER.map((item) => {
        const marketMeta = DISCLOSURE_MARKETS[item]
        const isActive = market === item
        return (
          <button
            key={item}
            type="button"
            onClick={() => setMarketAndUrl(item)}
            className={`flex min-h-[4.5rem] items-center justify-between gap-2 rounded-2xl px-3 py-2.5 text-left font-semibold transition-colors sm:min-h-20 sm:gap-3 sm:px-4 sm:py-3 ${
              isActive
                ? 'bg-primary/10 text-primary'
                : 'text-text-muted hover:bg-bg hover:text-text'
            }`}
            aria-pressed={isActive}
            title={`${marketMeta.professionalName} · ${marketMeta.exchanges}`}
          >
            <span className="min-w-0">
              <span className="block text-[15px] font-extrabold">{marketMeta.label}</span>
              <span className="mt-0.5 block truncate font-mono text-[11px] font-bold leading-4 opacity-85">{marketMeta.exchanges}</span>
              <span className="mt-0.5 block text-xs font-semibold leading-5 opacity-85">{marketMeta.searchDescription}</span>
            </span>
            <span className="flex shrink-0 flex-col items-end gap-1">
              <span className="rounded-full border border-current/20 px-2 py-0.5 font-mono text-xs font-bold">{marketMeta.shortLabel}</span>
            </span>
          </button>
        )
      })}
    </nav>
  )

  return (
    <div className="secondary-page min-w-0 overflow-x-hidden">

      <section className="secondary-hero">
        <div className="secondary-hero-inner">
          <div className="max-w-3xl">
            <div className="secondary-kicker">
              <Search className="h-3.5 w-3.5" />
              Search & Download
            </div>
            <h1 className="secondary-title">搜索下载</h1>
            <p className="secondary-description">按公司名或股票代码检索公告财报，选择目标文件后进入解析与分析流程。</p>
          </div>
          <div className="flex w-full flex-col gap-3 lg:w-auto lg:items-end">
            <div className="secondary-step-row">
              <span className="secondary-step-chip is-active">查询</span>
              <span className="secondary-step-chip">候选 {totalCandidates}</span>
              <span className="secondary-step-chip">已选 {selected.size}</span>
            </div>
          </div>
        </div>
      </section>

      {MarketSegmentedControl}

      <section className="search-download-query secondary-panel">
          <h3 className="search-download-heading">
            <Filter className="h-5 w-5 text-primary" />
            查询公司财报
          </h3>
          <div className="smart-search-panel">
            <div className="smart-search-copy">
              <span className="smart-search-icon">
                <Sparkles className="h-4 w-4" />
              </span>
              <div className="min-w-0">
                <div className="smart-search-title">智能检索助手</div>
                <div className="smart-search-subtitle">先选择市场，再用中文描述公司、年份和报告类型；系统会映射到当地上市公司与代码。</div>
              </div>
            </div>
            <div className="smart-search-input-row">
              <input
                type="text"
                value={smartPrompt}
                onChange={(e) => setSmartPromptAndUrl(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSmartParse()}
                placeholder={smartSearchPlaceholderForMarket(market)}
                className="form-control smart-search-input px-4 text-base placeholder:text-text-muted"
              />
              <button
                onClick={handleSmartParse}
                disabled={assistLoading || !smartPrompt.trim()}
                className="smart-search-button"
              >
                {assistLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                智能检索
              </button>
            </div>
            {assistResult?.intent ? (
              <div className="smart-search-intent">
                <span className="smart-search-chip is-primary">
                  市场：{assistResult.intent.market ? MARKET_CONFIGS[assistResult.intent.market]?.label || assistResult.intent.market : marketConfig.label}
                </span>
                <span className="smart-search-chip">
                  公司：{assistResult.intent.company_query || assistResult.intent.ticker || query || '待确认'}
                </span>
                <span className="smart-search-chip">
                  年份：{assistResult.intent.report_year || year}
                </span>
                <span className="smart-search-chip">
                  报告：{(assistResult.intent.report_types || []).map((item) => typeLabels[item] || item).join(' / ') || '年报'}
                </span>
                <span className="smart-search-chip">
                  模式：{assistResult.assistant_mode?.startsWith('llm:') ? '模型增强' : '规则辅助'}
                </span>
              </div>
            ) : null}
            {market === 'JP' || market === 'KR' ? (
              <div className={activeMarketSourceDisplay.className}>
                <AlertTriangle className="h-4 w-4" />
                <span>{activeMarketSourceDisplay.message}</span>
              </div>
            ) : null}
            {marketConfigWarning ? (
              <div className="smart-search-source is-warning">
                <AlertTriangle className="h-4 w-4" />
                <span>{marketConfigWarning}</span>
              </div>
            ) : null}
          </div>
          <div className={`search-download-form without-market ${marketConfig.filterOptions ? 'has-filter' : ''}`}>
            <div className="search-download-field query-field">
              <label className="secondary-label">{marketConfig.queryLabel}</label>
              <input
                type="text"
                value={query}
                onChange={(e) => setQueryAndUrl(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                placeholder={marketConfig.queryPlaceholder}
                className="form-control px-4 text-base placeholder:text-text-muted"
              />
            </div>
            <div className="search-download-field year-field">
              <label className="secondary-label">年份</label>
              <select
                value={year}
                onChange={(e) => setYearAndUrl(e.target.value)}
                className="form-control px-4 text-base"
              >
                {years.map((y) => (
                  <option key={y} value={y}>{y}</option>
                ))}
              </select>
            </div>
            {marketConfig.filterOptions && (
              <div className="search-download-field filter-field">
                <label className="secondary-label">{marketConfig.filterLabel}</label>
                <select
                  value={marketFilter}
                  onChange={(e) => setMarketFilterAndUrl(e.target.value)}
                  className="form-control px-4 text-base"
                >
                  {marketConfig.filterOptions.map((option) => (
                    <option key={option.value || 'auto'} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </div>
            )}
            <button
              onClick={handleSearch}
              disabled={loading || !query.trim()}
              className="search-download-submit accent-gradient"
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Search className="h-4 w-4" />
              )}
              查询列表
            </button>
          </div>
          {(market === 'JP' || market === 'KR') ? (
            <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
              <button
                type="button"
                onClick={handleLoadCuratedAnnuals}
                disabled={curatedLoading || loading}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-primary/25 bg-primary/5 px-4 text-sm font-semibold text-primary transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {curatedLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                载入主流 10 家年报
              </button>
              <span className="text-xs leading-5 text-text-muted">
                {market === 'JP' ? '公司 IR 官方 PDF' : 'DART 官方 PDF'}
              </span>
            </div>
          ) : null}
      </section>

      {/* Quick Download */}
      {companyInfo && !companyInfo.curated && (annualReports.length > 0 || financialReports.length > 0) && (
        <div className="secondary-panel grid grid-cols-2 gap-2 px-4 py-3 sm:flex sm:flex-wrap sm:items-center">
          <span className="col-span-2 self-center text-sm font-semibold text-text-muted sm:col-auto">快捷下载:</span>
          {quickDownloadOptions.map((option) => (
            <button
              key={option.label}
              onClick={() => handleQuickDownload(option.types)}
              disabled={downloading}
              className={`flex min-h-11 items-center justify-center gap-2 rounded-full px-3.5 py-2 text-sm font-semibold transition-colors disabled:opacity-50 ${
                option.primary
                  ? 'col-span-2 accent-gradient text-white shadow-sm hover:brightness-110 sm:col-auto'
                  : 'border border-primary/20 bg-primary/5 text-primary hover:bg-primary/10'
              }`}
            >
              <Download className="h-3 w-3" />
              {option.label}
            </button>
          ))}
        </div>
      )}

      {/* Report Tables */}
      <ReportTableSection
        reports={annualReports}
        title={annualTitle}
        icon={<FileText className="h-4 w-4 text-primary" />}
        selected={selected}
        candidateExplanationMap={candidateExplanationMap}
        onToggleSelect={toggleSelect}
        onToggleAll={toggleAll}
      />
      <ReportTableSection
        reports={financialReports}
        title={financialTitle}
        icon={<FileText className="h-4 w-4 text-primary" />}
        selected={selected}
        candidateExplanationMap={candidateExplanationMap}
        onToggleSelect={toggleSelect}
        onToggleAll={toggleAll}
      />

      {/* Download Selected Bar */}
      {(annualReports.length > 0 || financialReports.length > 0) && (
        <MobileActionBar className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <Download className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-text">
                已选择 <span className="font-mono text-base text-primary">{selected.size}</span> 份披露文件
              </p>
              <p className="text-xs text-text-muted">选择后点击下载，系统将自动拉取到本地</p>
            </div>
          </div>
          <button
            onClick={handleDownload}
            disabled={downloading || selected.size === 0}
            className="flex h-11 w-full items-center justify-center gap-2 rounded-xl accent-gradient px-5 text-sm font-semibold text-white shadow-lg shadow-primary/15 transition-all hover:-translate-y-0.5 hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60 disabled:shadow-none sm:h-10 sm:w-auto"
          >
            {downloading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Download className="h-4 w-4" />
            )}
            下载选中披露文件
          </button>
        </MobileActionBar>
      )}

      {/* Download Results */}
      {downloadResults.length > 0 && (
        <div className="apple-card rounded-[var(--radius-panel)] p-4 sm:p-5">
          <h3 className="mb-4 flex items-center gap-2 text-base font-semibold text-text">
            <CheckCircle2 className="h-4 w-4 text-success" />
            下载结果
          </h3>
          <div className="space-y-2">
            {visibleDownloadResults.map((r, i) => (
              <div
                key={i}
                  className={`content-auto flex flex-col gap-3 rounded-[14px] border px-3 py-3 text-sm sm:flex-row sm:items-center sm:justify-between sm:px-4 ${
                  r.success !== false
                    ? 'border-border bg-card'
                    : 'border-error/20 bg-error/5'
                }`}
              >
                <div className="flex min-w-0 items-start gap-2 sm:items-center">
                  <span className={typeStyles[r.report_type] || 'secondary-table-chip'}>
                    {reportTypeLabel(r)}
                  </span>
                  <span className="min-w-0 break-words font-medium text-text">{r.title || r.file_name}</span>
                </div>
                <div className="flex flex-wrap items-center gap-3">
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
        <div className="apple-card rounded-[var(--radius-panel)] p-4 sm:p-5">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h3 className="text-base font-semibold text-text">处理日志</h3>
              <p className="mt-1 text-sm text-text-muted">
                {hasProblemLogs ? '检测到需要关注的查询或下载提示。' : '日志默认折叠，避免干扰主流程。'}
              </p>
            </div>
            <button
              type="button"
              onClick={() => setLogsExpanded((value) => !value)}
              className="inline-flex h-10 items-center justify-center rounded-[var(--radius-control)] border border-border bg-card px-4 text-sm font-semibold text-text-muted hover:bg-bg hover:text-text"
            >
              {logsExpanded || hasProblemLogs ? '收起日志' : `展开 ${logs.length} 条`}
            </button>
          </div>
          {(logsExpanded || hasProblemLogs) && (
            <div className="mt-3 max-h-[220px] overflow-y-auto rounded-lg border border-border bg-bg/50 p-3 font-mono text-xs leading-relaxed">
              {visibleLogs.map((log, i) => (
                <div key={i} className="flex flex-col gap-1 border-b border-border/50 py-2 last:border-0 sm:flex-row sm:gap-3 sm:py-1">
                  <span className="shrink-0 text-text-muted">{log.time}</span>
                  <span
                    className={logMessageClassName(log.type)}
                  >
                    {log.msg}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Empty State */}
      {annualReports.length === 0 && financialReports.length === 0 && !loading && (
        <EmptyState
          icon={Search}
          title={marketConfig.emptyText}
          description={marketConfig.helpText}
          className="surface-card border-dashed py-10 sm:py-12"
        />
      )}

      <DownloadedReportsPanel
        reports={visibleDownloadedReports}
        loading={downloadedLoading}
        query={downloadedQuery}
        confirmDeletePath={confirmDeletePath}
        deletingPath={deletingPath}
        onQueryChange={setDownloadedQueryAndUrl}
        onRefresh={() => loadDownloadedReports(downloadedQuery)}
        onOpen={(report) => void openDownloadedReport(report)}
        onRequestDelete={setConfirmDeletePath}
        onConfirmDelete={(report) => void deleteDownloadedReport(report)}
        onCancelDelete={() => setConfirmDeletePath('')}
      />
    </div>
  )
}
