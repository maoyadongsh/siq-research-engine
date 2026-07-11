import { apiBlob, apiJson, readJsonResponse, type ApiRequestInit } from '../../shared/api/client'
import { WIKI_INPUT_ARTIFACTS, type ArtifactInfo, type ArtifactsMap, type DownloadedPdf, type FinancialResult, type PageContent, type WorkflowJob, type WorkflowStatus } from '../../lib/pdfTypes'
import type { MarketCode, MarketDocumentFullImportResponse } from '../market-parsing/api'
import { deriveMarketDocumentFullPostgresSummary } from '../market-parsing/marketIngestionPipelineState'

export const PDF_API = '/api/pdf'

export { readJsonResponse }

export function artifactUrl(info: ArtifactInfo | null | undefined): string {
  const raw = String(info?.url || '')
  if (!raw) return ''
  if (raw.startsWith('/api/artifact/')) return raw.replace(/^\/api/, PDF_API)
  return raw
}

export function artifactDownloadName(name: string): string {
  if (name === 'images') return 'images.zip'
  return name
}

export function artifactDownloadUrl(name: string, info: ArtifactInfo | null | undefined): string {
  const url = artifactUrl(info)
  if (name === 'images' && url) return `${url}/download`
  return url
}

export function pipelineArtifactSummary(artifacts: ArtifactsMap | null): {
  ready: string[]
  total: number
  missing: string[]
} {
  const ready = WIKI_INPUT_ARTIFACTS.filter((name) => artifacts?.[name]?.exists)
  return {
    ready,
    total: WIKI_INPUT_ARTIFACTS.length,
    missing: WIKI_INPUT_ARTIFACTS.filter((name) => !artifacts?.[name]?.exists),
  }
}

export function workflowReady(status: Record<string, unknown> | null | undefined, key: string): boolean {
  const bucket = status?.[key] as Record<string, unknown> | undefined
  return ['ready', 'missing_optional', 'stale_optional'].includes(String(bucket?.status || ''))
}

function numberValue(value: unknown): number {
  const n = Number(value || 0)
  return Number.isFinite(n) ? n : 0
}

function hasOwn(record: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key)
}

function isMarketDocumentFullDatabaseStatus(record: Record<string, unknown>): boolean {
  return [
    'facts',
    'chunks',
    'evidence',
    'schema',
    'marketStatus',
    'parse_runs',
    'parseRuns',
    'parse_run_id',
    'parseRunId',
    'missing_counts',
    'missingCounts',
  ].some((key) => hasOwn(record, key))
}

export function normalizeWorkflowStatus(data: WorkflowStatus): WorkflowStatus {
  const database = data.database
  if (!database) return data
  const databaseRecord = database as Record<string, unknown>
  if (!isMarketDocumentFullDatabaseStatus(databaseRecord)) {
    const facts = numberValue(database.statementItems)
    const tables = numberValue(database.tables)
    const message = database.message || (workflowReady(data as Record<string, unknown>, 'database')
      ? [`指标 ${facts}`, `表格 ${tables}`].filter(Boolean).join(' / ')
      : undefined)
    return {
      ...data,
      database: {
        ...database,
        statementItems: facts,
        tables,
        message,
      },
    }
  }

  const summary = deriveMarketDocumentFullPostgresSummary(databaseRecord)
  const normalizedDatabase = {
    ...database,
    status: database.status,
    statementItems: numberValue(database.statementItems || summary.facts),
    facts: summary.facts,
    tables: summary.tables,
    chunks: summary.chunks,
    evidence: summary.evidence,
    message: database.message || summary.description,
  } as WorkflowStatus['database'] & { facts: number; chunks: number; evidence: number }
  return {
    ...data,
    database: normalizedDatabase,
  }
}

export async function loadDownloadedReports(text: string, market?: 'CN' | 'HK' | 'US' | 'EU' | 'KR' | 'JP'): Promise<{ reports: DownloadedPdf[] }> {
  const params = new URLSearchParams({ q: text.trim(), limit: '120' })
  if (market) params.set('market', market)
  const data = await apiJson<{ reports?: DownloadedPdf[] }>(`/api/downloads/reports?${params.toString()}`)
  const reports = (data.reports || []) as DownloadedPdf[]
  return {
    ...data,
    reports: (market ? reports.filter((report) => report.relativePath.startsWith(`${market}/`)) : reports).map((report) => {
      const marketFromPath = report.relativePath.split('/')[0]
      return marketFromPath === 'CN' || marketFromPath === 'HK' || marketFromPath === 'US' || marketFromPath === 'EU' || marketFromPath === 'KR' || marketFromPath === 'JP'
        ? { ...report, market: marketFromPath }
        : report
    }),
  }
}

