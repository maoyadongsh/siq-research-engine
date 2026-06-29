import { useState, useCallback, useEffect, useMemo, useDeferredValue } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
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
  Play,
  Sparkles,
  AlertTriangle,
} from 'lucide-react'
import { useToast } from '../hooks/useToast'
import { EmptyState, MobileActionBar } from '@/components/page'
import { openAuthenticatedSourceLink } from '../lib/authenticatedSourceLinks'
import {
  DISCLOSURE_MARKET_ORDER,
  DISCLOSURE_MARKETS,
  isDisclosureMarketCode,
  type DisclosureMarketCode,
} from '../lib/marketMetadata'
import { loadDownloadedReports as loadDownloadedReportsApi } from '../lib/pdfApi'

interface ReportItem {
  title: string
  report_type: string
  report_family?: string
  form?: string
  company_id?: string
  ticker?: string
  company_name?: string
  report_end: string
  published_at: string
  document_url: string
  landing_url?: string
  file_format?: string
  file_name?: string
  saved_path?: string
  size_bytes?: number
  success?: boolean
  metadata?: Record<string, unknown>
}

interface DownloadFileResult {
  document_url?: string
  company_name?: string
  title: string
  report_type: string
  report_end: string
  file_name: string
  saved_path: string
  size_bytes: number
  success?: boolean
  cache_hit?: boolean
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
  contentType?: string
  isPdf?: boolean
}

type MarketCode = DisclosureMarketCode

interface AssistIntent {
  market?: MarketCode
  company_query?: string
  ticker?: string
  company_id?: string
  cik?: string
  report_year?: number
  report_types?: string[]
  confidence?: number
  notes?: string[]
}

interface CandidateExplanation {
  document_url: string
  title_zh: string
  report_type_zh: string
  period_zh: string
  recommendation: string
  recommended?: boolean
  warnings?: string[]
}

interface AssistResult {
  intent: AssistIntent
  candidate_explanations?: CandidateExplanation[]
  assistant_mode?: string
}

interface MarketSourceStatus {
  official_source?: string
  report_search_ready?: boolean
  required_config?: string[]
  message?: string
}

interface MarketReportHealth {
  report_finder?: {
    status?: string
    markets?: Partial<Record<MarketCode, MarketSourceStatus>>
  }
}

interface MarketConfig {
  label: string
  shortLabel: string
  queryLabel: string
  queryPlaceholder: string
  filterLabel?: string
  filterParam?: 'exchange' | 'form' | 'country'
  filterOptions?: { value: string; label: string }[]
  helpText: string
  emptyText: string
  quickOptions: { label: string; types: string[]; primary?: boolean }[]
}

