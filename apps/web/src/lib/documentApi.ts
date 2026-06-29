import type {
  DocumentBlocksPayload,
  DocumentExtractionTemplatesPayload,
  DocumentFiguresPayload,
  DocumentLayoutBlocksPayload,
  DocumentMineruImportCandidatesPayload,
  DocumentQualityReport,
  DocumentResult,
  DocumentSourceMapPayload,
  DocumentTableRelationsPayload,
  DocumentTablesPayload,
  DocumentTaskItem,
  DocumentWikiImportResult,
  DocumentWorkflowStatus,
} from './documentTypes'
import { fetchWithAuth } from './fetchWithAuth'
import { readJsonResponse } from './pdfApi'

export const DOCUMENT_API = '/api/documents'
const DOCUMENT_WORKFLOW_API = '/api/workflow/document'

export async function checkDocumentParserHealth(): Promise<Record<string, unknown> | null> {
  try {
    const r = await fetchWithAuth(`${DOCUMENT_API}/health`)
    return await readJsonResponse<Record<string, unknown>>(r)
  } catch {
    return null
  }
}

export async function loadDocumentQuota(): Promise<Record<string, unknown> | null> {
  try {
    const r = await fetchWithAuth(`${DOCUMENT_API}/quota`)
    return await readJsonResponse<Record<string, unknown>>(r)
  } catch {
    return null
  }
}

export async function createDocumentTasks(form: FormData): Promise<{ tasks?: DocumentTaskItem[] }> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/tasks`, { method: 'POST', body: form })
  const d = await readJsonResponse<{ tasks?: DocumentTaskItem[]; detail?: { message?: string }; error?: string; message?: string }>(r)
  if (!r.ok) throw new Error(String(d.detail?.message || d.message || d.error || '文档上传失败'))
  return d
}

export async function createDocumentTaskFromUrl(payload: Record<string, unknown>): Promise<{ tasks?: DocumentTaskItem[] }> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_type: 'url', ...payload }),
  })
  const d = await readJsonResponse<{ tasks?: DocumentTaskItem[]; detail?: { message?: string }; error?: string; message?: string }>(r)
  if (!r.ok) throw new Error(String(d.detail?.message || d.message || d.error || 'URL 解析失败'))
  return d
}

export async function importDocumentFromMineru(payload: Record<string, unknown>): Promise<{ task?: DocumentTaskItem }> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/import/mineru`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const d = await readJsonResponse<{ task?: DocumentTaskItem; detail?: { message?: string }; error?: string; message?: string }>(r)
  if (!r.ok) throw new Error(String(d.detail?.message || d.message || d.error || 'MinerU 产物导入失败'))
  return d
}

export async function fetchMineruImportCandidates(limit = 50): Promise<DocumentMineruImportCandidatesPayload> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/import/mineru/candidates?limit=${encodeURIComponent(String(limit))}`)
  const d = await readJsonResponse<DocumentMineruImportCandidatesPayload & { detail?: { message?: string }; error?: string; message?: string }>(r)
  if (!r.ok) throw new Error(String(d.detail?.message || d.message || d.error || 'MinerU 候选目录加载失败'))
  return d
}

export async function loadDocumentTasks(): Promise<DocumentTaskItem[]> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/tasks`)
  const d = await readJsonResponse<{ tasks?: DocumentTaskItem[] }>(r)
  return d.tasks || []
}

export async function fetchDocumentStatus(taskId: string, since = 0): Promise<DocumentTaskItem & { logs?: unknown[]; log_count?: number; artifacts_ready?: boolean }> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/status/${encodeURIComponent(taskId)}?since=${encodeURIComponent(String(since))}`)
  const d = await readJsonResponse<DocumentTaskItem & { logs?: unknown[]; log_count?: number; artifacts_ready?: boolean; error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '文档状态查询失败'))
  return d
}

export async function fetchDocumentResult(taskId: string): Promise<DocumentResult> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/result/${encodeURIComponent(taskId)}`)
  const d = await readJsonResponse<DocumentResult & { error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '文档结果加载失败'))
  return d
}

export async function fetchDocumentArtifactJson<T = unknown>(taskId: string, artifact: string): Promise<T> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/artifact/${encodeURIComponent(taskId)}/${artifact}`)
  const d = await readJsonResponse<T & { error?: string }>(r)
  if (!r.ok) throw new Error(String((d as { error?: string }).error || '产物加载失败'))
  return d
}

export function documentArtifactUrl(taskId: string, artifact: string): string {
  return `${DOCUMENT_API}/artifact/${encodeURIComponent(taskId)}/${artifact}`
}

export function documentSourcePageImageUrl(taskId: string, pageNumber: number): string {
  return `${DOCUMENT_API}/source/${encodeURIComponent(taskId)}/page-image/${encodeURIComponent(String(pageNumber))}`
}

export function documentDownloadUrl(taskId: string): string {
  return `${DOCUMENT_API}/download/${encodeURIComponent(taskId)}`
}

export async function openDocumentResource(url: string, filename?: string): Promise<void> {
  const response = await fetchWithAuth(url)
  if (!response.ok) {
    throw new Error(`打开产物失败：HTTP ${response.status}`)
  }
  const blob = await response.blob()
  const objectUrl = window.URL.createObjectURL(blob)
  const shouldDownload = /[?&]download=/.test(url) || /\/download(?:\/|$)/.test(url) || /\.zip(?:$|\?)/i.test(url)
  const link = document.createElement('a')
  link.href = objectUrl
  link.target = '_blank'
  link.rel = 'noreferrer'
  if (shouldDownload && filename) link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => window.URL.revokeObjectURL(objectUrl), 60_000)
}

export function documentBatchDownloadUrl(): string {
  return `${DOCUMENT_API}/download/batch`
}

export async function fetchDocumentQuality(taskId: string): Promise<DocumentQualityReport> {
  return fetchDocumentArtifactJson<DocumentQualityReport>(taskId, 'quality_report.json')
}

export async function fetchDocumentBlocks(taskId: string): Promise<DocumentBlocksPayload> {
  return fetchDocumentArtifactJson<DocumentBlocksPayload>(taskId, 'blocks.json')
}

export async function fetchDocumentLayoutBlocks(taskId: string): Promise<DocumentLayoutBlocksPayload> {
  return fetchDocumentArtifactJson<DocumentLayoutBlocksPayload>(taskId, 'layout_blocks.json')
}

export async function fetchDocumentTables(taskId: string): Promise<DocumentTablesPayload> {
  return fetchDocumentArtifactJson<DocumentTablesPayload>(taskId, 'tables.json')
}

export async function fetchDocumentFigures(taskId: string): Promise<DocumentFiguresPayload> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/figures/${encodeURIComponent(taskId)}`)
  const d = await readJsonResponse<DocumentFiguresPayload & { error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '图片产物加载失败'))
  return d
}

