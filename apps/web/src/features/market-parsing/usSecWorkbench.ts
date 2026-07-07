import type { DownloadedPdf } from '../../lib/pdfTypes'
import type { UsSecCaseSetItem, UsSecCaseSetStatus, UsSecPackageDetail } from './api'

export type UsSecParseStatus = 'unparsed' | 'building' | 'package_ready' | 'postgres_ready' | 'warning' | 'failed'
export type UsSecWorkflowStatus = 'ready' | 'pending' | 'unknown' | 'warning'

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

export interface UsSecRecentTaskRow {
  id: string
  packagePath: string
  ticker: string
  companyName: string
  form: string
  fiscalYear: string
  periodEnd: string
  filingDate: string
  status: UsSecParseStatus
  statusText: string
  qualityStatus: string
  sectionCount: number
  tableCount: number
  factCount: number
  metricCount: number
  item: UsSecCaseSetItem
}

export interface UsSecArtifactChip {
  name: string
  ready: boolean
}

export interface UsSecArtifactCheck {
  label: string
  status: 'ready' | 'missing' | 'unknown'
  description: string
}

export interface UsSecArtifactManifestView {
  chips: UsSecArtifactChip[]
  checks: UsSecArtifactCheck[]
  readyCount: number
  total: number
}

export interface UsSecWorkflowStepView {
  label: string
  status: UsSecWorkflowStatus
  description: string
}

export interface UsSecWorkflowSummary {
  steps: UsSecWorkflowStepView[]
  cards: UsSecWorkflowStepView[]
}

export interface UsSecQualitySummary {
  tiles: Array<{ label: string; value: string; description: string }>
  bridgeStatus: string
  bridgeCounts: Record<string, number>
  missingCoreSections: string[]
}

const usSecArtifactNames = [
  'manifest.json',
  'sections/*.md',
  'tables.json',
  'xbrl_facts.json',
  'normalized_metrics.json',
  'evidence_map.json',
  'quality_report.json',
  'bridge_checks.json',
]

const parseStatusText: Record<UsSecParseStatus, string> = {
  unparsed: '未解析',
  building: '解析中',
  package_ready: '证据包已生成',
  postgres_ready: 'PostgreSQL 已入库',
  warning: '质量警告',
  failed: '质量失败',
}

function normalized(value: unknown): string {
  return String(value || '').trim()
}

function upper(value: unknown): string {
  return normalized(value).toUpperCase()
}

function numberValue(value: unknown): number {
  const n = Number(value || 0)
  return Number.isFinite(n) ? n : 0
}

function qualityStatus(value: unknown): string {
  return normalized(value).toLowerCase() || 'unknown'
}

function packageReady(detail: UsSecPackageDetail | null | undefined): boolean {
  return Boolean(detail?.package_path)
}

function countText(value: unknown): string {
  return String(numberValue(value))
}

function formFromPackagePath(packagePath: unknown): string {
  const leaf = upper(normalized(packagePath).split('/').pop() || '')
  const legacyMatch = leaf.match(/^([A-Z0-9-]+)_/)
  if (legacyMatch?.[1]) return legacyMatch[1]
  const companyWikiMatch = leaf.match(/^\d{4}-(.+?)-\d{10}-\d{2}-\d{6}$/)
  if (companyWikiMatch?.[1]) return companyWikiMatch[1]
  const knownForms = ['10-K', '10-Q', '20-F', '40-F', '8-K', '6-K', 'DEF-14A', 'S-1', 'F-1']
  return knownForms.find((form) => leaf.includes(`-${form}-`) || leaf.endsWith(`-${form}`)) || ''
}

