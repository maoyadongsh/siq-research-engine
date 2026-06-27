import type {
  DocumentBlocksPayload,
  DocumentFiguresPayload,
  DocumentQualityReport,
  DocumentResult,
  DocumentSourceMapPayload,
  DocumentTablesPayload,
  DocumentTaskItem,
} from './documentTypes'
import { readJsonResponse } from './pdfApi'

export const DOCUMENT_API = '/api/documents'

export async function checkDocumentParserHealth(): Promise<Record<string, unknown> | null> {
  try {
    const r = await fetch(`${DOCUMENT_API}/health`)
    return await readJsonResponse<Record<string, unknown>>(r)
  } catch {
    return null
  }
}

export async function loadDocumentQuota(): Promise<Record<string, unknown> | null> {
  try {
    const r = await fetch(`${DOCUMENT_API}/quota`)
    return await readJsonResponse<Record<string, unknown>>(r)
  } catch {
    return null
  }
}

export async function createDocumentTasks(form: FormData): Promise<{ tasks?: DocumentTaskItem[] }> {
  const r = await fetch(`${DOCUMENT_API}/tasks`, { method: 'POST', body: form })
  const d = await readJsonResponse<{ tasks?: DocumentTaskItem[]; detail?: { message?: string }; error?: string; message?: string }>(r)
  if (!r.ok) throw new Error(String(d.detail?.message || d.message || d.error || '文档上传失败'))
  return d
}

export async function createDocumentTaskFromUrl(payload: Record<string, unknown>): Promise<{ tasks?: DocumentTaskItem[] }> {
  const r = await fetch(`${DOCUMENT_API}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_type: 'url', ...payload }),
  })
  const d = await readJsonResponse<{ tasks?: DocumentTaskItem[]; detail?: { message?: string }; error?: string; message?: string }>(r)
  if (!r.ok) throw new Error(String(d.detail?.message || d.message || d.error || 'URL 解析失败'))
  return d
}

export async function loadDocumentTasks(): Promise<DocumentTaskItem[]> {
  const r = await fetch(`${DOCUMENT_API}/tasks`)
  const d = await readJsonResponse<{ tasks?: DocumentTaskItem[] }>(r)
  return d.tasks || []
}

export async function fetchDocumentStatus(taskId: string, since = 0): Promise<DocumentTaskItem & { logs?: unknown[]; log_count?: number; artifacts_ready?: boolean }> {
  const r = await fetch(`${DOCUMENT_API}/status/${encodeURIComponent(taskId)}?since=${encodeURIComponent(String(since))}`)
  const d = await readJsonResponse<DocumentTaskItem & { logs?: unknown[]; log_count?: number; artifacts_ready?: boolean; error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '文档状态查询失败'))
  return d
}

export async function fetchDocumentResult(taskId: string): Promise<DocumentResult> {
  const r = await fetch(`${DOCUMENT_API}/result/${encodeURIComponent(taskId)}`)
  const d = await readJsonResponse<DocumentResult & { error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '文档结果加载失败'))
  return d
}

export async function fetchDocumentArtifactJson<T = unknown>(taskId: string, artifact: string): Promise<T> {
  const r = await fetch(`${DOCUMENT_API}/artifact/${encodeURIComponent(taskId)}/${artifact}`)
  const d = await readJsonResponse<T & { error?: string }>(r)
  if (!r.ok) throw new Error(String((d as { error?: string }).error || '产物加载失败'))
  return d
}

export function documentArtifactUrl(taskId: string, artifact: string): string {
  return `${DOCUMENT_API}/artifact/${encodeURIComponent(taskId)}/${artifact}`
}

export function documentDownloadUrl(taskId: string): string {
  return `${DOCUMENT_API}/download/${encodeURIComponent(taskId)}`
}

export async function fetchDocumentQuality(taskId: string): Promise<DocumentQualityReport> {
  return fetchDocumentArtifactJson<DocumentQualityReport>(taskId, 'quality_report.json')
}

export async function fetchDocumentBlocks(taskId: string): Promise<DocumentBlocksPayload> {
  return fetchDocumentArtifactJson<DocumentBlocksPayload>(taskId, 'blocks.json')
}

export async function fetchDocumentTables(taskId: string): Promise<DocumentTablesPayload> {
  return fetchDocumentArtifactJson<DocumentTablesPayload>(taskId, 'tables.json')
}

export async function fetchDocumentFigures(taskId: string): Promise<DocumentFiguresPayload> {
  const r = await fetch(`${DOCUMENT_API}/figures/${encodeURIComponent(taskId)}`)
  const d = await readJsonResponse<DocumentFiguresPayload & { error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '图片产物加载失败'))
  return d
}

export async function fetchDocumentSourceMap(taskId: string): Promise<DocumentSourceMapPayload> {
  return fetchDocumentArtifactJson<DocumentSourceMapPayload>(taskId, 'source_map.json')
}

export async function runDocumentExtraction(taskId: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const r = await fetch(`${DOCUMENT_API}/extract/${encodeURIComponent(taskId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const d = await readJsonResponse<Record<string, unknown> & { error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '结构化抽取失败'))
  return d
}

export async function retryDocumentTask(taskId: string): Promise<void> {
  const r = await fetch(`${DOCUMENT_API}/retry/${encodeURIComponent(taskId)}`, { method: 'POST' })
  const d = await readJsonResponse<Record<string, unknown> & { error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '重试失败'))
}

export async function cancelDocumentTask(taskId: string): Promise<void> {
  const r = await fetch(`${DOCUMENT_API}/cancel/${encodeURIComponent(taskId)}`, { method: 'POST' })
  const d = await readJsonResponse<Record<string, unknown> & { error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '取消失败'))
}

export async function deleteDocumentTask(taskId: string): Promise<void> {
  const r = await fetch(`${DOCUMENT_API}/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
  const d = await readJsonResponse<Record<string, unknown> & { error?: string }>(r)
  if (!r.ok) throw new Error(String(d.error || '删除失败'))
}
