import type { DownloadedPdf } from '../../lib/pdfTypes'
import type {
  MarketDocumentFullPostgresStatus,
  UsSecCaseSetItem,
  UsSecCaseSetStatus,
  UsSecPackageBuildRequest,
  UsSecPackageDetail,
} from './api'
import {
  deriveUsSecMarketIngestionPipelineState,
  type MarketIngestionPipelineActionState,
  type UsSecIngestionActionKey,
} from './marketIngestionPipelineState'

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
  documentFullPath: string
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
  activeStepIndex: number
  actions: Array<MarketIngestionPipelineActionState<UsSecIngestionActionKey>>
  runAll: MarketIngestionPipelineActionState<'runAll'>
}

export interface UsSecQualitySummary {
  tiles: Array<{ label: string; value: string; description: string }>
  bridgeStatus: string
  bridgeCounts: Record<string, number>
  missingCoreSections: string[]
}

const US_SEC_DOCUMENT_FULL_FILE = 'document_full.json'

const usSecFallbackArtifactNames = [
  'manifest.json',
  'parser/document_full.json',
  'parser/report_complete.md',
  'parser/content_list_enhanced.json',
  'parser/table_relations.json',
  'sections/report_complete.md',
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
  package_ready: '解析产物已生成',
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

function retrievalReady(item: UsSecCaseSetItem | null | undefined): boolean {
  return item?.wiki_ready === true || qualityStatus(item?.retrieval_status) === 'ready'
}

function packageReady(detail: UsSecPackageDetail | null | undefined): boolean {
  return Boolean(detail?.package_path)
}

function caseItemForPackage(status: UsSecCaseSetStatus | null | undefined, detail: UsSecPackageDetail | null | undefined): UsSecCaseSetItem | null {
  const items = status?.items || []
  if (!items.length) return null
  const manifest = plainRecord(detail?.manifest)
  const packagePath = normalized(detail?.package_path || manifest.package_path)
  const parserResultDir = normalized(detail?.parser_result_dir || manifest.parser_result_dir)
  const parserResultTaskId = normalized(detail?.parser_result_task_id || manifest.parser_result_task_id)
  return items.find((item) => normalized(item.package_path) === packagePath)
    || items.find((item) => normalized(item.parser_result_dir) === parserResultDir)
    || items.find((item) => normalized(item.parser_result_task_id) === parserResultTaskId)
    || null
}

function usSecDocumentFullPath(item: UsSecCaseSetItem): string {
  const explicitPaths = plainRecord(item.full_document_paths)
  const explicitPath = firstPathValue(
    item.document_full_path
      || explicitPaths.document_full_path,
    explicitPaths.document_full,
    explicitPaths.document_full_json,
    explicitPaths.parser_document_full,
  )
  if (explicitPath) {
    const fromExplicitPath = documentFullPathFromParserResultDir(explicitPath)
    if (fromExplicitPath) return fromExplicitPath
  }
  const parserResultDir = normalized(item.parser_result_dir)
  if (parserResultDir) return documentFullPathFromParserResultDir(parserResultDir)
  const parserResultTaskId = normalized(item.parser_result_task_id)
  if (parserResultTaskId) return documentFullPathFromParserResultDir(`data/parser-results/us-sec/${parserResultTaskId}`)
  return ''
}

function plainRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function pathValue(value: unknown): string {
  if (typeof value === 'string' || typeof value === 'number') return normalized(value)
  const record = plainRecord(value)
  return normalized(record.path || record.document_full_path || record.document_full_json || record.parser_document_full)
}

function firstPathValue(...values: unknown[]): string {
  for (const value of values) {
    const path = pathValue(value)
    if (path) return path
  }
  return ''
}

function usSecArtifactNames(detail: UsSecPackageDetail | null | undefined): string[] {
  const manifest = plainRecord(detail?.manifest)
  const artifacts = plainRecord(manifest.artifacts)
  if (!Object.keys(artifacts).length) return usSecFallbackArtifactNames
  const preferredKeys = [
    'document_full',
    'report_complete',
    'content_list_enhanced',
    'table_relations',
    'wiki_report_complete',
    'sections',
    'table_index',
    'xbrl_facts_raw',
    'xbrl_contexts',
    'xbrl_units',
    'xbrl_labels',
    'xbrl_taxonomy_summary',
    'financial_data',
    'financial_checks',
    'normalized_metrics',
    'operating_metrics',
    'quality_report',
    'source_map',
    'extraction_warnings',
  ]
  const names = ['manifest.json']
  const seen = new Set(names)
  const addArtifact = (value: unknown) => {
    const name = normalized(value)
    if (!name || seen.has(name)) return
    seen.add(name)
    names.push(name)
  }
  preferredKeys.forEach((key) => addArtifact(artifacts[key]))
  Object.keys(artifacts).sort().forEach((key) => addArtifact(artifacts[key]))
  return names
}

function usSecArtifactReady(detail: UsSecPackageDetail | null | undefined, name: string): boolean {
  if (!packageReady(detail)) return false
  if (name === 'manifest.json') return true
  const hashes = plainRecord(plainRecord(detail?.manifest).artifact_hashes)
  return !Object.keys(hashes).length || Boolean(hashes[name])
}

function countText(value: unknown): string {
  return String(numberValue(value))
}

function ratioText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-'
  const n = Number(value)
  return Number.isFinite(n) ? `${Math.round(n * 1000) / 10}%` : '-'
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

function packageFilePath(packagePath: unknown, file: unknown): string {
  const base = normalized(packagePath).replace(/\/+$/, '')
  const localFile = normalized(file).replace(/^\/+/, '')
  if (!base || !localFile) return ''
  if (localFile === base || localFile.startsWith(`${base}/`)) return localFile
  return `${base}/${localFile}`
}

function detailMatchesTaskPackage(
  task?: UsSecRecentTaskRow | null,
  detail?: UsSecPackageDetail | null,
): boolean {
  const taskPackagePath = normalized(task?.packagePath || task?.item.package_path)
  if (!taskPackagePath) return true
  const detailManifest = plainRecord(detail?.manifest)
  const detailPackagePath = normalized(detail?.package_path || detailManifest.package_path)
  return Boolean(detailPackagePath && detailPackagePath === taskPackagePath)
}

function documentFullPathFromParserResultDir(value: unknown): string {
  const path = pathValue(value).replace(/\/+$/, '')
  if (!path) return ''
  if (isUsSecPackageLocalDocumentFullPath(path)) return ''
  return path.endsWith(`/${US_SEC_DOCUMENT_FULL_FILE}`) || path === US_SEC_DOCUMENT_FULL_FILE
    ? path
    : `${path}/${US_SEC_DOCUMENT_FULL_FILE}`
}

function isUsSecPackageLocalDocumentFullPath(value: string): boolean {
  const path = value.replace(/^\.?\//, '')
  if (path === `parser/${US_SEC_DOCUMENT_FULL_FILE}` || path === 'parser') return true
  return path.endsWith(`/parser/${US_SEC_DOCUMENT_FULL_FILE}`) && !path.includes('/data/parser-results/us-sec/')
}

export function deriveUsSecDocumentFullImportPath(
  task?: UsSecRecentTaskRow | null,
  detail?: UsSecPackageDetail | null,
): string {
  const scopedDetail = detailMatchesTaskPackage(task, detail) ? detail : null
  const manifest = plainRecord(scopedDetail?.manifest)
  const detailPaths = plainRecord(scopedDetail?.full_document_paths || manifest.full_document_paths)
  const itemPaths = plainRecord(task?.item.full_document_paths)
  const explicitPath = firstPathValue(
    scopedDetail?.document_full_path,
    manifest.document_full_path,
    detailPaths.document_full_path,
    detailPaths.document_full,
    detailPaths.document_full_json,
    task?.item.document_full_path,
    itemPaths.document_full_path,
    itemPaths.document_full,
    itemPaths.document_full_json,
    itemPaths.parser_document_full,
  )
  const fromExplicitPath = documentFullPathFromParserResultDir(explicitPath)
  if (fromExplicitPath) return fromExplicitPath

  const parserResultDir = scopedDetail?.parser_result_dir || manifest.parser_result_dir || task?.item.parser_result_dir
  const fromDir = documentFullPathFromParserResultDir(parserResultDir)
  if (fromDir) return fromDir

  const parserResultTaskId = scopedDetail?.parser_result_task_id || manifest.parser_result_task_id || task?.item.parser_result_task_id
  return parserResultTaskId
    ? documentFullPathFromParserResultDir(`data/parser-results/us-sec/${parserResultTaskId}`)
    : ''
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
  const company = normalized(report.companyName || report.company).toLowerCase()
  const accessionMatch = accession
    ? items.find((item) =>
      pathIncludesAccession(item.package_path, accession)
      && (!periodEnd || normalized(item.period_end) === periodEnd)
    ) || items.find((item) => pathIncludesAccession(item.package_path, accession))
    : null
  if (accessionMatch) return accessionMatch
  if (ticker && periodEnd) {
    const periodMatch = items.find((item) => upper(item.ticker) === ticker && normalized(item.period_end) === periodEnd)
    if (periodMatch) return periodMatch
  }
  if (company && periodEnd) {
    const companyPeriodMatch = items.find((item) =>
      normalized(item.company_name).toLowerCase() === company
      && normalized(item.period_end) === periodEnd
    )
    if (companyPeriodMatch) return companyPeriodMatch
  }
  if (accession || periodEnd) return null
  const tickerMatch = ticker ? items.find((item) => upper(item.ticker) === ticker) : null
  if (tickerMatch) return tickerMatch
  return company
    ? items.find((item) => normalized(item.company_name).toLowerCase() === company) || null
    : null
}

export function deriveUsSecPackageRebuildRequest(
  task?: UsSecRecentTaskRow | null,
  detail?: UsSecPackageDetail | null,
): UsSecPackageBuildRequest | null {
  const scopedDetail = detailMatchesTaskPackage(task, detail) ? detail : null
  const manifest = plainRecord(scopedDetail?.manifest)
  const packagePath = normalized(task?.packagePath || task?.item.package_path || scopedDetail?.package_path || manifest.package_path)
  if (!packagePath) return null
  const localSource = normalized(
    manifest.local_source_path
      || manifest.source_path
      || manifest.raw_html
      || scopedDetail?.preview?.raw_html
      || 'raw/filing.htm',
  )
  const sourcePath = packageFilePath(packagePath, localSource)
  return sourcePath ? { source_path: sourcePath, force: true } : null
}

export function deriveUsSecParseStatus({
  report,
  item,
  status,
  postgresStatus,
  busyPath = '',
}: {
  report: DownloadedPdf
  item?: UsSecCaseSetItem | null
  status?: UsSecCaseSetStatus | null
  postgresStatus?: MarketDocumentFullPostgresStatus | null
  busyPath?: string
}): UsSecParseStatus {
  void status
  if (busyPath && busyPath === report.relativePath) return 'building'
  if (!item?.package_path) return 'unparsed'
  const readyForRetrieval = retrievalReady(item)
  const quality = normalized(item.quality_status).toLowerCase()
  if (quality === 'fail' || quality === 'failed') return 'failed'
  if ((quality === 'warning' || quality === 'warn') && !readyForRetrieval) return 'warning'
  if (documentFullPostgresReady(postgresStatus)) return 'postgres_ready'
  return 'package_ready'
}

export function deriveUsSecDownloadedRows(
  reports: DownloadedPdf[],
  status?: UsSecCaseSetStatus | null,
  busyPath = '',
  postgresByDocumentFullPath: Record<string, MarketDocumentFullPostgresStatus | undefined> = {},
): UsSecDownloadedRow[] {
  return reports.map((report) => {
    const item = findUsSecCaseItem(report, status)
    const documentFullPath = item ? usSecDocumentFullPath(item) : ''
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
      parseStatus: deriveUsSecParseStatus({
        report,
        item,
        status,
        busyPath,
        postgresStatus: postgresByDocumentFullPath[documentFullPath],
      }),
      packagePath: normalized(item?.package_path),
      report,
    }
  })
}

export function deriveUsSecRecentTasks(
  status?: UsSecCaseSetStatus | null,
  postgresByDocumentFullPath: Record<string, MarketDocumentFullPostgresStatus | undefined> = {},
): UsSecRecentTaskRow[] {
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
      const documentFullPath = usSecDocumentFullPath(item)
      const derivedStatus = deriveUsSecParseStatus({
        report: syntheticReport,
        item,
        status,
        postgresStatus: postgresByDocumentFullPath[documentFullPath],
      })
      const summary = item.quality_summary || {}
      return {
        id: normalized(item.package_path),
        packagePath: normalized(item.package_path),
        documentFullPath,
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
  const artifactNames = usSecArtifactNames(detail)
  const ready = packageReady(detail)
  const chips = artifactNames.map((name) => ({ name, ready: usSecArtifactReady(detail, name) }))
  const readyCount = chips.filter((chip) => chip.ready).length
  return {
    chips,
    readyCount,
    total: chips.length,
    checks: [
      {
        label: 'SEC 解析产物',
        status: ready ? 'ready' : 'missing',
        description: ready ? `${readyCount}/${chips.length} 个核心文件已生成` : '等待生成 SEC 解析产物',
      },
      {
        label: '解析产物索引',
        status: ready ? 'ready' : 'missing',
        description: ready ? 'SEC 解析产物可用于 LLM-Wiki、Wiki语义增强与 PostgreSQL 入库' : '等待 SEC 解析产物',
      },
      {
        label: 'Wiki语义增强入库脚本',
        status: 'ready',
        description: 'Wiki 证据语义 / 项目设置模型',
      },
      {
        label: 'PostgreSQL入库脚本',
        status: 'ready',
        description: 'db/imports/import_us_sec_document_full_to_postgres.py',
      },
    ],
  }
}

export function deriveUsSecWorkflowSummary(
  status?: UsSecCaseSetStatus | null,
  detail?: UsSecPackageDetail | null,
  postgresStatus?: MarketDocumentFullPostgresStatus | null,
  options: { documentFullPath?: string; busyAction?: string; taskId?: string } = {},
): UsSecWorkflowSummary {
  const artifactManifest = deriveUsSecArtifactManifest(detail)
  const selectedItem = caseItemForPackage(status, detail)
  const wikiReady = selectedItem ? retrievalReady(selectedItem) : packageReady(detail)
  const semanticStatus = plainRecord(detail?.semantic_status || selectedItem?.semantic_status)
  const semanticCounts = plainRecord(semanticStatus.counts)
  const semanticLlm = plainRecord(semanticStatus.llm)
  const semanticLlmCounts = plainRecord(semanticLlm.counts)
  const semanticEvidence = numberValue(
    semanticCounts.evidence
      || semanticCounts.segments
      || status?.ingest_report?.summary?.retrieval_chunks,
  )
  const semanticStatusText = normalized(semanticStatus.status).toLowerCase()
  const semanticReady = semanticStatusText
    ? semanticStatusText === 'ready'
    : semanticEvidence > 0
  const semanticDescription = semanticReady
    ? [
        `规则语义 segments ${numberValue(semanticCounts.segments)} / facts ${numberValue(semanticCounts.facts)} / evidence ${numberValue(semanticCounts.evidence) || semanticEvidence}`,
        normalized(semanticLlm.status).toLowerCase() === 'ready'
          ? `模型增强 claims ${numberValue(semanticLlmCounts.claims)} / risks ${numberValue(semanticLlmCounts.risks)}`
          : '',
      ].filter(Boolean).join('；')
    : normalized(semanticStatus.message)
  const pipelineState = deriveUsSecMarketIngestionPipelineState({
    artifactsReady: packageReady(detail),
    artifactReadyCount: artifactManifest.readyCount,
    artifactTotal: artifactManifest.total,
    wikiReady,
    semanticEvidence,
    semanticReady,
    semanticDescription,
    postgresStatus,
    documentFullPath: options.documentFullPath,
    busyAction: options.busyAction,
    taskId: options.taskId,
  })
  const steps: UsSecWorkflowStepView[] = pipelineState.steps.map((step) => ({
    label: step.label,
    status: (step.status || 'pending') as UsSecWorkflowStatus,
    description: step.description,
  }))
  return {
    steps,
    cards: steps,
    activeStepIndex: pipelineState.activeStepIndex,
    actions: pipelineState.actions,
    runAll: pipelineState.runAll,
  }
}

export function documentFullPostgresReady(status?: MarketDocumentFullPostgresStatus | null): boolean {
  const value = normalized(status?.status).toLowerCase()
  return value === 'postgres_ready' || value === 'ready'
}

export function deriveUsSecQualitySummary(detail?: UsSecPackageDetail | null): UsSecQualitySummary {
  const counts = detail?.counts || {}
  const quality = (detail?.quality || {}) as Record<string, unknown>
  const qualityGates = detail?.quality_gates || {}
  const bridgeSummary = detail?.bridge_checks?.summary || {}
  const missingCoreSections = Array.isArray(quality.missing_core_sections)
    ? quality.missing_core_sections.map((value) => normalized(value)).filter(Boolean)
    : []
  const evidenceCoverageRatio = quality.evidence_coverage_ratio ?? qualityGates.evidence_coverage_ratio
  const evidenceResolvabilityRatio = quality.evidence_resolvability_ratio ?? qualityGates.evidence_resolvability_ratio
  const unresolvableEvidenceCount = quality.unresolvable_evidence_count ?? qualityGates.unresolvable_evidence_count
  return {
    tiles: [
      { label: 'Sections', value: countText(counts.sections), description: '主体章节' },
      { label: 'Tables', value: countText(counts.tables), description: '表格' },
      { label: 'Metrics', value: countText(counts.metrics), description: '标准指标' },
      { label: 'Evidence', value: countText(counts.evidence), description: '证据项' },
      { label: '证据字段覆盖', value: ratioText(evidenceCoverageRatio), description: '结构化字段绑定证据比例' },
      { label: '证据可回链', value: `${ratioText(evidenceResolvabilityRatio)} · 不可回链 ${countText(unresolvableEvidenceCount)}`, description: '证据目标可打开、可定位、可复核比例' },
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
