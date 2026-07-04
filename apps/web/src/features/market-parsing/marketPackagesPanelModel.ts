import type { MarketPackageSummary, MarketPackagesResponse } from './api'

export interface MarketPackageRow extends MarketPackageSummary {
  id: string
  title: string
  summary: string
  busy: boolean
}

function textValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function yearValue(item: MarketPackageSummary): string {
  const reportYear = (item as MarketPackageSummary & { report_year?: number | string }).report_year
  return String(reportYear ?? item.fiscal_year ?? '').trim()
}

export function packagePrimaryFile(item: Pick<MarketPackageSummary, 'paths'>): string {
  const paths = item.paths ?? {}
  return (
    paths.report_complete ||
    paths.report_markdown ||
    paths.source_map ||
    paths.quality_report ||
    paths.manifest ||
    'manifest.json'
  )
}

export function deriveMarketPackageRows(payload: MarketPackagesResponse, busyPath = ''): MarketPackageRow[] {
  return (payload.packages ?? []).map((item) => {
    const packagePath = textValue(item.package_path)
    const titleParts = [item.ticker, item.company_name].map(textValue).filter(Boolean)
    const summaryParts = [yearValue(item), item.report_type, item.filing_id].map(textValue).filter(Boolean)
    const fallbackId = [item.market || payload.market || 'market', ...summaryParts, ...titleParts].map(textValue).filter(Boolean).join('-')

    return {
      ...item,
      id: packagePath || item.filing_id || fallbackId || 'market-package',
      title: titleParts.join(' ') || item.filing_id || packagePath || '未命名证据包',
      summary: summaryParts.join(' · '),
      busy: Boolean(packagePath && packagePath === busyPath),
    }
  })
}
