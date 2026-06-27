import { readJsonResponse } from './pdfApi'

export interface UsSecCaseSetItem {
  ticker?: string
  company_name?: string
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

export async function fetchUsSecCaseSet(): Promise<UsSecCaseSetStatus> {
  const r = await fetch('/api/us-sec/case-set')
  const d = await readJsonResponse<UsSecCaseSetStatus>(r)
  if (!r.ok) throw new Error('加载美股 SEC 案例集失败')
  return d
}

export async function runUsSecCaseSetIngest(body: UsSecIngestRequest): Promise<UsSecIngestResponse> {
  const r = await fetch('/api/us-sec/case-set/ingest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const d = await readJsonResponse<UsSecIngestResponse>(r)
  if (!r.ok || d.ok === false) {
    throw new Error(String(d.stderr || d.stdout || d.returncode || '美股 SEC 入库失败'))
  }
  return d
}

export async function fetchMarketReportJob<T = Record<string, unknown>>(jobId: string): Promise<UsSecJobStatus<T>> {
  const r = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`)
  const d = await readJsonResponse<UsSecJobStatus<T>>(r)
  if (!r.ok) throw new Error('加载任务状态失败')
  return d
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
  const r = await fetch(`/api/us-sec/packages/${encodeURIComponent(ticker)}`)
  const d = await readJsonResponse<UsSecPackageDetail>(r)
  if (!r.ok) throw new Error(`加载 ${ticker} 证据包失败`)
  return d
}

export function usSecPackageFileUrl(packagePath: string, file: string): string {
  const params = new URLSearchParams({ package_path: packagePath, file })
  return `/api/us-sec/package-file?${params.toString()}`
}

export async function fetchUsSecPackageText(packagePath: string, file: string): Promise<string> {
  const r = await fetch(usSecPackageFileUrl(packagePath, file))
  if (!r.ok) throw new Error('读取证据包文件失败')
  return r.text()
}

export async function rebuildUsSecPackage(ticker: string): Promise<{ ok?: boolean; queued?: boolean; job_id?: string; package?: UsSecPackageDetail; stdout?: string; stderr?: string }> {
  const r = await fetch(`/api/us-sec/packages/${encodeURIComponent(ticker)}/rebuild`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
  const d = await readJsonResponse<{ ok?: boolean; queued?: boolean; job_id?: string; package?: UsSecPackageDetail; stdout?: string; stderr?: string }>(r)
  if (!r.ok || d.ok === false) throw new Error(String(d.stderr || d.stdout || '重建 Wiki 证据包失败'))
  return d
}

export async function fetchMarketPackages(market: MarketCode, q = ''): Promise<MarketPackagesResponse> {
  const params = new URLSearchParams({ market, limit: '120' })
  if (q.trim()) params.set('q', q.trim())
  const r = await fetch(`/api/market-reports/packages?${params.toString()}`)
  const d = await readJsonResponse<MarketPackagesResponse>(r)
  if (!r.ok) throw new Error('加载市场证据包失败')
  return d
}

export async function fetchMarketPackageDetail(market: MarketCode, packagePath: string): Promise<MarketPackageDetail> {
  const params = new URLSearchParams({ market, package_path: packagePath })
  const r = await fetch(`/api/market-reports/package?${params.toString()}`)
  const d = await readJsonResponse<MarketPackageDetail>(r)
  if (!r.ok) throw new Error('加载证据包详情失败')
  return d
}

export async function runMarketPackageImport(market: MarketCode, packagePath: string, ddl = false): Promise<MarketPackageActionResponse> {
  const r = await fetch('/api/market-reports/packages/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ market, package_path: packagePath, ddl }),
  })
  const d = await readJsonResponse<MarketPackageActionResponse>(r)
  if (!r.ok || d.ok === false) throw new Error(String(d.stderr || d.stdout || '证据包入库失败'))
  return d
}

export async function runMarketPackageBuild(market: MarketCode, body: MarketPackageBuildRequest): Promise<MarketPackageActionResponse> {
  const r = await fetch('/api/market-reports/packages/build', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ market, ...body }),
  })
  const d = await readJsonResponse<MarketPackageActionResponse>(r)
  if (!r.ok || d.ok === false) throw new Error(String(d.stderr || d.stdout || '证据包构建失败'))
  return d
}

export async function runMarketPackageVectorIngest(market: MarketCode, packagePath: string, dryRun = true): Promise<MarketPackageActionResponse> {
  const r = await fetch('/api/market-reports/packages/vector-ingest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ market, package_path: packagePath, dry_run: dryRun, batch_tag: `market-${market.toLowerCase()}-evidence` }),
  })
  const d = await readJsonResponse<MarketPackageActionResponse>(r)
  if (!r.ok || d.ok === false) throw new Error(String(d.stderr || d.stdout || 'Milvus chunk 生成失败'))
  return d
}

export function marketPackageFileUrl(market: MarketCode | string, packagePath: string, file: string): string {
  const params = new URLSearchParams({ market: String(market), package_path: packagePath, file })
  return `/api/market-reports/package-file?${params.toString()}`
}
