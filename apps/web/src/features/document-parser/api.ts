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
} from '../../lib/documentTypes'
import { apiBlob, apiJson } from '../../shared/api/client'

export const DOCUMENT_API = '/api/documents'
const DOCUMENT_WORKFLOW_API = '/api/workflow/document'

export async function checkDocumentParserHealth(): Promise<Record<string, unknown> | null> {
  try {
    return await apiJson<Record<string, unknown>>(`${DOCUMENT_API}/health`)
  } catch {
    return null
  }
}

export async function loadDocumentQuota(): Promise<Record<string, unknown> | null> {
  try {
    return await apiJson<Record<string, unknown>>(`${DOCUMENT_API}/quota`)
  } catch {
    return null
  }
}

export async function createDocumentTasks(form: FormData): Promise<{ tasks?: DocumentTaskItem[] }> {
  return apiJson<{ tasks?: DocumentTaskItem[] }>(`${DOCUMENT_API}/tasks`, { method: 'POST', body: form })
}

export async function createDocumentTaskFromUrl(payload: Record<string, unknown>): Promise<{ tasks?: DocumentTaskItem[] }> {
  return apiJson<{ tasks?: DocumentTaskItem[] }>(`${DOCUMENT_API}/tasks`, {
    method: 'POST',
    body: { source_type: 'url', ...payload },
  })
}

export async function importDocumentFromMineru(payload: Record<string, unknown>): Promise<{ task?: DocumentTaskItem }> {
  return apiJson<{ task?: DocumentTaskItem }>(`${DOCUMENT_API}/import/mineru`, {
    method: 'POST',
    body: payload,
  })
}

export async function fetchMineruImportCandidates(limit = 50): Promise<DocumentMineruImportCandidatesPayload> {
  return apiJson<DocumentMineruImportCandidatesPayload>(`${DOCUMENT_API}/import/mineru/candidates?limit=${encodeURIComponent(String(limit))}`)
}

export async function loadDocumentTasks(): Promise<DocumentTaskItem[]> {
  const d = await apiJson<{ tasks?: DocumentTaskItem[] }>(`${DOCUMENT_API}/tasks`)
  return d.tasks || []
}

export async function fetchDocumentStatus(taskId: string, since = 0, signal?: AbortSignal): Promise<DocumentTaskItem & { logs?: unknown[]; log_count?: number; artifacts_ready?: boolean }> {
  return apiJson<DocumentTaskItem & { logs?: unknown[]; log_count?: number; artifacts_ready?: boolean }>(`${DOCUMENT_API}/status/${encodeURIComponent(taskId)}?since=${encodeURIComponent(String(since))}`, { signal })
}

export async function fetchDocumentResult(taskId: string, signal?: AbortSignal): Promise<DocumentResult> {
  return apiJson<DocumentResult>(`${DOCUMENT_API}/result/${encodeURIComponent(taskId)}`, { signal })
}

