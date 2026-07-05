import {
  batchDownloadReports,
  downloadReport,
  fetchRecentReports,
  linkWorkspaceDownload,
  resolveCompany,
  selectDownloadReports,
} from './api'
import {
  identifierPayload,
  uniqueBy,
  type DownloadFileResult,
  type MarketCode,
  type ReportItem,
} from './model'

export type ReportSearchSource = 'manual' | 'smart'

export interface SearchDownloadCompanyInfo {
  name: string
  ticker: string
  curated?: boolean
}

export interface ReportSearchParamsUpdate {
  market: MarketCode
  q: string
  year: string
  exchange: string
  form: string
  country: string
}

export interface ResolveSearchCompanyInput {
  targetMarket: MarketCode
  targetQuery: string
  targetTicker?: string
  targetCompanyId?: string
  targetFilter?: string
}

export interface FetchReportCandidatesInput {
  targetMarket: MarketCode
  targetQuery: string
  targetYear: string
  targetTicker?: string
  targetCompanyId?: string
  targetFilter?: string
  companyName: string
  ticker: string
}

export interface FetchReportCandidatesResult {
  annualReports: ReportItem[]
  financialReports: ReportItem[]
  candidateReports: ReportItem[]
}

export interface BatchDownloadReportsInput {
  market: MarketCode
  companyInfo: SearchDownloadCompanyInfo
  marketFilter: string
  reports: ReportItem[]
}

export interface BatchDownloadReportsResult {
  results: DownloadFileResult[]
  succeeded: number
  failed: number
}

export interface IndividualDownloadOutcome {
  report: ReportItem
  success: boolean
}

export interface QuickDownloadReportsInput {
  market: MarketCode
  companyInfo: SearchDownloadCompanyInfo
  marketFilter: string
  reportTypes: string[]
  year: string
}

export interface QuickDownloadReportsResult {
  files: DownloadFileResult[]
  companyName: string
  succeeded: number
  total: number
}

interface ReportListResponse {
  reports?: ReportItem[]
  items?: ReportItem[]
}

interface BatchDownloadResponse {
  results?: DownloadFileResult[]
  files?: DownloadFileResult[]
  succeeded?: number
  failed?: number
}

interface QuickDownloadResponse {
  files?: DownloadFileResult[]
  company_name?: string
  succeeded?: number
  total?: number
}

export function searchFilterForReportSearch({
  targetMarket,
  source,
  marketFilter,
  targetCountry,
}: {
  targetMarket: MarketCode
  source: ReportSearchSource
  marketFilter: string
  targetCountry?: string
}) {
  if (targetMarket === 'EU') {
    return targetCountry ?? (source === 'manual' ? marketFilter : '')
  }
  return source === 'manual' ? marketFilter : ''
}

export function searchParamsForReportSearch({
  targetMarket,
  targetQuery,
  targetYear,
  targetFilter,
}: {
  targetMarket: MarketCode
  targetQuery: string
  targetYear: string
  targetFilter: string
}): ReportSearchParamsUpdate {
  return {
    market: targetMarket,
    q: targetQuery,
    year: targetYear,
    exchange: targetMarket === 'CN' ? targetFilter : '',
    form: targetMarket === 'US' ? targetFilter : '',
    country: targetMarket === 'EU' ? targetFilter : '',
  }
}

export function annualFormsForMarket(targetMarket: MarketCode, targetFilter = '') {
  if (targetMarket === 'JP') return ['yuho']
  if (targetMarket !== 'US') return []
  if (targetFilter && ['10-K', '20-F'].includes(targetFilter)) return [targetFilter]
  if (targetFilter) return []
  return ['10-K', '20-F']
}

export function financialFormsForMarket(targetMarket: MarketCode, targetFilter = '') {
  if (targetMarket !== 'US') return []
  if (targetFilter) return [targetFilter]
  return ['10-Q', '6-K']
}

