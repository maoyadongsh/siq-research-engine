import type { DownloadedPdf } from '../../lib/pdfTypes'
import type { UsSecCaseSetItem, UsSecCaseSetStatus } from './api'

export type UsSecParseStatus = 'unparsed' | 'building' | 'package_ready' | 'postgres_ready' | 'warning' | 'failed'

export interface UsSecDownloadedRow {
  id: string
  relativePath: string
  filename: string
  companyName: string
  ticker: string
  form: string
  periodEnd: string
  filingDate: string
  fileType: string
  sizeBytes: number
  downloadedAt: string
  parseStatus: UsSecParseStatus
  packagePath: string
  report: DownloadedPdf
}

function normalized(value: unknown): string {
  return String(value || '').trim()
}

function upper(value: unknown): string {
  return normalized(value).toUpperCase()
}

function pathIncludesAccession(packagePath: string | undefined, accession: string): boolean {
  if (!packagePath || !accession) return false
  return packagePath.toLowerCase().includes(accession.toLowerCase())
}

export function usSecDocumentKind(report: DownloadedPdf): string {
  const filename = normalized(report.filename).toLowerCase()
  const suffix = filename.split('.').pop() || ''
  const contentType = normalized(report.contentType).toLowerCase()
  if (report.isPdf === true || suffix === 'pdf' || contentType.includes('pdf')) return 'PDF'
  if (suffix === 'zip' || contentType.includes('zip')) return 'ZIP'
  if (suffix === 'xhtml' || suffix === 'xbrl' || contentType.includes('xhtml')) return 'iXBRL'
  if (suffix === 'htm' || suffix === 'html' || contentType.includes('html')) return 'HTML'
  if (suffix === 'xml' || contentType.includes('xml')) return 'XML'
  return suffix ? suffix.toUpperCase() : '文件'
}

export function findUsSecCaseItem(
  report: DownloadedPdf,
  status: UsSecCaseSetStatus | null | undefined,
): UsSecCaseSetItem | null {
  const items = status?.items || []
  if (!items.length) return null
  const ticker = upper(report.ticker)
  const accession = normalized(report.accessionNumber)
  const periodEnd = normalized(report.reportEnd)
  const exact = items.find((item) =>
    (!ticker || upper(item.ticker) === ticker)
    && (!accession || pathIncludesAccession(item.package_path, accession))
    && (!periodEnd || normalized(item.period_end) === periodEnd)
  )
  if (exact) return exact
  const tickerMatch = ticker ? items.find((item) => upper(item.ticker) === ticker) : null
  if (tickerMatch) return tickerMatch
  const company = normalized(report.companyName || report.company).toLowerCase()
  return company
    ? items.find((item) => normalized(item.company_name).toLowerCase() === company) || null
    : null
}

export function deriveUsSecParseStatus({
  report,
  item,
  status,
  busyPath = '',
}: {
  report: DownloadedPdf
  item?: UsSecCaseSetItem | null
  status?: UsSecCaseSetStatus | null
  busyPath?: string
}): UsSecParseStatus {
  if (busyPath && busyPath === report.relativePath) return 'building'
  if (!item?.package_path) return 'unparsed'
  const quality = normalized(item.quality_status).toLowerCase()
  if (quality === 'fail' || quality === 'failed') return 'failed'
  if (quality === 'warning' || quality === 'warn') return 'warning'
  const importedPackages = Number(status?.ingest_report?.package_count || 0)
  const importedFacts = Number(status?.ingest_report?.summary?.xbrl_facts || 0)
  if (importedPackages > 0 && importedFacts > 0) return 'postgres_ready'
  return 'package_ready'
}

export function deriveUsSecDownloadedRows(
  reports: DownloadedPdf[],
  status?: UsSecCaseSetStatus | null,
  busyPath = '',
): UsSecDownloadedRow[] {
  return reports.map((report) => {
    const item = findUsSecCaseItem(report, status)
    return {
      id: report.id,
      relativePath: report.relativePath,
      filename: report.filename,
      companyName: normalized(report.companyName || report.company) || '未知公司',
      ticker: upper(report.ticker),
      form: normalized(report.form || report.reportType || report.category),
      periodEnd: normalized(report.reportEnd || item?.period_end),
      filingDate: normalized(report.publishedAt || item?.filing_date),
      fileType: usSecDocumentKind(report),
      sizeBytes: Number(report.size || report.downloadedFile?.size_bytes || 0),
      downloadedAt: normalized(report.mtime),
      parseStatus: deriveUsSecParseStatus({ report, item, status, busyPath }),
      packagePath: normalized(item?.package_path),
      report,
    }
  })
}
