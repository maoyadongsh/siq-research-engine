import { apiJson } from '@/shared/api/client'
import type {
  DealAuditResponse,
  DealDecisionResponse,
  DealDetailResponse,
  DealDocumentResponse,
  DealDocumentsResponse,
  DealEvidenceResponse,
  DealEvidenceFilters,
  DealListResponse,
  DealQuery,
  DealJobStatus,
  DealPreflightResponse,
  DealWorkflowResponse,
  DeleteDealDocumentResponse,
  OpenClawImportOptions,
  OpenClawImportPayload,
  OpenClawImportResponse,
} from './dealTypes'

export interface UploadDealDocumentPayload {
  file: File
  documentType?: string
  sourceNote?: string
}

export interface BindParserTaskPayload {
  taskId: string
  artifactPath?: string
  note?: string
}

export function fetchDeals(query: DealQuery = {}, signal?: AbortSignal) {
  const params = new URLSearchParams()
  const q = query.q?.trim()
  if (q) params.set('q', q)
  if (query.status) params.set('status', query.status)
  const suffix = params.toString() ? `?${params.toString()}` : ''
  return apiJson<DealListResponse>(`/api/deals${suffix}`, { signal })
}

export function fetchDeal(dealId: string, signal?: AbortSignal) {
  return apiJson<DealDetailResponse>(`/api/deals/${encodeURIComponent(dealId)}`, { signal })
}

export function fetchDealWorkflow(dealId: string, signal?: AbortSignal) {
  return apiJson<DealWorkflowResponse>(`/api/deals/${encodeURIComponent(dealId)}/workflow`, { signal })
}

export function fetchDealPreflight(dealId: string, signal?: AbortSignal) {
  return apiJson<DealPreflightResponse>(`/api/deals/${encodeURIComponent(dealId)}/preflight`, { signal })
}

export function fetchDealDecision(dealId: string, signal?: AbortSignal) {
  return apiJson<DealDecisionResponse>(`/api/deals/${encodeURIComponent(dealId)}/decision`, { signal })
}

export function fetchDealAudit(dealId: string, signal?: AbortSignal) {
  return apiJson<DealAuditResponse>(`/api/deals/${encodeURIComponent(dealId)}/audit`, { signal })
}

function isAbortSignal(value: DealEvidenceFilters | AbortSignal | undefined): value is AbortSignal {
  return Boolean(value && typeof value === 'object' && 'aborted' in value && 'addEventListener' in value)
}

export function fetchDealEvidence(
  dealId: string,
  filtersOrSignal?: DealEvidenceFilters | AbortSignal,
  maybeSignal?: AbortSignal,
) {
  const filters = isAbortSignal(filtersOrSignal) ? undefined : filtersOrSignal
  const signal = isAbortSignal(filtersOrSignal) ? filtersOrSignal : maybeSignal
  const params = new URLSearchParams()
  const q = filters?.q?.toString().trim()
  const dimension = filters?.dimension?.toString().trim()
  const documentId = filters?.document_id?.toString().trim()
  const sourceUrl = filters?.source_url?.toString().trim()
  const limit = filters?.limit?.toString().trim()
  if (q) params.set('q', q)
  if (dimension) params.set('dimension', dimension)
  if (documentId) params.set('document_id', documentId)
  if (sourceUrl) params.set('source_url', sourceUrl)
  if (limit) params.set('limit', limit)
  const suffix = params.toString() ? `?${params.toString()}` : ''
  return apiJson<DealEvidenceResponse>(`/api/deals/${encodeURIComponent(dealId)}/evidence${suffix}`, { signal })
}

export function buildDealEvidence(dealId: string, signal?: AbortSignal) {
  return apiJson<DealEvidenceResponse>(`/api/deals/${encodeURIComponent(dealId)}/evidence/build`, {
    method: 'POST',
    signal,
  })
}

export function fetchDealDocuments(dealId: string, signal?: AbortSignal) {
  return apiJson<DealDocumentsResponse>(`/api/deals/${encodeURIComponent(dealId)}/documents`, { signal })
}

export function uploadDealDocument(
  dealId: string,
  payload: UploadDealDocumentPayload,
  signal?: AbortSignal,
) {
  const form = new FormData()
  form.set('file', payload.file)
  if (payload.documentType?.trim()) form.set('document_type', payload.documentType.trim())
  if (payload.sourceNote?.trim()) form.set('source_note', payload.sourceNote.trim())

  return apiJson<DealDocumentResponse>(`/api/deals/${encodeURIComponent(dealId)}/documents`, {
    method: 'POST',
    body: form,
    signal,
  })
}

export function fetchDealDocument(dealId: string, documentId: string, signal?: AbortSignal) {
  return apiJson<DealDocumentResponse>(
    `/api/deals/${encodeURIComponent(dealId)}/documents/${encodeURIComponent(documentId)}`,
    { signal },
  )
}

export function deleteDealDocument(dealId: string, documentId: string, signal?: AbortSignal) {
  return apiJson<DeleteDealDocumentResponse>(
    `/api/deals/${encodeURIComponent(dealId)}/documents/${encodeURIComponent(documentId)}`,
    { method: 'DELETE', signal },
  )
}

export function bindDealDocumentParserTask(
  dealId: string,
  documentId: string,
  payload: BindParserTaskPayload,
  signal?: AbortSignal,
) {
  const body: Record<string, string> = {
    task_id: payload.taskId.trim(),
  }
  const artifactPath = payload.artifactPath?.trim()
  const note = payload.note?.trim()
  if (artifactPath) body.artifact_path = artifactPath
  if (note) body.note = note

  return apiJson<DealDocumentResponse>(
    `/api/deals/${encodeURIComponent(dealId)}/documents/${encodeURIComponent(documentId)}/bind-parser-task`,
    { method: 'POST', body, signal },
  )
}

export function importOpenClawDeal(
  payload: OpenClawImportPayload,
  options: OpenClawImportOptions = {},
  signal?: AbortSignal,
) {
  const params = new URLSearchParams()
  if (options.wait) params.set('wait', 'true')
  const suffix = params.toString() ? `?${params.toString()}` : ''
  return apiJson<OpenClawImportResponse>(`/api/deals/import/openclaw${suffix}`, {
    method: 'POST',
    body: payload,
    signal,
  })
}

export function fetchDealJob(jobId: string, signal?: AbortSignal) {
  return apiJson<DealJobStatus>(`/api/deals/jobs/${encodeURIComponent(jobId)}`, { signal })
}