const MARKET_CONFIGS: Record<MarketCode, MarketConfig> = {
  CN: {
    label: DISCLOSURE_MARKETS.CN.label,
    shortLabel: 'CN',
    queryLabel: '公司名称 / 股票代码',
    queryPlaceholder: '如：比亚迪 或 002594',
    filterLabel: '交易所',
    filterParam: 'exchange',
    filterOptions: [
      { value: '', label: '自动' },
      { value: 'SZSE', label: '深市' },
      { value: 'SSE', label: '沪市' },
    ],
    helpText: '支持已入库 A 股公司，例如比亚迪、002594。',
    emptyText: '输入 A 股公司名称或股票代码开始检索',
    quickOptions: [
      { label: '年报', types: ['annual'] },
      { label: '半年报', types: ['semiannual'] },
      { label: '一季报', types: ['q1'] },
      { label: '三季报', types: ['q3'] },
      { label: '全部下载', types: ['annual', 'semiannual', 'q1', 'q3'], primary: true },
    ],
  },
  HK: {
    label: DISCLOSURE_MARKETS.HK.label,
    shortLabel: 'HK',
    queryLabel: '港股代码 / 公司名称',
    queryPlaceholder: '优先输入 5 位港股代码，如：03690；也可输入 MEITUAN-W',
    helpText: '港股优先推荐使用 5 位股票代码；公司名称会尽量匹配 HKEX 中英文官方目录。',
    emptyText: '优先输入 5 位港股代码开始检索',
    quickOptions: [
      { label: '年报', types: ['annual'] },
      { label: '中报', types: ['semiannual'] },
      { label: '季报', types: ['quarterly'] },
      { label: '全部下载', types: ['annual', 'semiannual', 'quarterly'], primary: true },
    ],
  },
  US: {
    label: DISCLOSURE_MARKETS.US.label,
    shortLabel: 'US',
    queryLabel: 'Ticker / CIK / 公司名称',
    queryPlaceholder: '如：AAPL、MSFT 或 0000320193',
    filterLabel: '表单',
    filterParam: 'form',
    filterOptions: [
      { value: '', label: '常用' },
      { value: '10-K', label: '10-K' },
      { value: '10-Q', label: '10-Q' },
      { value: '20-F', label: '20-F' },
      { value: '6-K', label: '6-K' },
    ],
    helpText: '支持 SEC EDGAR，建议优先使用 ticker 或 CIK。',
    emptyText: '输入美股 ticker、CIK 或公司名称开始检索',
    quickOptions: [
      { label: '10-K/20-F 年报', types: ['annual', '10-K', '20-F'] },
      { label: '10-Q/6-K 季报', types: ['quarterly', '10-Q', '6-K'] },
      { label: '全部下载', types: ['annual', 'quarterly', '10-K', '10-Q', '20-F', '6-K'], primary: true },
    ],
  },
  EU: {
    label: DISCLOSURE_MARKETS.EU.label,
    shortLabel: 'EU',
    queryLabel: 'ISIN / LEI / Ticker / 公司名称',
    queryPlaceholder: '如：ASML、GB00BP6MXD84 或 5493001KJTIIGC8Y1R12',
    filterLabel: '国家 / 官方源',
    filterParam: 'country',
    filterOptions: [
      { value: '', label: '自动识别' },
      { value: 'UK', label: '英国 UK' },
      { value: 'FR', label: '法国 France' },
      { value: 'DE', label: '德国 Germany' },
      { value: 'NL', label: '荷兰 Netherlands' },
      { value: 'CH', label: '瑞士 Switzerland' },
    ],
    helpText: '先覆盖 UK、法国、德国、荷兰、瑞士；下载源按国家走官方/权威披露系统，文件格式可能是 PDF、XHTML/iXBRL 或 ZIP。',
    emptyText: '选择欧股国家后，输入 ISIN、LEI、ticker 或公司名称开始检索',
    quickOptions: [
      { label: '年度财务报告', types: ['annual'] },
      { label: '半年报 / 中期报告', types: ['semiannual'] },
      { label: '定期披露', types: ['annual', 'semiannual', 'quarterly'], primary: true },
    ],
  },
  KR: {
    label: DISCLOSURE_MARKETS.KR.label,
    shortLabel: 'KR',
    queryLabel: '韩国股票代码 / 公司名称 / DART Corp Code',
    queryPlaceholder: '如：005930、三星电子 或 00126380',
    helpText: '使用韩国 DART 官方披露；无 DART_API_KEY 时可下载 DART 官方 PDF，配置 key 后可增强为 OpenDART ZIP。',
    emptyText: '输入韩股代码、DART Corp Code 或公司名称开始检索',
    quickOptions: [
      { label: '年报', types: ['annual'] },
      { label: '半年报', types: ['semiannual'] },
      { label: '季报', types: ['quarterly'] },
      { label: '全部下载', types: ['annual', 'semiannual', 'quarterly'], primary: true },
    ],
  },
  JP: {
    label: DISCLOSURE_MARKETS.JP.label,
    shortLabel: 'JP',
    queryLabel: '日本证券代码 / 公司名称 / EDINET Code',
    queryPlaceholder: '如：7203、トヨタ自動車 或 E02144',
    helpText: '使用日本公司 IR 官方 PDF、EDINET 与 TDnet；无 EDINET_API_KEY 时可下载主流公司 IR 年报。',
    emptyText: '输入日股证券代码、EDINET Code 或公司名称开始检索',
    quickOptions: [
      { label: '有价证券报告书', types: ['annual'] },
      { label: '半期报告书', types: ['semiannual'] },
      { label: '季度报告书', types: ['quarterly'] },
      { label: '全部下载', types: ['annual', 'semiannual', 'quarterly'], primary: true },
    ],
  },
}

const typeLabels: Record<string, string> = {
  annual: '年报',
  semiannual: '半年报',
  quarterly: '季报',
  q1: '一季报',
  q3: '三季报',
  '10-K': '10-K',
  '10-Q': '10-Q',
  '20-F': '20-F',
  '6-K': '6-K',
  earnings_release: '业绩公告',
}

const typeStyles: Record<string, string> = {
  annual: 'secondary-table-chip',
  semiannual: 'secondary-table-chip',
  quarterly: 'secondary-table-chip',
  q1: 'secondary-table-chip',
  q3: 'secondary-table-chip',
  '10-K': 'secondary-table-chip',
  '10-Q': 'secondary-table-chip',
  '20-F': 'secondary-table-chip',
  '6-K': 'secondary-table-chip',
  earnings_release: 'secondary-table-chip',
}

const marketSourceConfigLabels: Partial<Record<MarketCode, string>> = {
  JP: 'EDINET_API_KEY',
  KR: 'DART_API_KEY',
}

function isMarketCode(value: string | null): value is MarketCode {
  return isDisclosureMarketCode(value)
}

function isRemoteConfigError(message: string) {
  return /EDINET_API_KEY|DART_API_KEY/.test(message)
}