export async function checkHealth(): Promise<{ mineru: boolean; vlm: boolean; submit_ready: boolean; warning?: string } | null> {
  try {
    const d = await apiJson<{ mineru?: boolean; vlm?: boolean; submit_ready?: boolean; warning?: string }>(`${PDF_API}/health`)
    return { mineru: !!d.mineru, vlm: !!d.vlm, submit_ready: !!d.submit_ready, warning: d.warning || undefined }
  } catch {
    return null
  }
}

export async function loadTasks(): Promise<Record<string, unknown>[]> {
  const d = await apiJson<{ tasks?: Record<string, unknown>[] }>('/api/pdf/tasks')
  return d.tasks || []
}

export async function downloadedReportToFile(report: DownloadedPdf): Promise<File> {
  if (report.isPdf === false) throw new Error('该文件不是 PDF，当前 PDF 解析器暂不支持直接解析')
  const url = report.url || `/api/downloads/report-file?path=${encodeURIComponent(report.relativePath)}`
  const blob = await apiBlob(url)
  const lastModified = new Date(report.mtime).getTime()
  return new File([blob], report.filename, {
    type: blob.type || 'application/pdf',
    lastModified: Number.isFinite(lastModified) ? lastModified : Date.now(),
  })
}

export async function linkDownloadedReport(report: DownloadedPdf): Promise<void> {
  await apiJson('/api/workspace/downloads/link', {
    method: 'POST',
    body: { relativePath: report.relativePath },
  }).catch(() => null)
}

export interface DownloadedReportParseOptions {
  backend: string
  parseMethod: string
  market?: 'CN' | 'HK' | 'US' | 'EU' | 'KR' | 'JP'
  startPage: string
  endPage: string
  formula: boolean
  table: boolean
}

export async function parseDownloadedReportFromDownload(
  report: DownloadedPdf,
  options: DownloadedReportParseOptions,
): Promise<Record<string, unknown>> {
  if (report.isPdf === false) throw new Error('该文件不是 PDF，当前 PDF 解析器暂不支持直接解析')
  return apiJson<Record<string, unknown>>('/api/pdf/tasks/from-download', {
    method: 'POST',
    body: {
      download_relative_path: report.relativePath,
      filename: report.filename,
      backend: options.backend,
      parse_method: options.parseMethod,
      market: options.market || report.market || 'CN',
      start_page_id: options.startPage,
      end_page_id: options.endPage,
      formula_enable: options.formula ? 'true' : 'false',
      table_enable: options.table ? 'true' : 'false',
    },
  })
}

export async function fetchStatus(taskId: string, since: number, init: ApiRequestInit = {}): Promise<Record<string, unknown>> {
  return apiJson<Record<string, unknown>>(`${PDF_API}/status/${encodeURIComponent(taskId)}?since=${since}`, init)
}

export async function cancelTaskApi(taskId: string): Promise<{ success: boolean; upstream_cancelled?: boolean }> {
  return apiJson<{ success: boolean; upstream_cancelled?: boolean }>(`${PDF_API}/cancel/${encodeURIComponent(taskId)}`, { method: 'POST' })
}

