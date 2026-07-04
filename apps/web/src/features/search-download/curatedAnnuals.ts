import {
  MARKET_CONFIGS,
  uniqueBy,
  type MarketCode,
  type ReportItem,
} from './model'
import type { SearchDownloadCompanyInfo } from './flows'

export interface CuratedAnnualsRequestPlan {
  params: URLSearchParams
  loadingLog: string
}

export type CuratedAnnualsLoadOptions =
  | number
  | { mode?: 'default'; limit?: number }
  | { mode: 'country'; country: string; limit?: number }
  | { mode: 'all-eu'; limit?: number }

export interface CuratedAnnualsApplyResult {
  reports: ReportItem[]
  selected: Set<string>
  companyInfo: SearchDownloadCompanyInfo
  successLog: string
}

export function canLoadCuratedAnnuals(market: MarketCode) {
  return market === 'JP' || market === 'KR' || market === 'EU'
}

export function buildCuratedAnnualsRequestPlan(
  market: MarketCode,
  year: string,
  options: CuratedAnnualsLoadOptions = 10,
): CuratedAnnualsRequestPlan {
  const plan = normalizeCuratedAnnualsLoadOptions(market, options)
  const marketLabel = market === 'EU' ? '欧股' : MARKET_CONFIGS[market].label
  const params = new URLSearchParams({ market, report_year: year, limit: String(plan.limit) })
  if (plan.country) {
    params.set('country', plan.country)
  }
  return {
    params,
    loadingLog: `正在载入 ${marketLabel} ${plan.sampleLabel} ${plan.limit} 家年报样本 (${year})`,
  }
}

function normalizeCuratedAnnualsLoadOptions(
  market: MarketCode,
  options: CuratedAnnualsLoadOptions,
): { limit: number; sampleLabel: string; country?: string } {
  if (typeof options === 'number') {
    return { limit: options, sampleLabel: '主流' }
  }

  if (market === 'EU' && options.mode === 'all-eu') {
    return { limit: options.limit ?? 50, sampleLabel: '五国' }
  }

  if (market === 'EU' && options.mode === 'country') {
    return { limit: options.limit ?? 10, sampleLabel: '当前国家', country: options.country }
  }

  return { limit: options.limit ?? 10, sampleLabel: '主流' }
}

export function buildCuratedAnnualsApplyResult(
  market: MarketCode,
  reports: ReportItem[],
): CuratedAnnualsApplyResult {
  const marketLabel = MARKET_CONFIGS[market].label
  const dedupedReports = uniqueBy(reports, (report) => report.document_url)
  return {
    reports: dedupedReports,
    selected: new Set(dedupedReports.map((report) => report.document_url)),
    companyInfo: { name: `${marketLabel}主流公司年报样本`, ticker: '', curated: true },
    successLog: `已载入 ${dedupedReports.length} 家${marketLabel}主流公司年报，并自动勾选`,
  }
}
