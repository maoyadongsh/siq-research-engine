import {
  DISCLOSURE_MARKET_ORDER,
  isDisclosureMarketCode,
  type DisclosureMarketCode,
} from '@/lib/marketMetadata'
import type {
  GeneratedArtifactOption,
  ResearchCompanyOption,
  ResearchMarketOption,
  SourceReportOption,
} from './types'

export interface ResearchSelection {
  market: DisclosureMarketCode | ''
  companyKey: string
  reportId: string
  artifactId: string
}

export type ResearchSelectionAction =
  | { type: 'select-market'; market: DisclosureMarketCode | '' }
  | { type: 'select-company'; companyKey: string }
  | { type: 'select-report'; reportId: string }
  | { type: 'select-artifact'; artifactId: string }
  | { type: 'reset' }

export const EMPTY_RESEARCH_SELECTION: ResearchSelection = {
  market: '',
  companyKey: '',
  reportId: '',
  artifactId: '',
}

export const SECONDARY_MARKET_DEFAULT = {
  market: 'CN' as const,
  companyCode: '600104',
  companyWikiId: '600104-上汽集团',
}

export function researchSelectionReducer(
  state: ResearchSelection,
  action: ResearchSelectionAction,
): ResearchSelection {
  if (action.type === 'reset') return EMPTY_RESEARCH_SELECTION
  if (action.type === 'select-market') {
    if (state.market === action.market) return state
    return { market: action.market, companyKey: '', reportId: '', artifactId: '' }
  }
  if (action.type === 'select-company') {
    if (state.companyKey === action.companyKey) return state
    return { ...state, companyKey: action.companyKey, reportId: '', artifactId: '' }
  }
  if (action.type === 'select-report') {
    if (state.reportId === action.reportId) return state
    return { ...state, reportId: action.reportId, artifactId: '' }
  }
  if (state.artifactId === action.artifactId) return state
  return { ...state, artifactId: action.artifactId }
}

export interface RequestedResearchSelection {
  market?: DisclosureMarketCode
  companyKey: string
  reportId: string
  artifactId: string
  legacyCompany: string
  legacyResult: string
}

export function readRequestedResearchSelection(searchParams: URLSearchParams): RequestedResearchSelection {
  const rawMarket = searchParams.get('market')?.toUpperCase()
  return {
    market: isDisclosureMarketCode(rawMarket) ? rawMarket : undefined,
    companyKey: searchParams.get('company_key')?.trim() || '',
    reportId: searchParams.get('report_id')?.trim() || '',
    artifactId: searchParams.get('artifact_id')?.trim() || '',
    legacyCompany: searchParams.get('company')?.trim() || '',
    legacyResult: searchParams.get('result')?.trim() || '',
  }
}

export function orderedResearchMarkets(markets: ResearchMarketOption[]) {
  const order = new Map(DISCLOSURE_MARKET_ORDER.map((market, index) => [market, index]))
  return markets.toSorted((left, right) => (
    (order.get(left.market) ?? Number.MAX_SAFE_INTEGER) - (order.get(right.market) ?? Number.MAX_SAFE_INTEGER)
  ))
}

export function resolveInitialMarket(
  markets: ResearchMarketOption[],
  requested: RequestedResearchSelection,
): DisclosureMarketCode | '' {
  const ordered = orderedResearchMarkets(markets)
  const requestedMarket = requested.market || (requested.legacyCompany || requested.legacyResult ? 'CN' : undefined)
  const requestedOption = requestedMarket && ordered.find((option) => option.market === requestedMarket && option.enabled)
  const defaultOption = ordered.find((option) => (
    option.market === SECONDARY_MARKET_DEFAULT.market && option.enabled
  ))
  return requestedOption?.market || defaultOption?.market || ordered.find((option) => option.enabled)?.market || ordered[0]?.market || ''
}

export function resolveInitialCompany(
  companies: ResearchCompanyOption[],
  requested: RequestedResearchSelection,
  market: DisclosureMarketCode,
) {
  const requestedCompany = companies.find((company) => company.company_key === requested.companyKey)
  if (requestedCompany) return requestedCompany.company_key
  if (market === 'CN' && requested.legacyCompany) {
    const legacyCompany = companies.find((company) => company.company_wiki_id === requested.legacyCompany)
    if (legacyCompany) return legacyCompany.company_key
  }
  if (market === SECONDARY_MARKET_DEFAULT.market) {
    const defaultCompany = companies.find((company) => (
      company.display_code === SECONDARY_MARKET_DEFAULT.companyCode
      || company.company_wiki_id === SECONDARY_MARKET_DEFAULT.companyWikiId
    ))
    if (defaultCompany) return defaultCompany.company_key
  }
  return companies[0]?.company_key || ''
}

export function resolveInitialReport(reports: SourceReportOption[], requestedReportId: string) {
  return reports.find((report) => report.report_id === requestedReportId)?.report_id || reports[0]?.report_id || ''
}

export function resolveInitialArtifact(
  artifacts: GeneratedArtifactOption[],
  requested: RequestedResearchSelection,
) {
  const exact = artifacts.find((artifact) => artifact.artifact_id === requested.artifactId)
  if (exact) return exact.artifact_id
  if (requested.legacyResult) {
    const legacy = artifacts.find((artifact) => artifact.filename === requested.legacyResult)
    if (legacy) return legacy.artifact_id
  }
  return artifacts[0]?.artifact_id || ''
}

export function applyResearchSelectionToSearchParams(
  current: URLSearchParams,
  selection: ResearchSelection,
) {
  const next = new URLSearchParams(current)
  next.delete('company')
  next.delete('result')
  const fields: Array<[string, string]> = [
    ['market', selection.market],
    ['company_key', selection.companyKey],
    ['report_id', selection.reportId],
    ['artifact_id', selection.artifactId],
  ]
  for (const [key, value] of fields) {
    if (value) next.set(key, value)
    else next.delete(key)
  }
  return next
}

export function buildResearchShareUrl(origin: string, pathname: string, selection: ResearchSelection) {
  const params = applyResearchSelectionToSearchParams(new URLSearchParams(), selection)
  const query = params.toString()
  return `${origin}${pathname}${query ? `?${query}` : ''}`
}
