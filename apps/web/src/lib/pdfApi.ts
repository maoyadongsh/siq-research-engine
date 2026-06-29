import type { ArtifactInfo, ArtifactsMap, DownloadedPdf, FinancialResult, WorkflowJob, WorkflowStatus } from './pdfTypes'

export const PDF_API = '/api/pdf'

export async function readJsonResponse<T = unknown>(response: Response): Promise<T> {
  const text = await response.text()
  const trimmed = text.trim()
  if (!trimmed) return {} as T
  try {
    return JSON.parse(trimmed) as T
  } catch {
    const preview = trimmed.replace(/\s+/g, ' ').slice(0, 120)
    if (/^<!doctype html/i.test(trimmed) || /^<html[\s>]/i.test(trimmed)) {
      throw new Error(`接口返回了 HTML 页面，未命中后端 API（HTTP ${response.status}）。请检查 /api 代理或后端 18081。`)
    }
    throw new Error(`接口返回非 JSON 内容（HTTP ${response.status}）：${preview}`)
  }
}

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
  const WIKI_INPUT_ARTIFACTS = [
    'result.md',
    'result_complete.md',
    'document_full.json',
    'content_list_enhanced.json',
    'financial_data.json',
    'financial_checks.json',
    'quality_report.json',
    'table_relations.json',
    'table_index.json',
  ]
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

export async function loadDownloadedReports(text: string, market?: 'CN' | 'HK' | 'US' | 'EU' | 'KR' | 'JP'): Promise<{ reports: DownloadedPdf[] }> {
  const params = new URLSearchParams({ q: text.trim(), limit: '120' })
  if (market) params.set('market', market)
  const r = await fetch(`/api/downloads/reports?${params.toString()}`)
  if (!r.ok) throw new Error(String(r.status))
  const data = await r.json()
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
    const r = await fetch(`${PDF_API}/health`)
    const d = await r.json()
    return { mineru: !!d.mineru, vlm: !!d.vlm, submit_ready: !!d.submit_ready, warning: d.warning || undefined }
  } catch {
    return null
  }
}

export async function loadTasks(): Promise<Record<string, unknown>[]> {
  const r = await fetch('/api/pdf/tasks')
  const d = await r.json()
  return d.tasks || []
}

export async function downloadedReportToFile(report: DownloadedPdf): Promise<File> {
  if (report.isPdf === false) throw new Error('该文件不是 PDF，当前 PDF 解析器暂不支持直接解析')
  const url = report.url || `/api/downloads/report-file?path=${encodeURIComponent(report.relativePath)}`
  const r = await fetch(url)
  if (!r.ok) throw new Error('读取已下载 PDF 失败')
  const blob = await r.blob()
  const lastModified = new Date(report.mtime).getTime()
  return new File([blob], report.filename, {
    type: blob.type || 'application/pdf',
    lastModified: Number.isFinite(lastModified) ? lastModified : Date.now(),
  })
}

export async function linkDownloadedReport(report: DownloadedPdf): Promise<void> {
  await fetch('/api/workspace/downloads/link', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ relativePath: report.relativePath }),
  }).catch(() => null)
}

export async function fetchStatus(taskId: string, since: number): Promise<Record<string, unknown>> {
  const r = await fetch(`${PDF_API}/status/${encodeURIComponent(taskId)}?since=${since}`)
  const d = await r.json()
  if (!r.ok) throw new Error(String(d.error || '状态查询失败'))
  return d
}

export async function cancelTaskApi(taskId: string): Promise<{ success: boolean; upstream_cancelled?: boolean }> {
  const r = await fetch(`${PDF_API}/cancel/${encodeURIComponent(taskId)}`, { method: 'POST' })
  return r.json()
}