export function fileFormatFromDocumentUrl(url: string, market: MarketCode) {
  const clean = url.toLowerCase().split(/[?#]/, 1)[0]
  if (clean.includes('dart.fss.or.kr/pdf/download/pdf.do')) return 'pdf'
  if (clean.includes('dart.fss.or.kr/dsaf001/') || clean.includes('kind.krx.co.kr/')) return 'html'
  const suffix = clean.match(/\.([a-z0-9]+)$/)?.[1]
  const openable = new Set(['pdf', 'html', 'htm', 'xml', 'txt', 'zip'])
  if (!suffix || !openable.has(suffix)) {
    if (market === 'US') return 'htm'
    if (market === 'KR') return 'html'
    if (market === 'EU') return 'zip'
    return 'pdf'
  }
  return suffix
}

export function identifierPayloadForSearch({
  targetMarket,
  targetQuery,
  targetTicker,
  targetCompanyId,
  targetFilter,
}: ResolveSearchCompanyInput) {
  if (targetMarket !== 'EU' && targetTicker && targetCompanyId) {
    return { ticker: targetTicker, company_id: targetCompanyId }
  }
  if (targetMarket !== 'EU' && targetTicker) {
    return { ticker: targetTicker }
  }
  if (targetMarket !== 'EU' && targetCompanyId) {
    return { company_id: targetCompanyId }
  }
  const base = identifierPayload(targetMarket, targetQuery, targetTicker)
  const countryPrefix = targetMarket === 'EU' && targetFilter ? `${targetFilter}:` : ''
  if (targetCompanyId) {
    const prefixedCompanyId = targetMarket === 'EU' && targetFilter && !String(targetCompanyId).includes(':')
      ? `${countryPrefix}${targetCompanyId}`
      : targetCompanyId
    if (targetTicker) return { ticker: targetTicker, company_id: prefixedCompanyId }
    return { ...base, company_id: prefixedCompanyId }
  }
  if (targetMarket === 'EU' && targetFilter) {
    if ('ticker' in base && typeof base.ticker === 'string') return { company_id: `${countryPrefix}${base.ticker}`, ticker: base.ticker }
    if ('company_id' in base && typeof base.company_id === 'string') return { company_id: `${countryPrefix}${base.company_id}` }
    return { ...base, company_id: `${countryPrefix}${targetQuery.trim()}` }
  }
  return base
}

export function resolveCompanyDisplayName(resolved: Record<string, unknown>, fallbackQuery: string) {
  const nested = resolved.resolved
  const resolvedCompany = nested && typeof nested === 'object'
    ? nested as Record<string, unknown>
    : resolved
  const companyName = String(
    resolvedCompany.company_name
    || resolvedCompany.canonical_name
    || resolvedCompany.display_name
    || resolved.company_name
    || resolved.name
    || fallbackQuery,
  )
  const ticker = String(resolvedCompany.ticker || resolved.ticker || resolved.code || '')
  return { companyName, ticker }
}

export async function resolveSearchCompany(input: ResolveSearchCompanyInput): Promise<SearchDownloadCompanyInfo> {
  const resolved = await resolveCompany<Record<string, unknown>>({
    market: input.targetMarket,
    ...identifierPayloadForSearch(input),
  })
  const { companyName, ticker } = resolveCompanyDisplayName(resolved, input.targetQuery)
  return { name: companyName, ticker }
}

export async function fetchReportCandidates({
  targetMarket,
  targetQuery,
  targetYear,
  targetTicker,
  targetCompanyId,
  targetFilter = '',
  companyName,
  ticker,
}: FetchReportCandidatesInput): Promise<FetchReportCandidatesResult> {
  const annualForms = annualFormsForMarket(targetMarket, targetFilter)
  const financialForms = financialFormsForMarket(targetMarket, targetFilter)
  const baseReportPayload = {
    market: targetMarket,
    company_name: companyName,
    ticker: ticker || targetTicker || undefined,
    company_id: targetMarket === 'EU' && targetFilter
      ? `${targetFilter}:${targetCompanyId || ticker || targetQuery}`
      : targetCompanyId || undefined,
    exchange_hint: targetMarket === 'CN' ? targetFilter || undefined : undefined,
  }

  let annualReports: ReportItem[] = []
  if (targetMarket !== 'US' || annualForms.length > 0) {
    const annualData = await fetchRecentReports<ReportListResponse | ReportItem[]>({
      ...baseReportPayload,
      target: 'annual_report',
      report_year: parseInt(targetYear, 10),
      forms: annualForms,
      limit: 10,
    })
    annualReports = normalizeReportList(annualData)
  }

  const financialData = await fetchRecentReports<ReportListResponse | ReportItem[]>({
    ...baseReportPayload,
    target: 'financial_report',
    report_year: parseInt(targetYear, 10),
    forms: financialForms,
    limit: 20,
  })
  const financialReports = normalizeReportList(financialData)

  return {
    annualReports,
    financialReports,
    candidateReports: uniqueBy([...annualReports, ...financialReports], (report) => report.document_url),
  }
}

export function selectedReportsForDownload(reports: ReportItem[], selected: Set<string>) {
  return uniqueBy(
    reports.filter((report) => selected.has(report.document_url)),
    (report) => report.document_url,
  )
}

export async function batchDownloadSelectedReports({
  market,
  companyInfo,
  marketFilter,
  reports,
}: BatchDownloadReportsInput): Promise<BatchDownloadReportsResult> {
  const items = reports.map((report) => downloadItemMarketPayload(report, {
    market,
    companyInfo,
    marketFilter,
  }))
  const data = await batchDownloadReports<BatchDownloadResponse>({
    market,
    default_company_name: companyInfo.name,
    items,
  })
  const results = uniqueBy(data.results || data.files || [], (item) => item.document_url || item.file_name)
  await linkDownloadedFilesToWorkspace(results)
  return {
    results,
    succeeded: data.succeeded || 0,
    failed: data.failed || 0,
  }
}

export async function downloadReportsIndividually({
  market,
  companyInfo,
  reports,
}: {
  market: MarketCode
  companyInfo: SearchDownloadCompanyInfo
  reports: ReportItem[]
}): Promise<IndividualDownloadOutcome[]> {
  const outcomes: IndividualDownloadOutcome[] = []
  for (const report of reports) {
    try {
      await downloadReport({
        market,
        company_name: companyInfo.name,
        ticker: companyInfo.ticker || undefined,
        document_url: report.document_url,
        title: report.title,
      })
      outcomes.push({ report, success: true })
    } catch {
      outcomes.push({ report, success: false })
    }
  }
  return outcomes
}

export async function quickDownloadReportTypes({
  market,
  companyInfo,
  marketFilter,
  reportTypes,
  year,
}: QuickDownloadReportsInput): Promise<QuickDownloadReportsResult> {
  const data = await selectDownloadReports<QuickDownloadResponse>({
    market,
    company_name: companyInfo.name,
    ticker: companyInfo.ticker || undefined,
    company_id: market === 'EU' && marketFilter && companyInfo.ticker ? `${marketFilter}:${companyInfo.ticker}` : undefined,
    report_types: reportTypes,
    report_year: parseInt(year, 10),
  })
  const files = data.files || []
  await linkDownloadedFilesToWorkspace(files)
  return {
    files,
    companyName: data.company_name || companyInfo.name,
    succeeded: data.succeeded || 0,
    total: data.total || files.length,
  }
}

function normalizeReportList(data: ReportListResponse | ReportItem[]) {
  return Array.isArray(data) ? data : data.reports || data.items || []
}

function downloadItemMarketPayload(
  report: ReportItem,
  {
    market,
    companyInfo,
    marketFilter,
  }: {
    market: MarketCode
    companyInfo: SearchDownloadCompanyInfo
    marketFilter: string
  },
) {
  return {
    market,
    document_url: report.document_url,
    company_name: report.company_name || companyInfo.name || '',
    title: report.title,
    ticker: report.ticker || companyInfo.ticker || undefined,
    company_id: market === 'EU'
      ? `${String(report.metadata?.country || marketFilter || '').trim()}:${report.company_id || report.ticker || companyInfo.ticker || companyInfo.name || ''}`.replace(/^:/, '')
      : report.company_id,
    report_type: report.report_type,
    report_end: report.report_end,
    published_at: report.published_at,
    landing_url: report.landing_url,
    file_format: report.file_format || fileFormatFromDocumentUrl(report.document_url, market),
  }
}

async function linkDownloadedFilesToWorkspace(items: DownloadFileResult[]) {
  const successful = items.filter((item) => item.success !== false && (item.saved_path || item.file_name))
  await Promise.allSettled(successful.map((item) => linkWorkspaceDownload({
    saved_path: item.saved_path,
    file_name: item.file_name,
    company_name: item.company_name,
    source: item.cache_hit ? 'reused_download_cache' : 'new_download',
  })))
}
