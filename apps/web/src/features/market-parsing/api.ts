import { apiJson, apiText } from '../../shared/api/client'

export interface UsSecCaseSetItem {
  ticker?: string
  company_name?: string
  form?: string
  fiscal_year?: number
  period_end?: string
  filing_date?: string
  quality_status?: string
  quality_summary?: {
    section_count?: number
    table_count?: number
    xbrl_fact_count?: number
    normalized_metric_count?: number
  }
  package_path?: string
}

export interface UsSecCaseSetStatus {
  case_set_path?: string
  ingest_report_path?: string
  company_count?: number
  quality?: Record<string, number>
  counts?: Record<string, number>
  items?: UsSecCaseSetItem[]
  ingest_report?: {
    generated_at?: string
    package_count?: number
    collection?: string
    batch_tag?: string
    summary?: {
      xbrl_facts?: number
      normalized_metrics?: number
      sections?: number
      tables?: number
      evidence_items?: number
      retrieval_chunks?: number
      section_chunks?: number
      metric_chunks?: number
      quality?: Record<string, number>
    }
  }
}

export interface UsSecPackageDetail {
  package_path?: string
  manifest?: Record<string, unknown>
  quality?: Record<string, unknown>
  financial_checks?: Record<string, unknown>
  bridge_checks?: {
    overall_status?: string
    summary?: Record<string, number>
    checks?: Array<Record<string, unknown>>
  }
  counts?: {
    sections?: number
    tables?: number
    metrics?: number
    evidence?: number
    dimension_metrics?: number
  }
  sections?: Array<Record<string, unknown>>
  tables?: Array<Record<string, unknown>>
  metrics?: Array<Record<string, unknown>>
  dimension_metrics?: Array<Record<string, unknown>>
  preview?: {
    raw_html?: string
    default_markdown?: string
  }
}

export interface UsSecIngestRequest {
  dry_run?: boolean
  postgres?: boolean
  milvus?: boolean
  ddl?: boolean
  include_fail?: boolean
  tickers?: string
  batch_tag?: string
}

export interface UsSecIngestResponse {
  ok?: boolean
  queued?: boolean
  job_id?: string
  status?: string
  returncode?: number
  command?: string
  stdout?: string
  stderr?: string
  report?: Record<string, unknown>
}

export interface UsSecJobStatus<T = Record<string, unknown>> {
  job_id?: string
  kind?: string
  status?: 'queued' | 'running' | 'succeeded' | 'failed' | string
  created_at?: string
  started_at?: string | null
  finished_at?: string | null
  result?: T | null
  error?: string | null
}

export type MarketCode = 'US' | 'HK' | 'JP' | 'KR' | 'EU'

export interface MarketPackageQualityGates {
  schema_version?: string
  overall_status?: 'pass' | 'warning' | 'fail' | 'unknown' | string
  action_blocked?: boolean
  import_blocked?: boolean
  vector_ingest_blocked?: boolean
  force_allowed?: boolean
  block_reasons?: string[]
  evidence_coverage_ratio?: number | null
  required_statement_status?: Record<string, string>
  missing_required_statements?: string[]
  artifact_hash_status?: 'ok' | 'missing' | 'mismatch' | string
  artifact_hash_mismatches?: string[]
  artifact_hash_missing?: string[]
  parser_warnings?: string[]
  rule_warnings?: string[]
  critical_warnings?: string[]
}

export interface MarketPackageSummary {
  package_path?: string
  paths?: Record<string, string>
  market?: MarketCode | string
  country?: string
  document_format?: string
  filing_id?: string
  parse_run_id?: string
  ticker?: string
  company_name?: string
  form?: string
  report_type?: string
  fiscal_year?: number
  fiscal_period?: string
  period_end?: string
  published_at?: string
  quality_status?: string
  quality_gates?: MarketPackageQualityGates
  counts?: {
    sections?: number
    tables?: number
    raw_facts?: number
    metrics?: number
    evidence?: number
  }
}