export async function deleteTaskApi(taskId: string): Promise<void> {
  const r = await fetch(`${PDF_API}/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
  if (!r.ok) {
    const d = await r.json()
    throw new Error(String(d.error || '删除失败'))
  }
}

export async function refetchTaskApi(taskId: string): Promise<void> {
  const r = await fetch(`${PDF_API}/refetch/${encodeURIComponent(taskId)}`, { method: 'POST' })
  if (!r.ok) {
    const d = await r.json()
    throw new Error(String(d.error || '重新拉取失败'))
  }
}

export async function reparseTaskApi(taskId: string): Promise<{ task_id: string; filename: string }> {
  const r = await fetch(`${PDF_API}/reparse/${encodeURIComponent(taskId)}`, { method: 'POST' })
  const d = await r.json()
  if (!r.ok) throw new Error(String(d.error || '重新解析失败'))
  return d
}

export async function uploadPdfs(form: FormData): Promise<Record<string, unknown>> {
  const r = await fetch('/api/pdf/upload', { method: 'POST', body: form })
  const d = await r.json()
  if (!r.ok) {
    const err: Error & { response?: Record<string, unknown>; status?: number } = new Error(
      String(d.message || d.error || d.detail?.message || '上传失败'),
    )
    err.response = d
    err.status = r.status
    throw err
  }
  return d
}

export async function fetchResultApi(taskId: string): Promise<{ artifacts?: ArtifactsMap; markdown?: string }> {
  const r = await fetch(`${PDF_API}/result/${encodeURIComponent(taskId)}`)
  return r.json()
}

export async function fetchQualityApi(taskId: string): Promise<{ quality?: Record<string, unknown> }> {
  const r = await fetch(`${PDF_API}/quality/${encodeURIComponent(taskId)}`)
  return r.json()
}

export async function fetchFinancialApi(taskId: string): Promise<FinancialResult> {
  const r = await fetch(`${PDF_API}/financial/${encodeURIComponent(taskId)}`)
  return r.json()
}

export async function loadWorkflowStatusApi(taskId: string): Promise<WorkflowStatus> {
  const r = await fetch(`/api/workflow/task/${encodeURIComponent(taskId)}/status`)
  const d = await readJsonResponse<WorkflowStatus>(r)
  if (!r.ok) throw new Error(typeof d.error === 'string' ? d.error : '状态查询失败')
  return d
}

export async function runWorkflowStepApi(
  taskId: string,
  step: 'wiki-import' | 'wiki-import-generic' | 'semantic' | 'semantic-generic' | 'db-import',
): Promise<WorkflowJob> {
  const r = await fetch(`/api/workflow/task/${encodeURIComponent(taskId)}/${step}`, { method: 'POST' })
  const d = await readJsonResponse<WorkflowJob>(r)
  if (!r.ok) throw new Error(typeof d.error === 'string' ? d.error : JSON.stringify(d))
  return d
}

export async function runRemainingWorkflowApi(taskId: string): Promise<WorkflowJob> {
  const r = await fetch(`/api/workflow/task/${encodeURIComponent(taskId)}/run-remaining`, { method: 'POST' })
  const d = await readJsonResponse<WorkflowJob>(r)
  if (!r.ok) throw new Error(typeof d.error === 'string' ? d.error : JSON.stringify(d))
  return d
}

export async function fetchWorkflowJobApi(jobId: string): Promise<WorkflowJob> {
  const r = await fetch(`/api/workflow/job/${encodeURIComponent(jobId)}`)
  const d = await readJsonResponse<WorkflowJob>(r)
  if (!r.ok) throw new Error(typeof d.error === 'string' ? d.error : '任务状态查询失败')
  return d
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
  const r = await fetch(`${PDF_API}/source/${encodeURIComponent(taskId)}/table/${encodeURIComponent(tableIndex)}`)
  const d = await r.json()
  if (!r.ok) throw new Error(String(d.error || '溯源失败'))
  return d
}

export async function fetchPageSourceApi(
  taskId: string,
  pageNum: number,
  tableIndex: number,
): Promise<Record<string, unknown>> {
  const r = await fetch(
    `${PDF_API}/source/${encodeURIComponent(taskId)}/page/${encodeURIComponent(pageNum)}?focus_table=${encodeURIComponent(String(tableIndex || ''))}`,
  )
  const d = await r.json()
  if (!r.ok) throw new Error(String(d.error || '加载失败'))
  return d
}

export async function saveCorrectionApi(
  taskId: string,
  tableIndex: number,
  body: { review_status?: string; table_markdown?: string; note?: string },
): Promise<void> {
  const r = await fetch(`${PDF_API}/source/${encodeURIComponent(taskId)}/table/${encodeURIComponent(tableIndex)}/correction`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const d = await r.json()
  if (!r.ok) throw new Error(String(d.error || '保存失败'))
}

export function getPdfUrl(taskId: string, page: number): string {
  return `${PDF_API}/pdf_page/${encodeURIComponent(taskId)}/${encodeURIComponent(page)}`
}

export function getDownloadUrl(taskId: string, variant: 'raw' | 'complete' | 'corrected' = 'raw'): string {
  const path = variant === 'complete' ? 'download_complete' : variant === 'corrected' ? 'download_corrected' : 'download'
  return `${PDF_API}/${path}/${encodeURIComponent(taskId)}`
}