export async function fetchDocumentSourceMap(taskId: string): Promise<DocumentSourceMapPayload> {
  return fetchDocumentArtifactJson<DocumentSourceMapPayload>(taskId, 'source_map.json')
}

export async function fetchDocumentTableRelations(taskId: string): Promise<DocumentTableRelationsPayload> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/table-relations/${encodeURIComponent(taskId)}`)
  const d = await readJsonResponse<DocumentTableRelationsPayload & { error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '表格关系加载失败'))
  return d
}

export async function fetchDocumentExtractionTemplates(): Promise<DocumentExtractionTemplatesPayload> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/extraction/templates`)
  const d = await readJsonResponse<DocumentExtractionTemplatesPayload & { error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '抽取模板加载失败'))
  return d
}

export async function reviewDocumentTableRelation(
  taskId: string,
  relationId: string,
  body: { review_status?: string; reviewStatus?: string; note?: string },
): Promise<Record<string, unknown>> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/table-relations/${encodeURIComponent(taskId)}/${encodeURIComponent(relationId)}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const d = await readJsonResponse<Record<string, unknown> & { error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '表格关系复核失败'))
  return d
}

export async function runDocumentExtraction(taskId: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/extract/${encodeURIComponent(taskId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const d = await readJsonResponse<Record<string, unknown> & { error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '结构化抽取失败'))
  return d
}

export async function retryDocumentTask(taskId: string): Promise<void> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/retry/${encodeURIComponent(taskId)}`, { method: 'POST' })
  const d = await readJsonResponse<Record<string, unknown> & { error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '重试失败'))
}

export async function cancelDocumentTask(taskId: string): Promise<void> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/cancel/${encodeURIComponent(taskId)}`, { method: 'POST' })
  const d = await readJsonResponse<Record<string, unknown> & { error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '取消失败'))
}

export async function deleteDocumentTask(taskId: string): Promise<void> {
  const r = await fetchWithAuth(`${DOCUMENT_API}/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
  const d = await readJsonResponse<Record<string, unknown> & { error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '删除失败'))
}

export async function fetchDocumentWorkflowStatus(taskId: string, collection = 'default'): Promise<DocumentWorkflowStatus> {
  const params = new URLSearchParams({ collection })
  const r = await fetchWithAuth(`${DOCUMENT_WORKFLOW_API}/${encodeURIComponent(taskId)}/status?${params.toString()}`)
  const d = await readJsonResponse<DocumentWorkflowStatus & { error?: string; detail?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || d.detail || '文档入库状态加载失败'))
  return d
}

export async function importDocumentToWiki(taskId: string, collection = 'default'): Promise<DocumentWikiImportResult> {
  const params = new URLSearchParams({ collection })
  const r = await fetchWithAuth(`${DOCUMENT_WORKFLOW_API}/${encodeURIComponent(taskId)}/wiki-import?${params.toString()}`, { method: 'POST' })
  const d = await readJsonResponse<DocumentWikiImportResult & { error?: string; detail?: string | { message?: string } }>(r)
  const detail = typeof d.detail === 'string' ? d.detail : d.detail?.message
  if (!r.ok) throw new Error(String(d.error || detail || '导入 Wiki 失败'))
  return d
}

export async function importDocumentToDatabase(taskId: string, collection = 'default'): Promise<DocumentWikiImportResult> {
  const params = new URLSearchParams({ collection })
  const r = await fetchWithAuth(`${DOCUMENT_WORKFLOW_API}/${encodeURIComponent(taskId)}/db-import?${params.toString()}`, { method: 'POST' })
  const d = await readJsonResponse<DocumentWikiImportResult & { error?: string; detail?: string | { message?: string } }>(r)
  const detail = typeof d.detail === 'string' ? d.detail : d.detail?.message
  if (!r.ok) throw new Error(String(d.error || detail || '导入 PostgreSQL 失败'))
  return d
}

export async function buildDocumentSemanticChunks(taskId: string, collection = 'default', milvus = false): Promise<DocumentWikiImportResult> {
  const params = new URLSearchParams({ collection, milvus: String(milvus) })
  const r = await fetchWithAuth(`${DOCUMENT_WORKFLOW_API}/${encodeURIComponent(taskId)}/semantic?${params.toString()}`, { method: 'POST' })
  const d = await readJsonResponse<DocumentWikiImportResult & { error?: string; detail?: string | { message?: string } }>(r)
  const detail = typeof d.detail === 'string' ? d.detail : d.detail?.message
  if (!r.ok) throw new Error(String(d.error || detail || (milvus ? '写入 Milvus 失败' : '生成语义 chunks 失败')))
  return d
}