function friendlyRemoteConfigError(message: string) {
  if (message.includes('EDINET_API_KEY')) {
    return '日股 EDINET 全量法定披露需要后端配置 EDINET_API_KEY；当前仍可使用公司 IR 官方 PDF 与 TDnet 免费披露。'
  }
  if (message.includes('DART_API_KEY')) {
    return '韩股 OpenDART ZIP 下载需要后端配置 DART_API_KEY；当前仍可使用 DART 官方 PDF 下载主流公司年报。'
  }
  return message
}

function looksLikeCik(value: string) {
  return /^\d{7,10}$/.test(value.trim())
}

function looksLikeTicker(value: string) {
  return /^[A-Za-z][A-Za-z0-9.-]{0,9}$/.test(value.trim())
}

function looksLikeHkTicker(value: string) {
  return /^\d{1,5}$/.test(value.trim())
}

function looksLikeKrTicker(value: string) {
  return /^\d{1,6}$/.test(value.trim())
}

function looksLikeJpTicker(value: string) {
  return /^\d{4,5}$/.test(value.trim())
}

function looksLikeEdinetCode(value: string) {
  return /^E\d{5}$/i.test(value.trim())
}

function looksLikeIsin(value: string) {
  return /^[A-Z]{2}[A-Z0-9]{9}\d$/i.test(value.trim())
}

function looksLikeLei(value: string) {
  return /^[A-Z0-9]{20}$/i.test(value.trim())
}

function identifierPayload(market: MarketCode, query: string, resolvedTicker?: string) {
  const trimmed = query.trim()
  if (market === 'US') {
    if (looksLikeCik(trimmed)) return { cik: trimmed }
    if (looksLikeTicker(trimmed)) return { ticker: trimmed.toUpperCase() }
    return { company_name: trimmed }
  }
  if (market === 'HK' && looksLikeHkTicker(trimmed)) {
    return { ticker: trimmed.padStart(5, '0') }
  }
  if (market === 'KR') {
    if (/^\d{8}$/.test(trimmed)) return { company_id: trimmed }
    if (looksLikeKrTicker(trimmed)) return { ticker: trimmed.padStart(6, '0') }
    return { company_name: trimmed }
  }
  if (market === 'JP') {
    if (looksLikeEdinetCode(trimmed)) return { company_id: trimmed.toUpperCase() }
    if (looksLikeJpTicker(trimmed)) return { ticker: trimmed }
    return { company_name: trimmed }
  }
  if (market === 'EU') {
    if (looksLikeIsin(trimmed) || looksLikeLei(trimmed)) return { company_id: trimmed.toUpperCase() }
    if (looksLikeTicker(trimmed)) return { ticker: trimmed.toUpperCase() }
    return { company_name: trimmed }
  }
  if (resolvedTicker) {
    return { company_name: trimmed, ticker: resolvedTicker }
  }
  return { company_name: trimmed }
}

function reportTypeLabel(report: ReportItem | DownloadFileResult) {
  return typeLabels[report.report_type] || report.report_type || '报告'
}

function explanationMap(items: CandidateExplanation[]) {
  return new Map(items.map((item) => [item.document_url, item]))
}

