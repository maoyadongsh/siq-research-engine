import { reportTableTitlesForMarket, marketSourceDisplay } from './display'
import { getSearchDownloadVisibleLogs, hasSearchDownloadProblemLogs, type SearchDownloadLogEntry } from './logs'
import {
  explanationMap,
  MARKET_CONFIGS,
  type CandidateExplanation,
  type DownloadFileResult,
  type DownloadedPdf,
  type MarketCode,
  type MarketReportHealth,
  type MarketSourceStatus,
  type ReportItem,
} from './model'

export interface SearchDownloadViewModelInput {
  market: MarketCode
  marketHealth: MarketReportHealth | null
  marketHealthLoading: boolean
  annualReports: ReportItem[]
  financialReports: ReportItem[]
  selected: Set<string>
  downloadResults: DownloadFileResult[]
  logs: SearchDownloadLogEntry[]
  visibleLogs?: SearchDownloadLogEntry[]
  downloadedReports: DownloadedPdf[]
  candidateExplanations: CandidateExplanation[]
  currentYear?: number
}

export interface SearchDownloadViewModel {
  marketConfig: (typeof MARKET_CONFIGS)[MarketCode]
  quickDownloadOptions: (typeof MARKET_CONFIGS)[MarketCode]['quickOptions']
  annualTitle: string
  financialTitle: string
  years: string[]
  activeMarketSource: MarketSourceStatus | undefined
  activeMarketSourceDisplay: ReturnType<typeof marketSourceDisplay>
  visibleDownloadResults: DownloadFileResult[]
  visibleLogs: SearchDownloadLogEntry[]
  visibleDownloadedReports: DownloadedPdf[]
  candidateExplanationMap: Map<string, CandidateExplanation>
  hasProblemLogs: boolean
  totalCandidates: number
  hasReports: boolean
  selectedCount: number
}

export function buildSearchDownloadYears(currentYear: number, count = 10) {
  return Array.from({ length: count }, (_, index) => String(currentYear - index))
}

export function buildSearchDownloadViewModel({
  market,
  marketHealth,
  marketHealthLoading,
  annualReports,
  financialReports,
  selected,
  downloadResults,
  logs,
  visibleLogs,
  downloadedReports,
  candidateExplanations,
  currentYear = new Date().getFullYear(),
}: SearchDownloadViewModelInput): SearchDownloadViewModel {
  const marketConfig = MARKET_CONFIGS[market]
  const activeMarketSource = marketHealth?.report_finder?.markets?.[market]
  const { annualTitle, financialTitle } = reportTableTitlesForMarket(market)
  const totalCandidates = annualReports.length + financialReports.length

  return {
    marketConfig,
    quickDownloadOptions: marketConfig.quickOptions,
    annualTitle,
    financialTitle,
    years: buildSearchDownloadYears(currentYear),
    activeMarketSource,
    activeMarketSourceDisplay: marketSourceDisplay({
      market,
      source: activeMarketSource,
      loading: marketHealthLoading,
    }),
    visibleDownloadResults: downloadResults,
    visibleLogs: getSearchDownloadVisibleLogs(visibleLogs || logs),
    visibleDownloadedReports: downloadedReports,
    candidateExplanationMap: explanationMap(candidateExplanations),
    hasProblemLogs: hasSearchDownloadProblemLogs(logs),
    totalCandidates,
    hasReports: totalCandidates > 0,
    selectedCount: selected.size,
  }
}