export async function fetchDocumentArtifactJson<T = unknown>(taskId: string, artifact: string, signal?: AbortSignal): Promise<T> {
  return apiJson<T>(`${DOCUMENT_API}/artifact/${encodeURIComponent(taskId)}/${artifact}`, { signal })
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
  const blob = await apiBlob(url)
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

export async function fetchDocumentQuality(taskId: string, signal?: AbortSignal): Promise<DocumentQualityReport> {
  return fetchDocumentArtifactJson<DocumentQualityReport>(taskId, 'quality_report.json', signal)
}

export async function fetchDocumentBlocks(taskId: string, signal?: AbortSignal): Promise<DocumentBlocksPayload> {
  return fetchDocumentArtifactJson<DocumentBlocksPayload>(taskId, 'blocks.json', signal)
}

export async function fetchDocumentLayoutBlocks(taskId: string, signal?: AbortSignal): Promise<DocumentLayoutBlocksPayload> {
  return fetchDocumentArtifactJson<DocumentLayoutBlocksPayload>(taskId, 'layout_blocks.json', signal)
}

export async function fetchDocumentTables(taskId: string, signal?: AbortSignal): Promise<DocumentTablesPayload> {
  return fetchDocumentArtifactJson<DocumentTablesPayload>(taskId, 'tables.json', signal)
}

export async function fetchDocumentFigures(taskId: string, signal?: AbortSignal): Promise<DocumentFiguresPayload> {
  return apiJson<DocumentFiguresPayload>(`${DOCUMENT_API}/figures/${encodeURIComponent(taskId)}`, { signal })
}

export async function fetchDocumentSourceMap(taskId: string, signal?: AbortSignal): Promise<DocumentSourceMapPayload> {
  return fetchDocumentArtifactJson<DocumentSourceMapPayload>(taskId, 'source_map.json', signal)
}

export async function fetchDocumentTableRelations(taskId: string, signal?: AbortSignal): Promise<DocumentTableRelationsPayload> {
  return apiJson<DocumentTableRelationsPayload>(`${DOCUMENT_API}/table-relations/${encodeURIComponent(taskId)}`, { signal })
}

export async function fetchDocumentExtractionTemplates(): Promise<DocumentExtractionTemplatesPayload> {
  return apiJson<DocumentExtractionTemplatesPayload>(`${DOCUMENT_API}/extraction/templates`)
}

export async function reviewDocumentTableRelation(
  taskId: string,
  relationId: string,
  body: { review_status?: string; reviewStatus?: string; note?: string },
): Promise<Record<string, unknown>> {
  return apiJson<Record<string, unknown>>(`${DOCUMENT_API}/table-relations/${encodeURIComponent(taskId)}/${encodeURIComponent(relationId)}/review`, {
    method: 'POST',
    body,
  })
}

export async function runDocumentExtraction(taskId: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  return apiJson<Record<string, unknown>>(`${DOCUMENT_API}/extract/${encodeURIComponent(taskId)}`, {
    method: 'POST',
    body,
  })
}

export async function retryDocumentTask(taskId: string): Promise<void> {
  await apiJson<Record<string, unknown>>(`${DOCUMENT_API}/retry/${encodeURIComponent(taskId)}`, { method: 'POST' })
}

export async function cancelDocumentTask(taskId: string): Promise<void> {
  await apiJson<Record<string, unknown>>(`${DOCUMENT_API}/cancel/${encodeURIComponent(taskId)}`, { method: 'POST' })
}

export async function deleteDocumentTask(taskId: string): Promise<void> {
  await apiJson<Record<string, unknown>>(`${DOCUMENT_API}/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
}

export async function fetchDocumentWorkflowStatus(taskId: string, collection = 'default', signal?: AbortSignal): Promise<DocumentWorkflowStatus> {
  const params = new URLSearchParams({ collection })
  return apiJson<DocumentWorkflowStatus>(`${DOCUMENT_WORKFLOW_API}/${encodeURIComponent(taskId)}/status?${params.toString()}`, { signal })
}

export async function importDocumentToWiki(taskId: string, collection = 'default'): Promise<DocumentWikiImportResult> {
  const params = new URLSearchParams({ collection })
  return apiJson<DocumentWikiImportResult>(`${DOCUMENT_WORKFLOW_API}/${encodeURIComponent(taskId)}/wiki-import?${params.toString()}`, { method: 'POST' })
}

export async function importDocumentToDatabase(taskId: string, collection = 'default'): Promise<DocumentWikiImportResult> {
  const params = new URLSearchParams({ collection })
  return apiJson<DocumentWikiImportResult>(`${DOCUMENT_WORKFLOW_API}/${encodeURIComponent(taskId)}/db-import?${params.toString()}`, { method: 'POST' })
}

export async function buildDocumentSemanticChunks(taskId: string, collection = 'default', milvus = false): Promise<DocumentWikiImportResult> {
  const params = new URLSearchParams({ collection, milvus: String(milvus) })
  return apiJson<DocumentWikiImportResult>(`${DOCUMENT_WORKFLOW_API}/${encodeURIComponent(taskId)}/semantic?${params.toString()}`, { method: 'POST' })
}