export interface MarketPackageDetail extends MarketPackageSummary {
  manifest?: Record<string, unknown>
  quality?: Record<string, unknown>
  financial_data?: Record<string, unknown>
  financial_checks?: Record<string, unknown>
  metrics?: Array<Record<string, unknown>>
  source_map?: Array<Record<string, unknown>>
  tables?: Array<Record<string, unknown>>
  parser_artifacts?: Record<string, unknown>
  qa_artifacts?: Record<string, unknown>
}

export interface MarketPackagesResponse {
  ok?: boolean
  market?: string | null
  markets?: string[]
  roots?: Record<string, string>
  count?: number
  packages?: MarketPackageSummary[]
}

export interface MarketPackageActionResponse {
  ok?: boolean
  queued?: boolean
  job_id?: string
  status?: string
  returncode?: number
  command?: string
  stdout?: string
  stderr?: string
  package?: MarketPackageDetail
  parse_run_id?: string | null
  dry_run?: boolean
  summary?: Record<string, unknown> | null
}

export interface MarketPackageBuildRequest {
  source_path?: string
  download_relative_path?: string
  parser_result?: string
  metadata_path?: string
  force?: boolean
}

export interface UsSecUploadResult {
  file_name?: string
  saved_path?: string
  size_bytes?: number
  content_type?: string | null
  cache_hit?: boolean
  deduplicated?: boolean
  content_sha256?: string | null
  metadata_path?: string | null
  relative_path?: string | null
}

export interface UsSecUploadResponse {
  ok?: boolean
  count?: number
  files?: UsSecUploadResult[]
}

export interface UsSecPackageBuildRequest {
  download_relative_path?: string
  source_path?: string
  metadata_path?: string
  force?: boolean
}

export interface UsSecPackageBuildResponse {
  ok?: boolean
  queued?: boolean
  job_id?: string
  status?: string
  returncode?: number
  command?: string
  stdout?: string
  stderr?: string
  package?: UsSecPackageDetail
}

export async function fetchUsSecCaseSet(): Promise<UsSecCaseSetStatus> {
  return apiJson<UsSecCaseSetStatus>('/api/us-sec/case-set')
}

export async function runUsSecCaseSetIngest(body: UsSecIngestRequest): Promise<UsSecIngestResponse> {
  const d = await apiJson<UsSecIngestResponse>('/api/us-sec/case-set/ingest', {
    method: 'POST',
    body,
  })
  if (d.ok === false) {
    throw new Error(String(d.stderr || d.stdout || d.returncode || '美股 SEC 入库失败'))
  }
  return d
}

export async function fetchMarketReportJob<T = Record<string, unknown>>(jobId: string): Promise<UsSecJobStatus<T>> {
  return apiJson<UsSecJobStatus<T>>(`/api/jobs/${encodeURIComponent(jobId)}`)
}

export async function waitForMarketReportJob<T extends { ok?: boolean; stdout?: string; stderr?: string }>(
  jobId: string,
  options: { intervalMs?: number; timeoutMs?: number } = {},
): Promise<T> {
  const intervalMs = options.intervalMs ?? 1500
  const timeoutMs = options.timeoutMs ?? 30 * 60 * 1000
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    const job = await fetchMarketReportJob<T>(jobId)
    if (job.status === 'succeeded' && job.result) return job.result
    if (job.status === 'failed') {
      const detail = job.result?.stderr || job.result?.stdout || job.error || '后台任务失败'
      throw new Error(String(detail))
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs))
  }
  throw new Error('后台任务超时')
}

export async function fetchUsSecPackage(ticker: string): Promise<UsSecPackageDetail> {
  return apiJson<UsSecPackageDetail>(`/api/us-sec/packages/${encodeURIComponent(ticker)}`)
}