function parsePathForDownloadedReport(relativePath: string) {
  if (relativePath.startsWith('HK/')) return '/parse-hk'
  if (relativePath.startsWith('US/')) return '/parse?market=US'
  if (relativePath.startsWith('EU/')) return '/parse-eu'
  if (relativePath.startsWith('JP/')) return '/parse-jp'
  if (relativePath.startsWith('KR/')) return '/parse-kr'
  return '/parse'
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
  const [companyInfo, setCompanyInfo] = useState<{ name: string; ticker: string; curated?: boolean } | null>(null)
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
  const activeMarketMissingConfig = activeMarketSource?.required_config?.length
    ? activeMarketSource.required_config
    : marketSourceConfigLabels[market] && activeMarketSource?.report_search_ready === false
      ? [marketSourceConfigLabels[market]]
      : []
  const activeMarketSearchBlocked = Boolean(activeMarketSource?.report_search_ready === false && activeMarketMissingConfig.length)
  const showMarketSourceReady = Boolean(
    activeMarketSource && activeMarketSource.report_search_ready !== false && activeMarketMissingConfig.length === 0,
  )

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

  const annualFormsForMarket = useCallback((targetMarket: MarketCode, targetFilter = '') => {
    if (targetMarket !== 'US') return []
    if (targetFilter && ['10-K', '20-F'].includes(targetFilter)) return [targetFilter]
    if (targetFilter) return []
    return ['10-K', '20-F']
  }, [])

  const financialFormsForMarket = useCallback((targetMarket: MarketCode, targetFilter = '') => {
    if (targetMarket !== 'US') return []
    if (targetFilter) return [targetFilter]
    return ['10-Q', '6-K']
  }, [])

  const fileFormatFromUrl = useCallback((url: string) => {
    const clean = url.toLowerCase().split(/[?#]/, 1)[0]
    if (clean.includes('dart.fss.or.kr/pdf/download/pdf.do')) return 'pdf'
    if (clean.includes('dart.fss.or.kr/dsaf001/') || clean.includes('kind.krx.co.kr/')) return 'html'
    const suffix = clean.match(/\.([a-z0-9]+)$/)?.[1]
    const openable = new Set(['pdf', 'html', 'htm', 'xml', 'txt', 'zip'])
    if (!suffix || !openable.has(suffix)) return market === 'US' ? 'htm' : market === 'KR' ? 'html' : market === 'EU' ? 'zip' : 'pdf'
    return suffix
  }, [market])

  const downloadItemMarketPayload = useCallback((report: ReportItem) => ({
    market,
    document_url: report.document_url,
    company_name: report.company_name || companyInfo?.name || '',
    title: report.title,
    ticker: report.ticker || companyInfo?.ticker || undefined,
    company_id: market === 'EU'
      ? `${String(report.metadata?.country || marketFilter || '').trim()}:${report.company_id || report.ticker || companyInfo?.ticker || companyInfo?.name || ''}`.replace(/^:/, '')
      : report.company_id,
    report_type: report.report_type,
    report_end: report.report_end,
    published_at: report.published_at,
    landing_url: report.landing_url,
    file_format: report.file_format || fileFormatFromUrl(report.document_url),
  }), [companyInfo, fileFormatFromUrl, market, marketFilter])

  const identifierPayloadFor = useCallback((targetMarket: MarketCode, targetQuery: string, resolvedTicker?: string, companyId?: string, targetCountry?: string) => {
    const base = identifierPayload(targetMarket, targetQuery, resolvedTicker)
    const countryPrefix = targetMarket === 'EU' && targetCountry ? `${targetCountry}:` : ''
    if (companyId) return { ...base, company_id: `${countryPrefix}${companyId}` }
    if (targetMarket === 'EU' && targetCountry) {
      if ('ticker' in base && typeof base.ticker === 'string') return { company_id: `${countryPrefix}${base.ticker}`, ticker: base.ticker }
      if ('company_id' in base && typeof base.company_id === 'string') return { company_id: `${countryPrefix}${base.company_id}` }
      return { ...base, company_id: `${countryPrefix}${targetQuery.trim()}` }
    }
    return base
  }, [])

  const quickDownloadOptions = marketConfig.quickOptions

  const annualTitle = market === 'US' ? '年度报告列表' : market === 'JP' ? '有价证券报告书列表' : market === 'EU' ? '年度财务报告列表' : '年报列表'
  const financialTitle = market === 'US'
    ? '定期披露列表（10-Q / 20-F / 6-K）'
    : market === 'EU'
      ? '其他定期披露列表'
    : market === 'HK'
      ? '财报列表（中期 / 季度）'
      : market === 'JP'
        ? '财报列表（半期 / 季度）'
        : '财报列表（半年报 / 季报）'

  const resolveCompanyName = useCallback((resolved: Record<string, unknown>) => {
    const resolvedCompany = (resolved.resolved || resolved) as Record<string, unknown>
    const companyName = String(
      resolvedCompany.company_name
      || resolvedCompany.canonical_name
      || resolvedCompany.display_name
      || resolved.company_name
      || resolved.name
      || query
    )
    const ticker = String(resolvedCompany.ticker || resolved.ticker || resolved.code || '')
    return { companyName, ticker }
  }, [query])

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
      const res = await fetch('/api/market-report-health')
      if (!res.ok) throw new Error(String(res.status))
      const data = await res.json() as MarketReportHealth
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
    const fallbackKey = marketSourceConfigLabels[targetMarket]
  const missing = source?.required_config?.length
    ? source.required_config
    : source?.report_search_ready === false && fallbackKey
      ? [fallbackKey]
      : []
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
    const res = await fetch('/api/v1/reports/assist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) throw new Error(`智能解析失败: ${res.status}`)
    return await res.json() as AssistResult
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

  const linkDownloadedFiles = useCallback(async (items: DownloadFileResult[]) => {
    const successful = items.filter((item) => item.success !== false && (item.saved_path || item.file_name))
    await Promise.allSettled(successful.map((item) => fetch('/api/workspace/downloads/link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        saved_path: item.saved_path,
        file_name: item.file_name,
        company_name: item.company_name,
        source: item.cache_hit ? 'reused_download_cache' : 'new_download',
      }),
    })))
  }, [])

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
    const targetFilter = targetMarket === 'EU'
      ? (targetCountry ?? (source === 'manual' ? marketFilter : ''))
      : source === 'manual' ? marketFilter : ''
    const targetAnnualForms = annualFormsForMarket(targetMarket, targetFilter)
    const targetFinancialForms = financialFormsForMarket(targetMarket, targetFilter)
    syncSearchParams({
      market: targetMarket,
      q: targetQuery,
      year: targetYear,
      exchange: targetMarket === 'CN' ? targetFilter : '',
      form: targetMarket === 'US' ? targetFilter : '',
      country: targetMarket === 'EU' ? targetFilter : '',
    }, false)
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
      // Step 1: Resolve company
      const resolveRes = await fetch('/api/v1/company/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          market: targetMarket,
          ...identifierPayloadFor(targetMarket, targetQuery, targetTicker, targetCompanyId, targetFilter),
        }),
      })

      if (!resolveRes.ok) {
        const errorText = await resolveRes.text().catch(() => '')
        throw new Error(`解析公司失败: ${resolveRes.status}${errorText ? ` ${errorText.slice(0, 160)}` : ''}`)
      }
      const resolved = await resolveRes.json()
      const { companyName, ticker } = resolveCompanyName(resolved)
      setCompanyInfo({ name: companyName, ticker })
      addLog(`已解析: ${companyName}${ticker ? ` (${ticker})` : ''}`, 'success')

      if (!(await ensureOfficialReportSearchReady(targetMarket))) return

      const baseReportPayload = {
        market: targetMarket,
        company_name: companyName,
        ticker: ticker || targetTicker || undefined,
        company_id: targetMarket === 'EU' && targetFilter
          ? `${targetFilter}:${targetCompanyId || ticker || targetQuery}`
          : targetCompanyId || undefined,
        exchange_hint: targetMarket === 'CN' ? targetFilter || undefined : undefined,
      }

      // Step 2: Query annual reports
      let annual: ReportItem[] = []
      if (targetMarket !== 'US' || targetAnnualForms.length > 0) {
        const annualRes = await fetch('/api/v1/reports/recent', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ...baseReportPayload,
            target: 'annual_report',
            report_year: parseInt(targetYear),
            forms: targetAnnualForms,
            limit: 10,
          }),
        })
        if (!annualRes.ok) {
          const errorText = await annualRes.text().catch(() => '')
          throw new Error(`查询年报失败: ${annualRes.status}${errorText ? ` ${errorText.slice(0, 160)}` : ''}`)
        }
        const annualData = await annualRes.json()
        annual = Array.isArray(annualData) ? annualData : annualData.reports || annualData.items || []
      }
      setAnnualReports(annual)
      addLog(`找到 ${annual.length} 份${targetMarket === 'US' ? '年度披露' : targetMarket === 'JP' ? '有价证券报告书' : '年报'}`, 'success')

      // Step 3: Query financial reports
      const finRes = await fetch('/api/v1/reports/recent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ...baseReportPayload,
            target: 'financial_report',
            report_year: parseInt(targetYear),
            forms: targetFinancialForms,
            limit: 20,
          }),
      })
      if (!finRes.ok) {
        const errorText = await finRes.text().catch(() => '')
        throw new Error(`查询定期披露失败: ${finRes.status}${errorText ? ` ${errorText.slice(0, 160)}` : ''}`)
      }
      const finData = await finRes.json()
      const financial = Array.isArray(finData) ? finData : finData.reports || finData.items || []
      setFinancialReports(financial)
      addLog(`找到 ${financial.length} 份定期披露`, 'success')
      await explainCandidates(
        uniqueBy([...annual, ...financial], (report) => report.document_url),
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
      const res = await fetch(`/api/v1/reports/curated-annuals?${params.toString()}`)
      if (!res.ok) {
        const errorText = await res.text().catch(() => '')
        throw new Error(`载入样本失败: ${res.status}${errorText ? ` ${errorText.slice(0, 160)}` : ''}`)
      }
      const data = await res.json()
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
    const selectedReports = uniqueBy(
      allReports.filter((r) => selected.has(r.document_url)),
      (report) => report.document_url,
    )

    try {
      const items = selectedReports.map(downloadItemMarketPayload)

      const res = await fetch('/api/v1/reports/batch-download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          market,
          default_company_name: companyInfo.name,
          items,
        }),
      })

      if (!res.ok) throw new Error(`下载失败: ${res.status}`)
      const data = await res.json()

      const results = uniqueBy(data.results || data.files || [], (item: DownloadFileResult) => item.document_url || item.file_name)
      setDownloadResults(results)
      await linkDownloadedFiles(results)
      addLog(`下载完成: 成功 ${data.succeeded || 0}, 失败 ${data.failed || 0}`, 'success')
      void loadDownloadedReports(downloadedQuery)
    } catch (err) {
      addLog(`批量下载失败: ${(err as Error).message}, 尝试逐个下载...`, 'warn')

      // Fallback: download one by one
      for (const report of selectedReports) {
        try {
          const res = await fetch('/api/v1/reports/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              market,
              company_name: companyInfo.name,
              ticker: companyInfo.ticker || undefined,
              document_url: report.document_url,
              title: report.title,
            }),
          })
          if (res.ok) {
            addLog(`下载成功: ${report.title}`, 'success')
          } else {
            addLog(`下载失败: ${report.title} (HTTP ${res.status})`, 'error')
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
    addLog(`快速下载 ${reportTypes.map((t) => typeLabels[t] || t).join('+')}...`, 'info')

    try {
      const res = await fetch('/api/v1/reports/select-download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          market,
          company_name: companyInfo.name,
          ticker: companyInfo.ticker || undefined,
          company_id: market === 'EU' && marketFilter && companyInfo.ticker ? `${marketFilter}:${companyInfo.ticker}` : undefined,
          report_types: reportTypes,
          report_year: parseInt(year),
        }),
      })

      if (!res.ok) throw new Error(`下载失败: ${res.status}`)
      const data = await res.json()

      const files = data.files || []
      setDownloadResults(files)
      await linkDownloadedFiles(files)
      addLog(`下载完成: ${data.company_name} 成功 ${data.succeeded}/${data.total}`, 'success')
      void loadDownloadedReports(downloadedQuery)
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
      <div className="overflow-hidden rounded-[var(--radius-panel)] border border-border bg-card shadow-sm">
        <div className="flex flex-col gap-3 border-b border-border px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <h3 className="flex min-w-0 items-center gap-2 text-base font-semibold text-text">
            {icon}
            {title}
          </h3>
          <label className="flex h-10 cursor-pointer items-center gap-2 self-start rounded-xl border border-border bg-bg/50 px-3 text-sm font-semibold text-text-muted transition-colors hover:bg-bg sm:self-auto">
            全选
            <input
              type="checkbox"
              checked={allChecked}
              onChange={() => toggleAll(reports)}
              className="h-5 w-5 cursor-pointer rounded accent-primary"
            />
          </label>
        </div>
        <div className="divide-y divide-border/60 md:hidden">
          {reports.map((report, idx) => {
            const explanation = candidateExplanationMap.get(report.document_url)
            return (
              <div key={report.document_url || idx} className="p-4">
                <label className="flex cursor-pointer items-start gap-3">
                  <input
                    type="checkbox"
                    checked={selected.has(report.document_url)}
                    onChange={() => toggleSelect(report.document_url)}
                    className="mt-0.5 h-5 w-5 shrink-0 cursor-pointer rounded accent-primary"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block break-words text-sm font-semibold leading-6 text-text">{report.title}</span>
                    {explanation ? (
                      <span className="mt-1 block break-words text-sm leading-6 text-text-muted">{explanation.title_zh}</span>
                    ) : null}
                    <span className="mt-3 flex flex-wrap items-center gap-2 text-xs text-text-muted">
                      <span className={typeStyles[report.report_type] || 'secondary-table-chip'}>
                        {explanation?.report_type_zh || reportTypeLabel(report)}
                      </span>
                      <span className="rounded-full border border-border bg-bg/60 px-2.5 py-1 font-mono tabular-nums">
                        {explanation?.period_zh || report.report_end || '-'}
                      </span>
                      <span className="rounded-full border border-border bg-bg/60 px-2.5 py-1 font-mono tabular-nums">
                        披露 {report.published_at || '-'}
                      </span>
                      {explanation?.warnings?.length ? (
                        <span className="rounded-full border border-warning/20 bg-warning/10 px-2.5 py-1 text-warning">
                          {explanation.warnings.join('；')}
                        </span>
                      ) : null}
                      {explanation ? (
                        <span className={`rounded-xl border px-2.5 py-1.5 ${
                          explanation.recommended ? 'border-primary/20 bg-primary/5 text-primary' : 'border-border bg-bg/60'
                        }`}>
                          {explanation.recommended ? '推荐：' : ''}{explanation.recommendation}
                        </span>
                      ) : null}
                    </span>
                  </span>
                </label>
              </div>
            )
          })}
        </div>
        <div className="scroll-hint hidden overflow-x-auto md:block">
          <table className="w-full min-w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-bg/60">
                <th className="w-12 px-4 py-3 text-left text-[11px] font-bold uppercase tracking-wider text-text-muted">选择</th>
                <th className="px-4 py-3 text-left text-[11px] font-bold uppercase tracking-wider text-text-muted">报告标题</th>
                <th className="min-w-[12rem] px-4 py-3 text-left text-[11px] font-bold uppercase tracking-wider text-text-muted">中文说明</th>
                <th className="px-4 py-3 text-left text-[11px] font-bold uppercase tracking-wider text-text-muted">类型</th>
                <th className="px-4 py-3 text-left text-[11px] font-bold uppercase tracking-wider text-text-muted">报告期</th>
                <th className="px-4 py-3 text-left text-[11px] font-bold uppercase tracking-wider text-text-muted">披露日期</th>
              </tr>
            </thead>
            <tbody>
              {reports.map((report, idx) => {
                const explanation = candidateExplanationMap.get(report.document_url)
                return (
                  <tr
                    key={report.document_url || idx}
                    className="border-b border-border/50 transition-colors last:border-0 hover:bg-bg/50"
                  >
                    <td className="px-4 py-3 align-top">
                      <input
                        type="checkbox"
                        checked={selected.has(report.document_url)}
                        onChange={() => toggleSelect(report.document_url)}
                        className="h-5 w-5 cursor-pointer rounded accent-primary"
                      />
                    </td>
                    <td className="px-4 py-3 align-top font-medium leading-6 text-text">{report.title}</td>
                    <td className="px-4 py-3 align-top leading-6 text-text-muted">
                      {explanation ? (
                        <div className="space-y-1">
                          <div className="font-medium text-text">{explanation.title_zh}</div>
                          <div className={explanation.recommended ? 'text-primary' : ''}>{explanation.recommendation}</div>
                          {explanation.warnings?.length ? (
                            <div className="text-warning">{explanation.warnings.join('；')}</div>
                          ) : null}
                        </div>
                      ) : (
                        <span className="text-xs">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3 align-top">
                      <span className={typeStyles[report.report_type] || 'secondary-table-chip'}>
                        {explanation?.report_type_zh || reportTypeLabel(report)}
                      </span>
                    </td>
                    <td className="px-4 py-3 align-top font-mono text-xs tabular-nums text-text-muted">
                      {explanation?.period_zh || report.report_end}
                    </td>
                    <td className="px-4 py-3 align-top font-mono text-xs tabular-nums text-text-muted">
                      {report.published_at}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
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
            className={`flex min-h-[4.5rem] items-center justify-between gap-2 rounded-2xl px-3 py-2.5 text-left transition-colors sm:min-h-20 sm:gap-3 sm:px-4 sm:py-3 ${
              isActive
                ? 'bg-primary/10 text-primary'
                : 'text-text-muted hover:bg-bg hover:text-text'
            }`}
            aria-pressed={isActive}
            title={`${marketMeta.professionalName} · ${marketMeta.exchanges}`}
          >
            <span className="min-w-0">
              <span className="block text-sm font-semibold">{marketMeta.label}</span>
              <span className="mt-0.5 block truncate font-mono text-[11px] leading-4 opacity-75">{marketMeta.exchanges}</span>
              <span className="mt-0.5 block text-xs leading-5 opacity-70">{marketMeta.searchDescription}</span>
            </span>
            <span className="flex shrink-0 flex-col items-end gap-1">
              <span className="rounded-full border border-current/20 px-2 py-0.5 font-mono text-xs">{marketMeta.shortLabel}</span>
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
                placeholder={market === 'KR' ? '例如：三星电子 2025 年年报和三季度报告' : market === 'JP' ? '例如：铠侠 2025 年有价证券报告书' : '例如：比亚迪 2025 年年报'}
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
              <div className={`smart-search-source ${activeMarketMissingConfig.length ? 'is-warning' : 'is-ready'}`}>
                <AlertTriangle className="h-4 w-4" />
                <span>
                  {activeMarketSearchBlocked
                    ? `${marketConfig.label}增强官方源需配置 ${activeMarketMissingConfig.join('、')}；当前仍可使用官方 fallback。`
                    : activeMarketMissingConfig.length
                      ? `${marketConfig.label}部分官方源缺少 ${activeMarketMissingConfig.join('、')}；将继续使用可用的免费官方源查询，法定报告全量可能不完整。`
                    : marketHealthLoading
                      ? '正在检查官方披露源配置...'
                      : showMarketSourceReady
                        ? `${marketConfig.label}官方披露源已就绪，可查询下载列表。`
                        : `正在等待${marketConfig.label}官方披露源状态。`}
                </span>
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
      {renderTable(annualReports, annualTitle, <FileText className="h-4 w-4 text-primary" />)}
      {renderTable(financialReports, financialTitle, <FileText className="h-4 w-4 text-primary" />)}

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

      <div className="apple-card rounded-[var(--radius-panel)] p-4 sm:p-6">
        <div className="mb-5 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-lg font-semibold text-text sm:text-xl">
              <FolderOpen className="h-5 w-5 text-primary" />
              已下载财报文件
            </h2>
            <p className="mt-1 text-sm leading-6 text-text-muted sm:text-base">来自本地 downloads 目录，PDF 可进入解析；HTML/iXBRL 可在浏览器新标签中打开查看。</p>
          </div>
          <div className="grid w-full gap-2 sm:grid-cols-[auto_minmax(240px,320px)_auto] sm:items-center lg:w-auto">
            <label htmlFor="downloaded-report-query" className="text-sm font-semibold leading-5 text-text-muted sm:whitespace-nowrap">
              搜索公司或文件名
            </label>
            <div className="relative min-w-0">
              <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
              <input
                id="downloaded-report-query"
                type="search"
                value={downloadedQuery}
                onChange={(e) => setDownloadedQueryAndUrl(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && loadDownloadedReports(downloadedQuery)}
                placeholder="输入公司或文件名"
                className="form-control h-10 min-h-10 w-full rounded-xl py-0 pl-10 pr-3 text-sm"
              />
            </div>
            <button
              onClick={() => loadDownloadedReports(downloadedQuery)}
              disabled={downloadedLoading}
              className="inline-flex h-10 min-w-[96px] shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text shadow-sm hover:bg-bg disabled:opacity-60"
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
          <EmptyState
            icon={FolderOpen}
            title="暂无已下载财报文件"
            description="完成上方下载后，这里会自动汇总本地文件。"
            className="border-dashed"
          />
        ) : (
          <div className="divide-y divide-border overflow-hidden rounded-2xl border border-border">
            {visibleDownloadedReports.map((report) => {
              const isPdf = report.isPdf !== false
              const actionGridColumns = confirmDeletePath === report.relativePath ? 'repeat(2, minmax(0, 1fr))' : 'repeat(3, minmax(0, 1fr))'
              return (
              <div
                key={report.id}
                className="content-auto group flex flex-col gap-3 bg-card px-4 py-4 transition-colors hover:bg-primary/[0.035] sm:flex-row sm:items-center sm:gap-4 sm:px-5"
              >
                <button
                  type="button"
                  onClick={() => openDownloadedReport(report)}
                  className="flex min-w-0 flex-1 items-start gap-3 text-left sm:items-center sm:gap-4"
                >
                  <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                    <FileText className="h-5 w-5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block break-words text-sm font-semibold leading-6 text-text sm:truncate sm:text-base">{report.filename}</span>
                    <span className="mt-1 block break-words text-xs leading-5 text-text-muted sm:truncate sm:text-sm">{report.company} · {report.category} · {report.relativePath}</span>
                    {!isPdf ? <span className="mt-1 block text-xs font-semibold text-warning">非 PDF 文件：{report.contentType || 'HTML/iXBRL'}</span> : null}
                  </span>
                </button>
                <span className="hidden shrink-0 text-right text-sm text-text-muted md:block">
                  <span className="block font-mono">{formatBytes(report.size)}</span>
                  <span className="mt-1 block">{formatDateTime(report.mtime)}</span>
                </span>
                <div
                  className="grid gap-2 sm:flex sm:shrink-0 sm:items-center"
                  style={{ gridTemplateColumns: actionGridColumns }}
                >
                  {isPdf ? (
                    <Link
                      to={parsePathForDownloadedReport(report.relativePath)}
                      className="flex h-10 min-w-0 w-full items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-border px-2.5 text-sm font-semibold text-text-muted transition-colors hover:bg-primary/10 hover:text-primary sm:w-10 sm:border-0 sm:px-0"
                      aria-label="解析 PDF"
                    >
                      <Play className="h-5 w-5" />
                      <span className="sm:hidden">解析</span>
                    </Link>
                  ) : (
                    <button
                      type="button"
                      className="flex h-10 min-w-0 w-full cursor-not-allowed items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-border px-2.5 text-sm font-semibold text-text-muted opacity-45 sm:w-10 sm:border-0 sm:px-0"
                      disabled
                      title="该文件不是 PDF，暂不能送入 PDF 解析器"
                      aria-label="非 PDF 暂不能解析"
                    >
                      <Play className="h-5 w-5" />
                      <span className="sm:hidden">解析</span>
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => openDownloadedReport(report)}
                    className="flex h-10 min-w-0 w-full items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-border px-2.5 text-sm font-semibold text-text-muted transition-colors hover:bg-primary/10 hover:text-primary sm:w-10 sm:border-0 sm:px-0"
                    aria-label="打开文件"
                  >
                    <ExternalLink className="h-5 w-5" />
                    <span className="sm:hidden">打开</span>
                  </button>
                  {confirmDeletePath === report.relativePath ? (
                    <>
                    <button
                      type="button"
                      onClick={() => deleteDownloadedReport(report)}
                      disabled={deletingPath === report.relativePath}
                      className="inline-flex h-10 w-full min-w-0 items-center justify-center whitespace-nowrap rounded-xl bg-error px-2.5 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-60"
                    >
                      {deletingPath === report.relativePath ? <Loader2 className="h-4 w-4 animate-spin" /> : '确认'}
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmDeletePath('')}
                      disabled={Boolean(deletingPath)}
                      className="inline-flex h-10 w-full min-w-0 items-center justify-center whitespace-nowrap rounded-xl border border-border bg-card px-2.5 text-sm font-semibold text-text hover:bg-bg disabled:opacity-60"
                    >
                      取消
                    </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirmDeletePath(report.relativePath)}
                      className="flex h-10 min-w-0 w-full items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-border px-2.5 text-sm font-semibold text-text-muted transition-colors hover:bg-error/10 hover:text-error sm:w-10 sm:border-0 sm:px-0"
                      aria-label="删除 PDF"
                    >
                      <Trash2 className="h-5 w-5" />
                      <span className="sm:hidden">删除</span>
                    </button>
                  )}
                </div>
              </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
