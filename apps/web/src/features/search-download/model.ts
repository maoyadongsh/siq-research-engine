import {
  DISCLOSURE_MARKETS,
  isDisclosureMarketCode,
  type DisclosureMarketCode,
} from '../../lib/marketMetadata'

export interface ReportItem {
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

export interface DownloadFileResult {
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

export interface DownloadedPdf {
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

export type MarketCode = DisclosureMarketCode

export interface AssistIntent {
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

export interface CandidateExplanation {
  document_url: string
  title_zh: string
  report_type_zh: string
  period_zh: string
  recommendation: string
  recommended?: boolean
  warnings?: string[]
}

export interface AssistResult {
  intent: AssistIntent
  candidate_explanations?: CandidateExplanation[]
  assistant_mode?: string
}

export interface MarketSourceStatus {
  official_source?: string
  report_search_ready?: boolean
  required_config?: string[]
  message?: string
}

export interface MarketReportHealth {
  report_finder?: {
    status?: string
    markets?: Partial<Record<MarketCode, MarketSourceStatus>>
  }
}

export interface MarketConfig {
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

export const MARKET_CONFIGS: Record<MarketCode, MarketConfig> = {
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

export const typeLabels: Record<string, string> = {
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

export const typeStyles: Record<string, string> = {
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

export const marketSourceConfigLabels: Partial<Record<MarketCode, string>> = {
  JP: 'EDINET_API_KEY',
  KR: 'DART_API_KEY',
}

export function isMarketCode(value: string | null): value is MarketCode {
  return isDisclosureMarketCode(value)
}

export function isRemoteConfigError(message: string) {
  return /EDINET_API_KEY|DART_API_KEY/.test(message)
}

export function friendlyRemoteConfigError(message: string) {
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

export function identifierPayload(market: MarketCode, query: string, resolvedTicker?: string) {
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

export function reportTypeLabel(report: ReportItem | DownloadFileResult) {
  return typeLabels[report.report_type] || report.report_type || '报告'
}

export function explanationMap(items: CandidateExplanation[]) {
  return new Map(items.map((item) => [item.document_url, item]))
}

export function parsePathForDownloadedReport(relativePath: string) {
  if (relativePath.startsWith('HK/')) return '/parse-hk'
  if (relativePath.startsWith('US/')) return '/parse?market=US'
  if (relativePath.startsWith('EU/')) return '/parse-eu'
  if (relativePath.startsWith('JP/')) return '/parse-jp'
  if (relativePath.startsWith('KR/')) return '/parse-kr'
  return '/parse'
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

export function uniqueBy<T>(items: T[], getKey: (item: T) => string) {
  const seen = new Set<string>()
  return items.filter((item) => {
    const key = getKey(item)
    if (!key || seen.has(key)) return false
    seen.add(key)
    return true
  })
}
