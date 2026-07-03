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

export interface CuratedAnnualsApplyResult {
  reports: ReportItem[]
  selected: Set<string>
  companyInfo: SearchDownloadCompanyInfo
  successLog: string
}

export function canLoadCuratedAnnuals(market: MarketCode) {
  return market === 'JP' || market === 'KR'
}

export function buildCuratedAnnualsRequestPlan(
  market: MarketCode,
  year: string,
  limit = 10,
): CuratedAnnualsRequestPlan {
  const marketLabel = MARKET_CONFIGS[market].label
  const params = new URLSearchParams({ market, report_year: year, limit: String(limit) })
  return {
    params,
    loadingLog: `正在载入 ${marketLabel} 主流 ${limit} 家年报样本 (${year})`,
  }
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