export async function deleteTaskApi(taskId: string): Promise<void> {
  await apiJson(`${PDF_API}/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
}

export async function refetchTaskApi(taskId: string): Promise<void> {
  await apiJson(`${PDF_API}/refetch/${encodeURIComponent(taskId)}`, { method: 'POST' })
}

export async function reparseTaskApi(taskId: string): Promise<{ task_id: string; filename: string }> {
  return apiJson<{ task_id: string; filename: string }>(`${PDF_API}/reparse/${encodeURIComponent(taskId)}`, { method: 'POST' })
}

export async function uploadPdfs(form: FormData): Promise<Record<string, unknown>> {
  return apiJson<Record<string, unknown>>('/api/pdf/upload', { method: 'POST', body: form })
}

export async function fetchResultApi(taskId: string): Promise<{ artifacts?: ArtifactsMap; markdown?: string }> {
  return apiJson<{ artifacts?: ArtifactsMap; markdown?: string }>(`${PDF_API}/result/${encodeURIComponent(taskId)}`)
}

export async function fetchQualityApi(taskId: string): Promise<{ quality?: Record<string, unknown> }> {
  return apiJson<{ quality?: Record<string, unknown> }>(`${PDF_API}/quality/${encodeURIComponent(taskId)}`)
}

export async function fetchFinancialApi(taskId: string): Promise<FinancialResult> {
  return apiJson<FinancialResult>(`${PDF_API}/financial/${encodeURIComponent(taskId)}`)
}

export async function loadWorkflowStatusApi(taskId: string): Promise<WorkflowStatus> {
  return normalizeWorkflowStatus(await apiJson<WorkflowStatus>(`/api/workflow/task/${encodeURIComponent(taskId)}/status`))
}

export async function runWorkflowStepApi(
  taskId: string,
  step: 'wiki-import' | 'wiki-import-generic' | 'semantic' | 'semantic-generic' | 'db-import',
): Promise<WorkflowJob> {
  return apiJson<WorkflowJob>(`/api/workflow/task/${encodeURIComponent(taskId)}/${step}`, { method: 'POST' })
}

export async function runMarketDocumentFullWorkflowImportApi(
  market: Exclude<MarketCode, 'US'>,
  taskId: string,
): Promise<MarketDocumentFullImportResponse> {
  return apiJson<MarketDocumentFullImportResponse>('/api/market-reports/document-full/import', {
    method: 'POST',
    body: { market, task_id: taskId, ddl: true },
  })
}

export async function runRemainingWorkflowApi(taskId: string): Promise<WorkflowJob> {
  return apiJson<WorkflowJob>(`/api/workflow/task/${encodeURIComponent(taskId)}/run-remaining`, { method: 'POST' })
}

export async function fetchWorkflowJobApi(jobId: string): Promise<WorkflowJob> {
  return apiJson<WorkflowJob>(`/api/workflow/job/${encodeURIComponent(jobId)}`)
}

export async function showTableSourceApi(
  taskId: string,
  tableIndex: number,
): Promise<{
  table: Record<string, unknown>
  table_html?: string
  correction?: Record<string, unknown>
  pdf_page_image?: Record<string, unknown>
  page_content?: Record<string, unknown>
  markdown_excerpt?: Array<Record<string, unknown>>
  artifacts?: ArtifactsMap
}> {
  return apiJson<{
    table: Record<string, unknown>
    table_html?: string
    correction?: Record<string, unknown>
    pdf_page_image?: Record<string, unknown>
    page_content?: Record<string, unknown>
    markdown_excerpt?: Array<Record<string, unknown>>
    artifacts?: ArtifactsMap
  }>(`${PDF_API}/source/${encodeURIComponent(taskId)}/table/${encodeURIComponent(tableIndex)}`)
}

export async function fetchPageSourceApi(
  taskId: string,
  pageNum: number,
  tableIndex: number,
): Promise<Record<string, unknown>> {
  return apiJson<Record<string, unknown>>(
    `${PDF_API}/source/${encodeURIComponent(taskId)}/page/${encodeURIComponent(pageNum)}?focus_table=${encodeURIComponent(String(tableIndex || ''))}`,
  )
}

export async function saveCorrectionApi(
  taskId: string,
  tableIndex: number,
  body: { review_status?: string; table_markdown?: string; note?: string },
): Promise<void> {
  await apiJson(`${PDF_API}/source/${encodeURIComponent(taskId)}/table/${encodeURIComponent(tableIndex)}/correction`, {
    method: 'POST',
    body,
  })
}

export function getPdfUrl(taskId: string, page: number): string {
  return `${PDF_API}/pdf_page/${encodeURIComponent(taskId)}/${encodeURIComponent(page)}`
}

export function getDownloadUrl(taskId: string, variant: 'raw' | 'complete' | 'corrected' = 'raw'): string {
  const path = variant === 'complete' ? 'download_complete' : variant === 'corrected' ? 'download_corrected' : 'download'
  return `${PDF_API}/${path}/${encodeURIComponent(taskId)}`
}

export function fetchPdfArtifactJson<T>(url: string, init: ApiRequestInit = {}): Promise<T> {
  return apiJson<T>(url, init)
}

export function fetchPdfPageContentApi(
  taskId: string,
  pageNum: number,
  focusTableIndex = 0,
  init: ApiRequestInit = {},
): Promise<PageContent> {
  return apiJson<PageContent>(
    `${PDF_API}/source/${encodeURIComponent(taskId)}/page/${encodeURIComponent(pageNum)}?focus_table=${encodeURIComponent(String(focusTableIndex || ''))}`,
    init,
  )
}