function formFromItem(item: UsSecCaseSetItem): string {
  return normalized(item.form || formFromPackagePath(item.package_path) || 'SEC')
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

export function deriveUsSecRecentTasks(status?: UsSecCaseSetStatus | null): UsSecRecentTaskRow[] {
  const items = status?.items || []
  return items
    .filter((item) => normalized(item.package_path))
    .map((item) => {
      const syntheticReport = {
        id: normalized(item.package_path),
        market: 'US',
        company: normalized(item.company_name),
        companyName: normalized(item.company_name),
        ticker: upper(item.ticker),
        category: formFromItem(item),
        filename: normalized(item.package_path).split('/').pop() || normalized(item.package_path),
        relativePath: normalized(item.package_path),
        size: 0,
        mtime: normalized(item.filing_date || item.period_end),
        url: '',
        form: formFromItem(item),
        reportEnd: normalized(item.period_end),
        publishedAt: normalized(item.filing_date),
      } as DownloadedPdf
      const derivedStatus = deriveUsSecParseStatus({ report: syntheticReport, item, status })
      const summary = item.quality_summary || {}
      return {
        id: normalized(item.package_path),
        packagePath: normalized(item.package_path),
        ticker: upper(item.ticker),
        companyName: normalized(item.company_name) || '未知公司',
        form: formFromItem(item),
        fiscalYear: normalized(item.fiscal_year),
        periodEnd: normalized(item.period_end),
        filingDate: normalized(item.filing_date),
        status: derivedStatus,
        statusText: parseStatusText[derivedStatus],
        qualityStatus: qualityStatus(item.quality_status),
        sectionCount: numberValue(summary.section_count),
        tableCount: numberValue(summary.table_count),
        factCount: numberValue(summary.xbrl_fact_count),
        metricCount: numberValue(summary.normalized_metric_count),
        item,
      }
    })
    .sort((a, b) => new Date(b.filingDate || b.periodEnd || 0).getTime() - new Date(a.filingDate || a.periodEnd || 0).getTime())
}

export function deriveUsSecArtifactManifest(detail?: UsSecPackageDetail | null): UsSecArtifactManifestView {
  const ready = packageReady(detail)
  const chips = usSecArtifactNames.map((name) => ({ name, ready }))
  const readyCount = chips.filter((chip) => chip.ready).length
  return {
    chips,
    readyCount,
    total: chips.length,
    checks: [
      {
        label: 'SEC 证据包',
        status: ready ? 'ready' : 'missing',
        description: ready ? `${readyCount}/${chips.length} 个核心文件已生成` : '等待生成 SEC 证据包',
      },
      {
        label: '解析产物索引',
        status: ready ? 'ready' : 'missing',
        description: ready ? 'SEC 解析产物可用于 PostgreSQL 入库与公司级 Wiki 派生' : '等待 SEC 解析产物',
      },
      {
        label: '语义层脚本',
        status: 'ready',
        description: 'market evidence chunks / 项目设置模型',
      },
      {
        label: 'PostgreSQL 入库脚本',
        status: 'ready',
        description: 'scripts/us-sec/ingest_sec_case_set.py',
      },
    ],
  }
}

export function deriveUsSecWorkflowSummary(
  status?: UsSecCaseSetStatus | null,
  detail?: UsSecPackageDetail | null,
): UsSecWorkflowSummary {
  const artifactManifest = deriveUsSecArtifactManifest(detail)
  const packageStatus: UsSecWorkflowStepView['status'] = packageReady(detail) ? 'ready' : 'pending'
  const importedPackages = numberValue(status?.ingest_report?.package_count)
  const importedFacts = numberValue(status?.ingest_report?.summary?.xbrl_facts)
  const retrievalChunks = numberValue(status?.ingest_report?.summary?.retrieval_chunks)
  const postgresReady = importedPackages > 0 && importedFacts > 0
  const semanticReady = retrievalChunks > 0
  const steps: UsSecWorkflowStepView[] = [
    {
      label: '解析产物包',
      status: packageStatus,
      description: packageReady(detail) ? `${artifactManifest.readyCount}/${artifactManifest.total} 个核心文件已生成` : '等待解析生成 evidence package',
    },
    {
      label: 'Wiki 派生',
      status: packageStatus,
      description: packageReady(detail) ? '公司级 Wiki 由 SEC 解析产物派生' : '等待 SEC 解析产物',
    },
    {
      label: '语义层',
      status: semanticReady ? 'ready' : 'pending',
      description: semanticReady ? `检索 chunks ${retrievalChunks}` : '等待使用项目设置模型生成检索语义层',
    },
    {
      label: 'PostgreSQL',
      status: postgresReady ? 'ready' : 'pending',
      description: postgresReady ? `XBRL facts ${importedFacts}` : '等待 PostgreSQL 入库',
    },
  ]
  return { steps, cards: steps }
}

export function deriveUsSecQualitySummary(detail?: UsSecPackageDetail | null): UsSecQualitySummary {
  const counts = detail?.counts || {}
  const quality = (detail?.quality || {}) as Record<string, unknown>
  const bridgeSummary = detail?.bridge_checks?.summary || {}
  const missingCoreSections = Array.isArray(quality.missing_core_sections)
    ? quality.missing_core_sections.map((value) => normalized(value)).filter(Boolean)
    : []
  return {
    tiles: [
      { label: 'Sections', value: countText(counts.sections), description: '主体章节' },
      { label: 'Tables', value: countText(counts.tables), description: '表格' },
      { label: 'Metrics', value: countText(counts.metrics), description: '标准指标' },
      { label: 'Evidence', value: countText(counts.evidence), description: '证据项' },
      { label: 'Dimensions', value: countText(counts.dimension_metrics), description: '维度事实' },
    ],
    bridgeStatus: normalized(detail?.bridge_checks?.overall_status || quality.status || 'unknown'),
    bridgeCounts: {
      pass: numberValue(bridgeSummary.pass),
      warning: numberValue(bridgeSummary.warning),
      fail: numberValue(bridgeSummary.fail),
      skipped: numberValue(bridgeSummary.skipped),
    },
    missingCoreSections,
  }
}
