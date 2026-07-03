import { ApiError, apiJson } from '@/shared/api/client'
import type {
  DealAuditResponse,
  DealAgentsResponse,
  DealAgentTaskDryRunResponse,
  DealDecisionHumanConfirmationPayload,
  DealDecisionHumanConfirmationUpdateResponse,
  DealDecisionResponse,
  DealDetailResponse,
  DealDocumentResponse,
  DealDocumentsResponse,
  DealDisputesResponse,
  DealEvidenceIngestDryRunResponse,
  DealEvidenceResponse,
  DealEvidenceFilters,
  DealManifestResponse,
  DealListResponse,
  DealPhaseArtifactsResponse,
  DealQuery,
  DealJobStatus,
  DealPreflightResponse,
  DealR1AgentReportsResponse,
  DealR2AgentReportsResponse,
  DealR3ReviewSummaryResponse,
  DealReportDetailResponse,
  DealReportsResponse,
  DealStatusResponse,
  DealStartupRetrievalResponse,
  DealWorkflowResponse,
  DealWorkflowRunR1AgentDryRunResponse,
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

export function fetchDealStatus(dealId: string, signal?: AbortSignal) {
  return apiJson<DealStatusResponse>(`/api/deals/${encodeURIComponent(dealId)}/status`, { signal })
}

export function fetchDealAgents(dealId: string, signal?: AbortSignal) {
  return apiJson<DealAgentsResponse>(`/api/deals/${encodeURIComponent(dealId)}/agents`, { signal })
}

export function fetchDealWorkflow(dealId: string, signal?: AbortSignal) {
  return apiJson<DealWorkflowResponse>(`/api/deals/${encodeURIComponent(dealId)}/workflow`, { signal })
}

export function fetchDealPreflight(dealId: string, signal?: AbortSignal) {
  return apiJson<DealPreflightResponse>(`/api/deals/${encodeURIComponent(dealId)}/preflight`, { signal })
}

export function fetchDealDisputes(dealId: string, signal?: AbortSignal) {
  return apiJson<DealDisputesResponse>(`/api/deals/${encodeURIComponent(dealId)}/disputes`, { signal })
}

export function fetchDealPhaseArtifacts(dealId: string, signal?: AbortSignal) {
  return apiJson<DealPhaseArtifactsResponse>(
    `/api/deals/${encodeURIComponent(dealId)}/phase-artifacts`,
    { signal },
  )
}

export function fetchDealDecision(dealId: string, signal?: AbortSignal) {
  return apiJson<DealDecisionResponse>(`/api/deals/${encodeURIComponent(dealId)}/decision`, { signal })
}

export function postDealDecisionHumanConfirmation(
  dealId: string,
  payload: DealDecisionHumanConfirmationPayload,
  signal?: AbortSignal,
) {
  return apiJson<DealDecisionHumanConfirmationUpdateResponse>(
    `/api/deals/${encodeURIComponent(dealId)}/decision/human-confirmation`,
    {
      method: 'POST',
      body: {
        dry_run: true,
        ...payload,
      },
      signal,
    },
  )
}

export function fetchDealAudit(dealId: string, signal?: AbortSignal) {
  return apiJson<DealAuditResponse>(`/api/deals/${encodeURIComponent(dealId)}/audit`, { signal })
}

export function fetchDealManifest(dealId: string, signal?: AbortSignal) {
  return apiJson<DealManifestResponse>(`/api/deals/${encodeURIComponent(dealId)}/manifest`, { signal })
}

export function fetchDealReports(dealId: string, signal?: AbortSignal) {
  return apiJson<DealReportsResponse>(`/api/deals/${encodeURIComponent(dealId)}/reports`, { signal })
}

export function fetchDealR1AgentReports(dealId: string, signal?: AbortSignal) {
  return apiJson<DealR1AgentReportsResponse>(
    `/api/deals/${encodeURIComponent(dealId)}/reports/r1-agents`,
    { signal },
  )
}

export function fetchDealR2AgentReports(dealId: string, signal?: AbortSignal) {
  return apiJson<DealR2AgentReportsResponse>(
    `/api/deals/${encodeURIComponent(dealId)}/reports/r2-agents`,
    { signal },
  )
}

export function fetchDealR3ReviewSummary(dealId: string, signal?: AbortSignal) {
  return apiJson<DealR3ReviewSummaryResponse>(
    `/api/deals/${encodeURIComponent(dealId)}/reports/r3-review`,
    { signal },
  )
}

export function fetchDealReport(dealId: string, reportPath: string, signal?: AbortSignal) {
  const encodedReportPath = reportPath.split('/').map((part) => encodeURIComponent(part)).join('/')
  return apiJson<DealReportDetailResponse>(
    `/api/deals/${encodeURIComponent(dealId)}/reports/${encodedReportPath}`,
    { signal },
  )
}

export function generateDealStartupRetrieval(
  dealId: string,
  profileId: string,
  payload: { round_name?: string; query?: string; limit?: number } = {},
  signal?: AbortSignal,
) {
  return apiJson<DealStartupRetrievalResponse>(
    `/api/deals/${encodeURIComponent(dealId)}/agents/${encodeURIComponent(profileId)}/startup-retrieval`,
    {
      method: 'POST',
      body: {
        round_name: payload.round_name || 'R1',
        query: payload.query,
        limit: payload.limit ?? 10,
      },
      signal,
    },
  )
}

export function fetchDealStartupRetrieval(dealId: string, profileId: string, signal?: AbortSignal) {
  return apiJson<DealStartupRetrievalResponse>(
    `/api/deals/${encodeURIComponent(dealId)}/agents/${encodeURIComponent(profileId)}/startup-retrieval`,
    { signal },
  )
}

export function dryRunDealAgentTask(
  dealId: string,
  profileId: string,
  payload: { round_name?: string } = {},
  signal?: AbortSignal,
) {
  return apiJson<DealAgentTaskDryRunResponse>(
    `/api/deals/${encodeURIComponent(dealId)}/agents/${encodeURIComponent(profileId)}/dry-run`,
    {
      method: 'POST',
      body: { round_name: payload.round_name || 'R1' },
      signal,
    },
  )
}

export function dryRunDealWorkflowR1Agent(
  dealId: string,
  profileId: string,
  payload: { round_name?: string } = {},
  signal?: AbortSignal,
) {
  return apiJson<DealWorkflowRunR1AgentDryRunResponse>(
    `/api/deals/${encodeURIComponent(dealId)}/workflow/run-r1-agent`,
    {
      method: 'POST',
      body: {
        profile_id: profileId,
        round_name: payload.round_name || 'R1',
        dry_run: true,
      },
      signal,
    },
  )
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

export async function dryRunDealEvidenceIngest(dealId: string, signal?: AbortSignal) {
  const encodedDealId = encodeURIComponent(dealId)
  const paths = [
    `/api/deals/${encodedDealId}/evidence/ingest/dry-run`,
    `/api/deals/${encodedDealId}/evidence/ingest-dry-run`,
  ]
  const attempts = paths.flatMap((path) => [
    { path, method: 'POST' },
    { path, method: 'GET' },
  ])
  let lastError: unknown

  for (const attempt of attempts) {
    try {
      return await apiJson<DealEvidenceIngestDryRunResponse>(attempt.path, {
        method: attempt.method,
        signal,
      })
    } catch (err) {
      if (signal?.aborted) throw err
      if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
        lastError = err
        continue
      }
      throw err
    }
  }

  throw lastError instanceof Error ? lastError : new Error('Evidence ingest dry-run 接口不可用')
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