export async function uploadUsSecFiles(form: FormData): Promise<UsSecUploadResponse> {
  const d = await apiJson<UsSecUploadResponse>('/api/us-sec/uploads', { method: 'POST', body: form })
  if (d.ok === false) throw new Error('上传 US 文件失败')
  return d
}

export async function buildUsSecPackage(body: UsSecPackageBuildRequest): Promise<UsSecPackageBuildResponse> {
  const d = await apiJson<UsSecPackageBuildResponse>('/api/market-reports/packages/build', {
    method: 'POST',
    body: { market: 'US', ...body },
  })
  if (d.ok === false) throw new Error(String(d.stderr || d.stdout || 'US 证据包构建失败'))
  return d
}

export function usSecPackageFileUrl(packagePath: string, file: string): string {
  const params = new URLSearchParams({ package_path: packagePath, file })
  return `/api/us-sec/package-file?${params.toString()}`
}

export async function fetchUsSecPackageText(packagePath: string, file: string): Promise<string> {
  return apiText(usSecPackageFileUrl(packagePath, file))
}

export async function rebuildUsSecPackage(ticker: string): Promise<{ ok?: boolean; queued?: boolean; job_id?: string; package?: UsSecPackageDetail; stdout?: string; stderr?: string }> {
  const d = await apiJson<{ ok?: boolean; queued?: boolean; job_id?: string; package?: UsSecPackageDetail; stdout?: string; stderr?: string }>(`/api/us-sec/packages/${encodeURIComponent(ticker)}/rebuild`, {
    method: 'POST',
    body: {},
  })
  if (d.ok === false) throw new Error(String(d.stderr || d.stdout || '重建 Wiki 证据包失败'))
  return d
}

export async function fetchMarketPackages(market: MarketCode, q = '', signal?: AbortSignal): Promise<MarketPackagesResponse> {
  const params = new URLSearchParams({ market, limit: '120' })
  if (q.trim()) params.set('q', q.trim())
  return apiJson<MarketPackagesResponse>(`/api/market-reports/packages?${params.toString()}`, { signal })
}

export async function fetchMarketPackageDetail(market: MarketCode, packagePath: string): Promise<MarketPackageDetail> {
  const params = new URLSearchParams({ market, package_path: packagePath })
  return apiJson<MarketPackageDetail>(`/api/market-reports/package?${params.toString()}`)
}

export async function runMarketPackageImport(market: MarketCode, packagePath: string, ddl = false, force = false): Promise<MarketPackageActionResponse> {
  const d = await apiJson<MarketPackageActionResponse>('/api/market-reports/packages/import', {
    method: 'POST',
    body: { market, package_path: packagePath, ddl, force },
  })
  if (d.ok === false) throw new Error(String(d.stderr || d.stdout || '证据包入库失败'))
  return d
}

export async function runMarketPackageBuild(market: MarketCode, body: MarketPackageBuildRequest): Promise<MarketPackageActionResponse> {
  const d = await apiJson<MarketPackageActionResponse>('/api/market-reports/packages/build', {
    method: 'POST',
    body: { market, ...body },
  })
  if (d.ok === false) throw new Error(String(d.stderr || d.stdout || '证据包构建失败'))
  return d
}

export async function runMarketPackageVectorIngest(market: MarketCode, packagePath: string, dryRun = true, force = false): Promise<MarketPackageActionResponse> {
  const d = await apiJson<MarketPackageActionResponse>('/api/market-reports/packages/vector-ingest', {
    method: 'POST',
    body: { market, package_path: packagePath, dry_run: dryRun, batch_tag: `market-${market.toLowerCase()}-evidence`, force },
  })
  if (d.ok === false) throw new Error(String(d.stderr || d.stdout || 'Milvus chunk 生成失败'))
  return d
}

export function marketPackageFileUrl(market: MarketCode | string, packagePath: string, file: string): string {
  const params = new URLSearchParams({ market: String(market), package_path: packagePath, file })
  return `/api/market-reports/package-file?${params.toString()}`
}
