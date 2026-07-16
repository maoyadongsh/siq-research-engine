import { apiJson, apiStreamFetch } from '@/shared/api/client'
import {
  bindDealDocumentParserTask,
  buildDealEvidence,
  deleteDealDocument,
  dryRunDealEvidenceIngest,
  dryRunDealWorkflowR1Agent,
  fetchDealAgents,
  fetchDealAudit,
  fetchDealDecision,
  fetchDealDisputes,
  fetchDealDocuments,
  fetchDealEvidence,
  fetchDealPhaseArtifacts,
  fetchDealPreflight,
  fetchDealR2AgentReports,
  fetchDealR3ReviewSummary,
  fetchDealStartupRetrieval,
  fetchDealWorkflow,
  finalizeDealWorkflowR4,
  generateDealStartupRetrieval,
  generateDealWorkflowDisputeRulings,
  identifyDealWorkflowDisputes,
  postDealDecisionHumanConfirmation,
  runDealWorkflowR1Serial,
  runDealWorkflowR2,
  runDealWorkflowR3,
  uploadDealDocument,
} from '@/lib/dealApi'
import type {
  DealDecisionHumanConfirmationPayload,
  DealDecisionHumanConfirmationUpdateResponse,
  DealDetailResponse,
  DealEvidenceMilvusIndexResponse,
  DealListResponse,
  PrimaryMarketMaterialParseStatusResponse,
  PrimaryMarketMaterialResponse,
  PrimaryMarketMaterialsResponse,
  PrimaryMarketWikiProjection,
  DealQuery,
  DealStartupReceipt,
  DealStatusResponse,
  DealWorkflowRunR1AgentDryRunResponse,
  DealWorkflowRunR1SerialResponse,
} from '@/lib/dealTypes'
import type { AgentAttachment, ChatSessionSummary, HistoryRecord } from '@/lib/useAgentChat'
import type { BadgeTone, MeetingEvent, MeetingEventType, MeetingQualityResult } from '@/features/primary-market/primaryMarketViewModel'

export {
  bindDealDocumentParserTask as bindPrimaryMarketDocumentParserTask,
  buildDealEvidence as buildPrimaryMarketEvidence,
  deleteDealDocument as deletePrimaryMarketDocument,
  dryRunDealEvidenceIngest as dryRunPrimaryMarketEvidenceIngest,
  dryRunDealWorkflowR1Agent as dryRunPrimaryMarketWorkflowR1Agent,
  fetchDealAgents as fetchPrimaryMarketAgents,
  fetchDealAudit as fetchPrimaryMarketAudit,
  fetchDealDecision as fetchPrimaryMarketDecision,
  fetchDealDisputes as fetchPrimaryMarketDisputes,
  fetchDealDocuments as fetchPrimaryMarketDocuments,
  fetchDealEvidence as fetchPrimaryMarketEvidence,
  fetchDealPhaseArtifacts as fetchPrimaryMarketPhaseArtifacts,
  fetchDealPreflight as fetchPrimaryMarketPreflight,
  fetchDealR2AgentReports as fetchPrimaryMarketR2AgentReports,
  fetchDealR3ReviewSummary as fetchPrimaryMarketR3ReviewSummary,
  fetchDealStartupRetrieval as fetchPrimaryMarketStartupRetrieval,
  fetchDealWorkflow as fetchPrimaryMarketWorkflow,
  finalizeDealWorkflowR4 as finalizePrimaryMarketWorkflowR4,
  generateDealStartupRetrieval as generatePrimaryMarketStartupRetrieval,
  generateDealWorkflowDisputeRulings as generatePrimaryMarketWorkflowDisputeRulings,
  identifyDealWorkflowDisputes as identifyPrimaryMarketWorkflowDisputes,
  postDealDecisionHumanConfirmation as postPrimaryMarketDecisionHumanConfirmation,
  runDealWorkflowR1Serial as runPrimaryMarketWorkflowR1Serial,
  runDealWorkflowR2 as runPrimaryMarketWorkflowR2,
  runDealWorkflowR3 as runPrimaryMarketWorkflowR3,
  uploadDealDocument as uploadPrimaryMarketDocument,
}

export function fetchPrimaryMarketProjects(query: DealQuery = {}, signal?: AbortSignal) {
  const params = new URLSearchParams()
  const q = query.q?.trim()
  if (q) params.set('q', q)
  if (query.status) params.set('status', query.status)
  if (query.page != null) params.set('page', String(query.page))
  if (query.page_size != null) params.set('page_size', String(query.page_size))
  if (query.include_status) params.set('include_status', 'true')
  const suffix = params.toString() ? `?${params.toString()}` : ''
  return apiJson<DealListResponse>(`/api/primary-market/projects${suffix}`, { signal })
}

export function fetchPrimaryMarketProject(dealId: string, signal?: AbortSignal) {
  return apiJson<DealDetailResponse>(`/api/primary-market/projects/${encodeURIComponent(dealId)}`, { signal })
}

export function fetchPrimaryMarketProjectStatus(dealId: string, signal?: AbortSignal) {
  return apiJson<DealStatusResponse>(`/api/primary-market/projects/${encodeURIComponent(dealId)}/status`, { signal })
}

export function fetchPrimaryMarketWiki(dealId: string, signal?: AbortSignal) {
  return apiJson<PrimaryMarketWikiProjection>(
    `/api/deals/${encodeURIComponent(dealId)}/wiki`,
    { signal },
  )
}

function primaryMarketMaterialsPath(dealId: string) {
  return `/api/primary-market/projects/${encodeURIComponent(dealId)}/materials`
}

function primaryMarketMaterialPath(dealId: string, documentId: string) {
  return `${primaryMarketMaterialsPath(dealId)}/${encodeURIComponent(documentId)}`
}

export interface UploadPrimaryMarketProspectusPayload {
  file: File
  exchange: 'SSE' | 'SZSE' | 'BSE' | string
  board: 'main' | 'star' | 'chinext' | 'beijing' | string
  filingStage: string
  documentDate?: string
  sourceNote?: string
  supersedesDocumentId?: string
}

export interface ReparsePrimaryMarketMaterialPayload {
  reason: 'parser_upgrade' | 'quality_retry' | 'manual' | string
  parseMethod?: string
  formulaEnable?: boolean
  tableEnable?: boolean
}

export interface ReviewPrimaryMarketAnalysisSourcePayload {
  decision: 'activate' | 'block'
  capabilityOverrides?: Record<string, string>
  note?: string
}

export function fetchPrimaryMarketMaterials(dealId: string, signal?: AbortSignal) {
  return apiJson<PrimaryMarketMaterialsResponse>(primaryMarketMaterialsPath(dealId), { signal })
}

export function fetchPrimaryMarketMaterial(dealId: string, documentId: string, signal?: AbortSignal) {
  return apiJson<PrimaryMarketMaterialResponse>(primaryMarketMaterialPath(dealId, documentId), { signal })
}

export function fetchPrimaryMarketMaterialParseStatus(
  dealId: string,
  documentId: string,
  signal?: AbortSignal,
) {
  return apiJson<PrimaryMarketMaterialParseStatusResponse>(
    `${primaryMarketMaterialPath(dealId, documentId)}/parse-status`,
    { signal },
  )
}

export function parsePrimaryMarketMaterial(
  dealId: string,
  documentId: string,
  signal?: AbortSignal,
) {
  return apiJson<PrimaryMarketMaterialResponse>(
    `${primaryMarketMaterialPath(dealId, documentId)}/parse`,
    { method: 'POST', signal },
  )
}

export function indexPrimaryMarketEvidenceMilvus(dealId: string, signal?: AbortSignal) {
  return apiJson<DealEvidenceMilvusIndexResponse>(
    `/api/deals/${encodeURIComponent(dealId)}/evidence/index-milvus`,
    { method: 'POST', signal },
  )
}

export function uploadPrimaryMarketProspectus(
  dealId: string,
  payload: UploadPrimaryMarketProspectusPayload,
  signal?: AbortSignal,
) {
  const form = new FormData()
  form.append('file', payload.file)
  form.append('exchange', payload.exchange)
  form.append('board', payload.board)
  form.append('filing_stage', payload.filingStage)
  if (payload.documentDate) form.append('document_date', payload.documentDate)
  if (payload.sourceNote?.trim()) form.append('source_note', payload.sourceNote.trim())
  if (payload.supersedesDocumentId?.trim()) {
    form.append('supersedes_document_id', payload.supersedesDocumentId.trim())
  }
  return apiJson<PrimaryMarketMaterialResponse>(`${primaryMarketMaterialsPath(dealId)}/prospectuses`, {
    method: 'POST',
    body: form,
    signal,
  })
}

export function reparsePrimaryMarketMaterial(
  dealId: string,
  documentId: string,
  payload: ReparsePrimaryMarketMaterialPayload,
  signal?: AbortSignal,
) {
  return apiJson<PrimaryMarketMaterialResponse>(
    `${primaryMarketMaterialPath(dealId, documentId)}/reparse`,
    {
      method: 'POST',
      body: {
        reason: payload.reason,
        parse_method: payload.parseMethod || 'auto',
        formula_enable: payload.formulaEnable ?? true,
        table_enable: payload.tableEnable ?? true,
      },
      signal,
    },
  )
}

export function reviewPrimaryMarketAnalysisSource(
  dealId: string,
  documentId: string,
  payload: ReviewPrimaryMarketAnalysisSourcePayload,
  signal?: AbortSignal,
) {
  return apiJson<PrimaryMarketMaterialResponse>(
    `${primaryMarketMaterialPath(dealId, documentId)}/analysis-source/review`,
    {
      method: 'POST',
      body: {
        decision: payload.decision,
        capability_overrides: payload.capabilityOverrides || {},
        note: payload.note?.trim() || '',
      },
      signal,
    },
  )
}

export function disablePrimaryMarketAnalysisSource(
  dealId: string,
  documentId: string,
  note = '',
  signal?: AbortSignal,
) {
  return apiJson<PrimaryMarketMaterialResponse>(
    `${primaryMarketMaterialPath(dealId, documentId)}/analysis-source/disable`,
    { method: 'POST', body: { note: note.trim() }, signal },
  )
}

export function supersedePrimaryMarketMaterial(
  dealId: string,
  documentId: string,
  supersedingDocumentId: string,
  signal?: AbortSignal,
) {
  return apiJson<PrimaryMarketMaterialResponse>(
    `${primaryMarketMaterialPath(dealId, documentId)}/supersede`,
    {
      method: 'POST',
      body: { superseding_document_id: supersedingDocumentId },
      signal,
    },
  )
}

export function primaryMarketMaterialArtifactUrl(dealId: string, documentId: string, artifactName: string) {
  return `${primaryMarketMaterialPath(dealId, documentId)}/artifacts/${encodeURIComponent(artifactName)}`
}

export function primaryMarketMaterialOriginalUrl(dealId: string, documentId: string) {
  return `${primaryMarketMaterialPath(dealId, documentId)}/original`
}

export function primaryMarketMaterialSourcePageUrl(dealId: string, documentId: string, pageNumber: number) {
  return `${primaryMarketMaterialPath(dealId, documentId)}/source/page/${encodeURIComponent(String(pageNumber))}`
}

export interface PrimaryMarketMeetingChatRequest {
  agentId: string
  agentLabel: string
  message: string
  retrievalQuery?: string
  displayMessage: string
  dealId: string
  companyName?: string | null
  lane?: string
  sessionId?: string | null
  modelMode?: string | null
  attachments?: AgentAttachment[]
  signal?: AbortSignal
}

export interface PrimaryMarketMeetingModelOption {
  mode: string
  label: string
  kind: 'cloud' | 'local'
  model: string
  provider: string
}

export interface PrimaryMarketMeetingModelCatalog {
  options: PrimaryMarketMeetingModelOption[]
  profiles: Record<string, { mode: string; model: string; provider: string }>
}

export interface PrimaryMarketMeetingChatResponse {
  reply?: string
  message?: string
  content?: string
  session_id?: string
  sessionId?: string
}

interface RawPrimaryMarketMeetingEvent {
  id?: string | null
  event_type?: MeetingEventType | string | null
  type?: MeetingEventType | string | null
  phase?: string | null
  speaker?: string | null
  title?: string | null
  body?: string | null
  tone?: BadgeTone | string | null
  meta?: string | Record<string, unknown> | null
  quality?: unknown
  agent_id?: string | null
  agentId?: string | null
  attachments?: AgentAttachment[] | null
  created_at?: string | null
  createdAt?: string | null
}

interface RawPrimaryMarketMeetingTranscript {
  deal_id?: string
  dealId?: string
  lane?: string
  events?: RawPrimaryMarketMeetingEvent[]
}

export interface PrimaryMarketMeetingTranscript {
  dealId: string
  lane: string
  events: MeetingEvent[]
}

export interface PrimaryMarketMeetingAttachmentUploadFile {
  filename: string
  content_type: string
  data_url: string
}

export interface PrimaryMarketMeetingChatHistory {
  dealId: string
  lane: string
  sessionId: string | null
  messages: HistoryRecord[]
}

export interface PrimaryMarketMeetingChatSessions {
  dealId: string
  lane: string
  sessions: ChatSessionSummary[]
}

export interface PrimaryMarketMeetingSessionResponse {
  dealId: string
  lane: string
  sessionId: string | null
  deletedOld?: string | null
  created?: boolean
}

export interface PrimaryMarketMeetingSuggestionQuestion {
  key: string
  label: string
  prompt: string
}

export interface PrimaryMarketMeetingSuggestions {
  dealId: string
  lane: string
  profile: string
  intro: string
  questions: PrimaryMarketMeetingSuggestionQuestion[]
  source: string
  error?: string | null
}

export interface PrimaryMarketMeetingAgentRuntimeReadiness {
  health: string
  port: number | null
  enabled?: boolean | null
  baseUrl?: string | null
  model?: string | null
}

export interface PrimaryMarketMeetingAgentContractReadiness {
  version?: string
  responsibilities: string[]
  sourceFiles: string[]
  outputs: string[]
  boundaries: string[]
  focus?: string
  phaseCapabilities?: Record<string, string[]>
  outputSchemas?: Record<string, string>
}

export interface PrimaryMarketMeetingStartupReceiptReadiness {
  present: boolean
  receiptId: string
  sharedConnected?: boolean
  privateConnected?: boolean
  sharedHits: number
  privateHits: number
  evidenceHits: number
  gaps: string[]
  collections?: string[]
  physicalCollections?: string[]
  sharedCollection?: string
  privateCollection?: string
  retrievalStatus?: string
  rerankReady?: boolean
  rerankStatus?: string
  rerankCandidateCount?: number
  rerankResultCount?: number
  retrievalStrategy?: string
  degradedReasons?: string[]
  blockingReasons?: string[]
  evidenceSnapshotHash?: string
  capabilityRestrictions?: string[]
  stale?: boolean
}

export interface PrimaryMarketMeetingR1ReportReadiness {
  present: boolean
  score: number | null
  recommendation: string
  artifactPath: string
}

export interface PrimaryMarketMeetingQualityReadiness {
  readyForFormalTask: boolean
  blockingReasons: string[]
  warnings: string[]
  status?: string
  stale?: boolean
}

export interface PrimaryMarketMeetingReadinessProfile {
  profileId: string
  label: string
  role: string
  runtime: PrimaryMarketMeetingAgentRuntimeReadiness
  contract: PrimaryMarketMeetingAgentContractReadiness
  startupReceipt: PrimaryMarketMeetingStartupReceiptReadiness
  r1Report: PrimaryMarketMeetingR1ReportReadiness
  quality: PrimaryMarketMeetingQualityReadiness
  serviceReadyForChat?: boolean
  chatBlockingReasons?: string[]
  contentWarnings?: string[]
  phaseTaskStatus?: string
}

export interface PrimaryMarketMeetingReadinessSummary {
  runtimeRunning: number
  receiptPresent: number
  r1ReportsPresent: number
  serviceReadyForChat?: number
  formalTaskReady?: number
  blockingProfiles: string[]
}

export interface PrimaryMarketMeetingAgentReadiness {
  schemaVersion: string
  dealId: string
  generatedAt?: string | null
  profiles: PrimaryMarketMeetingReadinessProfile[]
  summary: PrimaryMarketMeetingReadinessSummary
}

export interface PrimaryMarketMeetingPrepareOptions {
  round_name?: string
  query?: string | null
  limit?: number
  include_external?: boolean
  external_providers?: string[] | null
  include_vector?: boolean
  include_rerank?: boolean
  vector_collections?: string[] | null
}

export interface PrimaryMarketMeetingPrepareAllOptions extends PrimaryMarketMeetingPrepareOptions {
  profile_ids?: string[] | null
}

export interface PrimaryMarketMeetingPrepareAgentResponse {
  deal_id?: string
  agent_id?: string
  skipped?: boolean
  reason?: string
  receipt?: DealStartupReceipt | null
  event?: Record<string, unknown> | null
  readiness?: PrimaryMarketMeetingAgentReadiness | null
  [key: string]: unknown
}

export interface PrimaryMarketMeetingPrepareCommitteeResult {
  agent_id?: string
  status?: string
  receipt_id?: string | null
  shared_hits?: number | null
  private_hits?: number | null
  gaps?: string[]
  reason?: string
  error?: string
  [key: string]: unknown
}

export interface PrimaryMarketMeetingPrepareCommitteeResponse {
  deal_id?: string
  results?: PrimaryMarketMeetingPrepareCommitteeResult[]
  event?: Record<string, unknown> | null
  readiness?: PrimaryMarketMeetingAgentReadiness | null
  [key: string]: unknown
}

export interface PrimaryMarketMeetingWorkflowAdvanceRequest {
  dry_run?: boolean
  allow_hermes?: boolean
  max_agents?: number
  r3_skip?: boolean
  r3_skip_reason?: string | null
  r4_overwrite?: boolean
}

export interface PrimaryMarketMeetingWorkflowAdvanceResult {
  schema_version?: string
  deal_id?: string
  workflow_action?: string
  dry_run?: boolean
  allowed?: boolean
  would_write?: boolean
  selected_action?: string
  requires_hermes?: boolean
  allow_hermes?: boolean
  blocking_reasons?: string[]
  warnings?: string[]
  action_dry_run?: Record<string, unknown>
  action_result?: Record<string, unknown>
  workflow_advanced?: boolean
  [key: string]: unknown
}

export interface PrimaryMarketMeetingWorkflowAdvanceResponse {
  deal_id?: string
  dry_run?: boolean
  result?: PrimaryMarketMeetingWorkflowAdvanceResult
  event?: Record<string, unknown> | null
  readiness?: PrimaryMarketMeetingAgentReadiness | null
  [key: string]: unknown
}

export interface PrimaryMarketMeetingR1AgentRunRequest {
  dry_run?: boolean
  allow_hermes?: boolean
  round_name?: string
  lane?: string | null
  timeout?: number | null
}

export interface PrimaryMarketMeetingR1AgentRunResponse extends DealWorkflowRunR1AgentDryRunResponse {
  readiness?: PrimaryMarketMeetingAgentReadiness | null
}

export interface PrimaryMarketMeetingR1SerialRunRequest {
  dry_run?: boolean
  allow_hermes?: boolean
  round_name?: string
  max_agents?: number
  lane?: string | null
  timeout?: number | null
}

export interface PrimaryMarketMeetingR1SerialRunResponse extends DealWorkflowRunR1SerialResponse {
  readiness?: PrimaryMarketMeetingAgentReadiness | null
}

export interface PrimaryMarketMeetingDecisionHumanConfirmResponse {
  deal_id?: string
  dry_run?: boolean
  result?: DealDecisionHumanConfirmationUpdateResponse
  event?: Record<string, unknown> | null
  readiness?: PrimaryMarketMeetingAgentReadiness | null
  [key: string]: unknown
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function asStringArray(value: unknown) {
  return Array.isArray(value) ? value.map((item) => String(item || '').trim()).filter(Boolean) : []
}

function asPathArray(value: unknown) {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => {
      if (typeof item === 'string') return item.trim()
      const record = asRecord(item)
      return asString(record.path || record.name)
    })
    .filter(Boolean)
}

function asString(value: unknown, fallback = '') {
  if (value === null || value === undefined) return fallback
  const text = String(value).trim()
  return text || fallback
}

function asNumber(value: unknown, fallback = 0) {
  if (value === null || value === undefined || value === '') return fallback
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

function asNullableNumber(value: unknown) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function asBoolean(value: unknown, fallback = false) {
  if (typeof value === 'boolean') return value
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (['true', 'yes', '1'].includes(normalized)) return true
    if (['false', 'no', '0'].includes(normalized)) return false
  }
  return fallback
}

function normalizeReply(payload: PrimaryMarketMeetingChatResponse) {
  const value = payload.reply || payload.message || payload.content
  return typeof value === 'string' && value.trim() ? value : '智能体未返回内容。'
}

function normalizeSessionId(payload: Record<string, unknown>) {
  const value = payload.session_id || payload.sessionId
  return typeof value === 'string' && value.trim() ? value : null
}

function normalizeSuggestions(
  data: Record<string, unknown>,
  fallbackDealId: string,
  fallbackLane: string,
  fallbackProfile: string,
): PrimaryMarketMeetingSuggestions {
  const questions = (Array.isArray(data.questions) ? data.questions : [])
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    .map((item, index) => ({
      key: String(item.key || item.label || `q-${index + 1}`),
      label: String(item.label || '').trim(),
      prompt: String(item.prompt || '').trim(),
    }))
    .filter((item) => item.label && item.prompt)
  return {
    dealId: String(data.deal_id || data.dealId || fallbackDealId),
    lane: String(data.lane || fallbackLane || 'main'),
    profile: String(data.profile || fallbackProfile),
    intro: String(data.intro || '').trim(),
    questions,
    source: String(data.source || 'model'),
    error: typeof data.error === 'string' ? data.error : null,
  }
}

function normalizeReadinessProfile(item: unknown, index: number): PrimaryMarketMeetingReadinessProfile {
  const data = asRecord(item)
  const runtime = asRecord(data.runtime)
  const contract = asRecord(data.contract)
  const startupReceipt = asRecord(data.startup_receipt || data.startupReceipt)
  const r1Report = asRecord(data.r1_report || data.r1Report || data.report)
  const workflow = asRecord(data.workflow)
  const quality = asRecord(data.quality)
  const receiptId = asString(startupReceipt.receipt_id || startupReceipt.receiptId)
  const reportPath = asString(r1Report.artifact_path || r1Report.artifactPath)
  const profileId = asString(data.profile_id || data.profileId || data.agent_id || data.agentId, `profile-${index + 1}`)
  const blockingReasons = asStringArray(
    quality.blocking_reasons
      || quality.blockingReasons
      || data.blocking_reasons
      || data.blockingReasons
      || workflow.blocking_reasons
      || workflow.blockingReasons,
  )
  const warnings = asStringArray(quality.warnings || data.warnings || workflow.warnings)
  const evidenceHitCount = Array.isArray(startupReceipt.evidence_hits)
    ? startupReceipt.evidence_hits.length
    : asNumber(startupReceipt.evidence_hit_count ?? startupReceipt.evidenceHits)
  const sourceFiles = asPathArray(contract.source_files || contract.sourceFiles)
  const retrievalStrategy = asRecord(startupReceipt.retrieval_strategy || startupReceipt.retrievalStrategy)
  const physicalCollections = asStringArray(startupReceipt.physical_collections || startupReceipt.physicalCollections)
  const sharedCollection = asString(startupReceipt.shared_collection || startupReceipt.sharedCollection)
  const privateCollection = asString(startupReceipt.private_collection || startupReceipt.privateCollection)
  const sharedConnected = asBoolean(
    startupReceipt.shared_connected ?? startupReceipt.sharedConnected,
    false,
  )
  const privateConnected = asBoolean(
    startupReceipt.private_connected ?? startupReceipt.privateConnected,
    false,
  )
  const chatBlockingReasons = asStringArray(
    data.chat_blocking_reasons
      || data.chatBlockingReasons
      || quality.chat_blocking_reasons
      || quality.chatBlockingReasons,
  )
  const serviceReadyForChat = asBoolean(
    data.service_ready_for_chat
      ?? data.serviceReadyForChat
      ?? quality.service_ready_for_chat
      ?? quality.serviceReadyForChat,
    ['running', 'ready', 'healthy', 'ok', 'enabled'].includes(
      asString(runtime.health || runtime.status).toLowerCase(),
    ) && sourceFiles.length > 0 && sharedConnected && privateConnected && chatBlockingReasons.length === 0,
  )
  return {
    profileId,
    label: asString(data.label, profileId),
    role: asString(data.role),
    runtime: {
      health: asString(runtime.health || runtime.status, asBoolean(runtime.enabled, true) ? 'unknown' : 'disabled'),
      port: asNullableNumber(runtime.port),
      enabled: typeof runtime.enabled === 'boolean' ? runtime.enabled : null,
      baseUrl: asString(runtime.base_url || runtime.baseUrl) || null,
      model: asString(runtime.model || runtime.model_name || runtime.modelName) || null,
    },
    contract: {
      version: asString(contract.contract_version || contract.contractVersion || contract.schema_version || contract.schemaVersion),
      responsibilities: asStringArray(contract.responsibilities),
      sourceFiles,
      outputs: asStringArray(contract.outputs || contract.output_features || contract.outputFeatures),
      boundaries: asStringArray(contract.boundaries),
      focus: asString(contract.focus || contract.core_focus || contract.coreFocus) || undefined,
      phaseCapabilities: asRecord(contract.phase_capabilities || contract.phaseCapabilities) as Record<string, string[]>,
      outputSchemas: asRecord(contract.output_schemas || contract.outputSchemas) as Record<string, string>,
    },
    startupReceipt: {
      present: asBoolean(startupReceipt.present, Boolean(receiptId)),
      receiptId,
      sharedConnected,
      privateConnected,
      sharedHits: asNumber(startupReceipt.shared_hits ?? startupReceipt.sharedHits),
      privateHits: asNumber(startupReceipt.private_hits ?? startupReceipt.privateHits),
      evidenceHits: evidenceHitCount,
      gaps: asStringArray(startupReceipt.gaps),
      collections: asStringArray(startupReceipt.collections),
      physicalCollections,
      sharedCollection,
      privateCollection,
      retrievalStatus: asString(startupReceipt.retrieval_status || startupReceipt.retrievalStatus || startupReceipt.status),
      rerankReady: asBoolean(startupReceipt.rerank_ready ?? startupReceipt.rerankReady),
      rerankStatus: asString(startupReceipt.rerank_status || startupReceipt.rerankStatus),
      rerankCandidateCount: asNumber(startupReceipt.rerank_candidate_count ?? startupReceipt.rerankCandidateCount),
      rerankResultCount: asNumber(startupReceipt.rerank_result_count ?? startupReceipt.rerankResultCount),
      retrievalStrategy: asString(retrievalStrategy.mode),
      degradedReasons: asStringArray(startupReceipt.degraded_reasons || startupReceipt.degradedReasons || startupReceipt.retrieval_warnings),
      blockingReasons: asStringArray(startupReceipt.blocking_reasons || startupReceipt.blockingReasons),
      evidenceSnapshotHash: asString(startupReceipt.evidence_snapshot_hash || startupReceipt.evidenceSnapshotHash),
      capabilityRestrictions: asStringArray(startupReceipt.capability_restrictions || startupReceipt.capabilityRestrictions),
      stale: asBoolean(startupReceipt.stale || startupReceipt.is_stale || startupReceipt.isStale),
    },
    r1Report: {
      present: asBoolean(r1Report.present, Boolean(reportPath || r1Report.score || r1Report.recommendation)),
      score: asNullableNumber(r1Report.score),
      recommendation: asString(r1Report.recommendation),
      artifactPath: reportPath,
    },
    quality: {
      readyForFormalTask: asBoolean(
        quality.ready_for_formal_task
          ?? quality.readyForFormalTask
          ?? data.ready_for_formal_task
          ?? data.readyForFormalTask,
        blockingReasons.length === 0,
      ),
      blockingReasons,
      warnings,
      status: asString(quality.status),
      stale: asBoolean(quality.stale || data.stale),
    },
    serviceReadyForChat,
    chatBlockingReasons,
    contentWarnings: asStringArray(
      data.content_warnings
        || data.contentWarnings
        || quality.content_warnings
        || quality.contentWarnings,
    ),
    phaseTaskStatus: asString(workflow.phase_task_status || workflow.phaseTaskStatus || workflow.task_status || workflow.taskStatus),
  }
}

export function normalizePrimaryMarketMeetingAgentReadiness(
  data: unknown,
  fallbackDealId = '',
): PrimaryMarketMeetingAgentReadiness {
  const payload = asRecord(data)
  const rawSummary = asRecord(payload.summary)
  const rawProfiles = Array.isArray(payload.profiles)
    ? payload.profiles
    : Array.isArray(payload.agents)
      ? payload.agents
      : []
  const profiles = rawProfiles.map(normalizeReadinessProfile)
  const fallbackReceiptPresent = profiles.filter((profile) => profile.startupReceipt.present).length
  const fallbackReportsPresent = profiles.filter((profile) => profile.r1Report.present).length
  const fallbackRuntimeRunning = profiles.filter((profile) => profile.runtime.health === 'running').length
  const fallbackServiceReadyForChat = profiles.filter((profile) => profile.serviceReadyForChat).length
  const fallbackFormalTaskReady = profiles.filter((profile) => profile.quality.readyForFormalTask).length
  return {
    schemaVersion: asString(payload.schema_version || payload.schemaVersion, 'siq_primary_market_meeting_readiness_v1'),
    dealId: asString(payload.deal_id || payload.dealId, fallbackDealId),
    generatedAt: asString(payload.generated_at || payload.generatedAt) || null,
    profiles,
    summary: {
      runtimeRunning: asNumber(rawSummary.runtime_running ?? rawSummary.runtimeRunning, fallbackRuntimeRunning),
      receiptPresent: asNumber(rawSummary.receipt_present ?? rawSummary.receiptPresent ?? rawSummary.startup_receipts ?? rawSummary.startupReceipts, fallbackReceiptPresent),
      r1ReportsPresent: asNumber(rawSummary.r1_reports_present ?? rawSummary.r1ReportsPresent ?? rawSummary.r1_reports ?? rawSummary.r1Reports, fallbackReportsPresent),
      serviceReadyForChat: asNumber(
        rawSummary.service_ready_for_chat ?? rawSummary.serviceReadyForChat,
        fallbackServiceReadyForChat,
      ),
      formalTaskReady: asNumber(
        rawSummary.formal_task_ready
          ?? rawSummary.formalTaskReady
          ?? rawSummary.ready_for_formal_task
          ?? rawSummary.readyForFormalTask,
        fallbackFormalTaskReady,
      ),
      blockingProfiles: asStringArray(rawSummary.blocking_profiles || rawSummary.blockingProfiles),
    },
  }
}

function normalizeReadinessResponse<T extends { deal_id?: string; dealId?: string; readiness?: unknown }>(
  data: T,
  fallbackDealId: string,
) {
  return {
    ...data,
    readiness: data.readiness ? normalizePrimaryMarketMeetingAgentReadiness(data.readiness, data.deal_id || data.dealId || fallbackDealId) : null,
  }
}

function primaryMarketMeetingChatPayload(request: PrimaryMarketMeetingChatRequest) {
  const dealId = requireDealId(request.dealId, '缺少项目 ID，无法调用一级市场会议室智能体。')
  const lane = request.lane || 'main'
  const payload: Record<string, unknown> = {
    message: request.message,
    retrieval_query: request.retrievalQuery || request.message,
    display_message: request.displayMessage,
    deal_id: dealId,
    company_name: request.companyName || dealId,
    lane,
    context: {
      deal_id: dealId,
      company_name: request.companyName || dealId,
      lane,
      page: { title: '一级市场多智能体投研决策' },
      agent: {
        id: request.agentId,
        label: request.agentLabel,
      },
    },
  }
  if (request.attachments?.length) payload.attachments = request.attachments
  if (request.sessionId) payload.session_id = request.sessionId
  if (request.modelMode) payload.model_mode = request.modelMode
  return { dealId, lane, payload }
}

function normalizePrimaryMarketMeetingModelCatalog(data: unknown): PrimaryMarketMeetingModelCatalog {
  const payload = asRecord(data)
  const options = (Array.isArray(payload.options) ? payload.options : [])
    .map((item): PrimaryMarketMeetingModelOption | null => {
      const option = asRecord(item)
      const mode = asString(option.mode)
      const kind = option.kind === 'cloud' || option.kind === 'local' ? option.kind : null
      if (!mode || !kind) return null
      return {
        mode,
        label: asString(option.label, mode),
        kind,
        model: asString(option.model),
        provider: asString(option.provider),
      }
    })
    .filter((option): option is PrimaryMarketMeetingModelOption => option !== null)
  const profiles = Object.fromEntries(
    Object.entries(asRecord(payload.profiles)).map(([profileId, value]) => {
      const profile = asRecord(value)
      return [profileId, {
        mode: asString(profile.mode),
        model: asString(profile.model),
        provider: asString(profile.provider),
      }]
    }),
  )
  return { options, profiles }
}

export async function fetchPrimaryMarketMeetingModels(signal?: AbortSignal) {
  return normalizePrimaryMarketMeetingModelCatalog(
    await apiJson<unknown>('/api/primary-market/meeting/models', { signal }),
  )
}

function normalizeMeetingEventType(value: unknown): MeetingEventType {
  const allowed: MeetingEventType[] = [
    'coordinator_instruction',
    'agent_speech',
    'human_intervention',
    'phase_summary',
    'receipt_generated',
    'dispute_detected',
    'decision_draft',
    'system_blocking',
    'artifact_written',
    'audit_event',
    'quality_check',
  ]
  return allowed.includes(value as MeetingEventType) ? value as MeetingEventType : 'phase_summary'
}

function normalizeTone(value: unknown): BadgeTone {
  const allowed: BadgeTone[] = ['neutral', 'info', 'success', 'warning', 'error']
  return allowed.includes(value as BadgeTone) ? value as BadgeTone : 'neutral'
}

function normalizeEventMeta(value: unknown) {
  if (typeof value === 'string' && value.trim()) return value
  const record = asRecord(value)
  if (!Object.keys(record).length) return undefined
  const source = asString(record.source)
  if (source) return source
  return JSON.stringify(record)
}

function normalizeQualityCheck(value: unknown) {
  const record = asRecord(value)
  const id = asString(record.id || record.check_id || record.name)
  const status = asString(record.status)
  if (!id || !status) return null
  return {
    id,
    status,
    detail: asString(record.detail || record.message) || undefined,
  }
}

function normalizeEventQuality(value: unknown): MeetingQualityResult | null {
  const record = asRecord(value)
  if (!Object.keys(record).length) return null
  const checks = Array.isArray(record.checks)
    ? record.checks.map(normalizeQualityCheck).filter((item): item is NonNullable<ReturnType<typeof normalizeQualityCheck>> => Boolean(item))
    : []
  return {
    ...record,
    status: asString(record.status, 'warn'),
    checks,
  }
}

function normalizeMeetingEvent(event: RawPrimaryMarketMeetingEvent, index: number): MeetingEvent {
  const createdAt = event.createdAt || event.created_at || null
  const metaRecord = asRecord(event.meta)
  const quality = normalizeEventQuality(event.quality || metaRecord.quality)
  return {
    id: String(event.id || `meeting-transcript-${index}`),
    phase: String(event.phase || 'R0'),
    type: normalizeMeetingEventType(event.type || event.event_type),
    speaker: String(event.speaker || 'SIQ 会议室'),
    title: String(event.title || '会议事件'),
    body: String(event.body || ''),
    tone: normalizeTone(event.tone),
    meta: normalizeEventMeta(event.meta),
    agentId: event.agentId || event.agent_id || null,
    attachments: Array.isArray(event.attachments) ? event.attachments : undefined,
    quality,
    createdAt,
  }
}

function serializeMeetingEvent(event: MeetingEvent): RawPrimaryMarketMeetingEvent {
  return {
    id: event.id,
    event_type: event.type,
    phase: event.phase,
    speaker: event.speaker,
    title: event.title,
    body: event.body,
    tone: event.tone,
    meta: event.meta || null,
    agent_id: event.agentId || null,
    ...(event.attachments?.length ? { attachments: event.attachments } : {}),
    created_at: event.createdAt || null,
  }
}

function requireDealId(dealId: string, message: string) {
  const value = dealId.trim()
  if (!value) throw new Error(message)
  return value
}

export async function fetchPrimaryMarketMeetingTranscript(
  dealId: string,
  options: { lane?: string; limit?: number } = {},
  signal?: AbortSignal,
): Promise<PrimaryMarketMeetingTranscript> {
  const value = requireDealId(dealId, '缺少项目 ID，无法读取一级市场会议纪要。')
  const params = new URLSearchParams()
  params.set('lane', options.lane || 'main')
  if (options.limit && Number.isFinite(options.limit)) params.set('limit', String(Math.max(1, Math.floor(options.limit))))
  const data = await apiJson<RawPrimaryMarketMeetingTranscript>(
    `/api/primary-market/projects/${encodeURIComponent(value)}/meeting-transcript?${params.toString()}`,
    { signal },
  )
  return {
    dealId: data.dealId || data.deal_id || value,
    lane: data.lane || options.lane || 'main',
    events: (data.events || []).map(normalizeMeetingEvent),
  }
}

export async function appendPrimaryMarketMeetingEvent(
  dealId: string,
  event: MeetingEvent,
  options: { lane?: string; signal?: AbortSignal } = {},
): Promise<MeetingEvent> {
  const value = requireDealId(dealId, '缺少项目 ID，无法归档一级市场会议事件。')
  const data = await apiJson<{ event?: RawPrimaryMarketMeetingEvent }>(
    `/api/primary-market/projects/${encodeURIComponent(value)}/meeting-transcript/events`,
    {
      method: 'POST',
      signal: options.signal,
      body: {
        lane: options.lane || 'main',
        event: serializeMeetingEvent(event),
      },
    },
  )
  return normalizeMeetingEvent(data.event || serializeMeetingEvent(event), 0)
}

export async function uploadPrimaryMarketMeetingAttachments(
  dealId: string,
  files: PrimaryMarketMeetingAttachmentUploadFile[],
  signal?: AbortSignal,
): Promise<AgentAttachment[]> {
  const value = requireDealId(dealId, '缺少项目 ID，无法上传一级市场会议附件。')
  const data = await apiJson<{ attachments?: AgentAttachment[] }>(
    `/api/primary-market/projects/${encodeURIComponent(value)}/meeting/attachments`,
    {
      method: 'POST',
      signal,
      body: { files },
    },
  )
  return data.attachments || []
}

export async function postPrimaryMarketMeetingChat(request: PrimaryMarketMeetingChatRequest) {
  const { payload } = primaryMarketMeetingChatPayload(request)

  const data = await apiJson<PrimaryMarketMeetingChatResponse>(
    `/api/primary-market/meeting/${encodeURIComponent(request.agentId)}/chat`,
    {
      method: 'POST',
      signal: request.signal,
      body: payload,
    },
  )
  return {
    reply: normalizeReply(data),
    sessionId: data.sessionId || data.session_id || null,
  }
}

export function streamPrimaryMarketMeetingChat(request: PrimaryMarketMeetingChatRequest) {
  const { payload } = primaryMarketMeetingChatPayload(request)
  return apiStreamFetch(`/api/primary-market/meeting/${encodeURIComponent(request.agentId)}/chat/stream`, {
    method: 'POST',
    headers: { Accept: 'text/event-stream' },
    signal: request.signal,
    body: payload,
  })
}

function meetingChatParams(dealId: string, lane = 'main', extra: Record<string, string | number | null | undefined> = {}) {
  const value = requireDealId(dealId, '缺少项目 ID，无法读取一级市场会议会话。')
  const params = new URLSearchParams()
  params.set('deal_id', value)
  params.set('lane', lane || 'main')
  Object.entries(extra).forEach(([key, rawValue]) => {
    if (rawValue === null || rawValue === undefined || rawValue === '') return
    params.set(key, String(rawValue))
  })
  return { dealId: value, params }
}

export async function fetchPrimaryMarketMeetingSuggestions(
  agentId: string,
  dealId: string,
  options: { lane?: string; mode?: string } = {},
  signal?: AbortSignal,
): Promise<PrimaryMarketMeetingSuggestions> {
  const { dealId: value, params } = meetingChatParams(dealId, options.lane, { mode: options.mode || undefined })
  const data = await apiJson<Record<string, unknown>>(
    `/api/primary-market/meeting/${encodeURIComponent(agentId)}/suggestions?${params.toString()}`,
    { signal },
  )
  return normalizeSuggestions(data, value, options.lane || 'main', agentId)
}

export async function fetchPrimaryMarketMeetingAgentReadiness(
  dealId: string,
  signal?: AbortSignal,
): Promise<PrimaryMarketMeetingAgentReadiness> {
  const value = requireDealId(dealId, '缺少项目 ID，无法读取一级市场会议室 readiness。')
  const data = await apiJson<Record<string, unknown>>(
    `/api/primary-market/meeting/${encodeURIComponent(value)}/agents/readiness`,
    { signal },
  )
  return normalizePrimaryMarketMeetingAgentReadiness(data, value)
}

function primaryMarketMeetingPreparePayload(options: PrimaryMarketMeetingPrepareOptions = {}) {
  return {
    round_name: options.round_name || 'R1',
    query: options.query || undefined,
    limit: options.limit ?? 10,
    include_external: options.include_external ?? false,
    external_providers: options.external_providers || undefined,
    include_vector: options.include_vector ?? true,
    include_rerank: options.include_rerank ?? true,
    vector_collections: options.vector_collections || undefined,
  }
}

export async function preparePrimaryMarketMeetingAgent(
  dealId: string,
  profileId: string,
  options: PrimaryMarketMeetingPrepareOptions = {},
  signal?: AbortSignal,
): Promise<PrimaryMarketMeetingPrepareAgentResponse> {
  const value = requireDealId(dealId, '缺少项目 ID，无法准备一级市场会议室智能体。')
  const profile = profileId.trim()
  if (!profile) throw new Error('缺少智能体 ID，无法准备一级市场会议室智能体。')
  const data = await apiJson<PrimaryMarketMeetingPrepareAgentResponse>(
    `/api/primary-market/meeting/${encodeURIComponent(value)}/agents/${encodeURIComponent(profile)}/prepare`,
    {
      method: 'POST',
      signal,
      body: primaryMarketMeetingPreparePayload(options),
    },
  )
  return normalizeReadinessResponse(data, value)
}

export async function preparePrimaryMarketMeetingCommittee(
  dealId: string,
  options: PrimaryMarketMeetingPrepareAllOptions = {},
  signal?: AbortSignal,
): Promise<PrimaryMarketMeetingPrepareCommitteeResponse> {
  const value = requireDealId(dealId, '缺少项目 ID，无法准备一级市场会议室全体委员。')
  const data = await apiJson<PrimaryMarketMeetingPrepareCommitteeResponse>(
    `/api/primary-market/meeting/${encodeURIComponent(value)}/agents/prepare-all`,
    {
      method: 'POST',
      signal,
      body: {
        ...primaryMarketMeetingPreparePayload(options),
        profile_ids: options.profile_ids || undefined,
      },
    },
  )
  return normalizeReadinessResponse(data, value)
}

export async function advancePrimaryMarketMeetingWorkflow(
  dealId: string,
  payload: PrimaryMarketMeetingWorkflowAdvanceRequest = {},
  signal?: AbortSignal,
): Promise<PrimaryMarketMeetingWorkflowAdvanceResponse> {
  const value = requireDealId(dealId, '缺少项目 ID，无法推进一级市场会议室 workflow。')
  const data = await apiJson<PrimaryMarketMeetingWorkflowAdvanceResponse>(
    `/api/primary-market/meeting/${encodeURIComponent(value)}/workflow/advance`,
    {
      method: 'POST',
      signal,
      body: {
        dry_run: payload.dry_run ?? true,
        allow_hermes: payload.allow_hermes ?? false,
        max_agents: payload.max_agents ?? 1,
        r3_skip: payload.r3_skip ?? false,
        r3_skip_reason: payload.r3_skip_reason ?? null,
        r4_overwrite: payload.r4_overwrite ?? false,
      },
    },
  )
  return normalizeReadinessResponse(data, value)
}

export async function runPrimaryMarketMeetingR1Agent(
  dealId: string,
  profileId: string,
  payload: PrimaryMarketMeetingR1AgentRunRequest = {},
  signal?: AbortSignal,
): Promise<PrimaryMarketMeetingR1AgentRunResponse> {
  const value = requireDealId(dealId, '缺少项目 ID，无法执行当前智能体 R1 正式任务。')
  const profile = profileId.trim()
  if (!profile) throw new Error('缺少智能体 ID，无法执行当前智能体 R1 正式任务。')
  return apiJson<PrimaryMarketMeetingR1AgentRunResponse>(
    `/api/primary-market/meeting/${encodeURIComponent(value)}/agents/${encodeURIComponent(profile)}/run-r1`,
    {
      method: 'POST',
      signal,
      body: {
        round_name: payload.round_name || 'R1',
        dry_run: payload.dry_run ?? true,
        allow_hermes: payload.allow_hermes ?? false,
        lane: payload.lane || undefined,
        timeout: payload.timeout ?? undefined,
      },
    },
  )
}

export async function runPrimaryMarketMeetingR1Serial(
  dealId: string,
  payload: PrimaryMarketMeetingR1SerialRunRequest = {},
  signal?: AbortSignal,
): Promise<PrimaryMarketMeetingR1SerialRunResponse> {
  const value = requireDealId(dealId, '缺少项目 ID，无法执行 R1 串行任务。')
  return apiJson<PrimaryMarketMeetingR1SerialRunResponse>(
    `/api/primary-market/meeting/${encodeURIComponent(value)}/workflow/run-r1-serial`,
    {
      method: 'POST',
      signal,
      body: {
        round_name: payload.round_name || 'R1',
        dry_run: payload.dry_run ?? true,
        allow_hermes: payload.allow_hermes ?? false,
        max_agents: payload.max_agents ?? 6,
        lane: payload.lane || undefined,
        timeout: payload.timeout ?? undefined,
      },
    },
  )
}

export async function confirmPrimaryMarketDecision(
  dealId: string,
  payload: DealDecisionHumanConfirmationPayload,
  signal?: AbortSignal,
): Promise<PrimaryMarketMeetingDecisionHumanConfirmResponse> {
  const value = requireDealId(dealId, '缺少项目 ID，无法写入一级市场投决人工确认。')
  const data = await apiJson<PrimaryMarketMeetingDecisionHumanConfirmResponse>(
    `/api/primary-market/meeting/${encodeURIComponent(value)}/decision/human-confirm`,
    {
      method: 'POST',
      signal,
      body: {
        dry_run: true,
        ...payload,
      },
    },
  )
  return normalizeReadinessResponse(data, value)
}

export async function fetchPrimaryMarketMeetingChatHistory(
  agentId: string,
  dealId: string,
  options: { lane?: string; sessionId?: string | null; limit?: number } = {},
  signal?: AbortSignal,
): Promise<PrimaryMarketMeetingChatHistory> {
  const { dealId: value, params } = meetingChatParams(dealId, options.lane, {
    session_id: options.sessionId || undefined,
    limit: options.limit && Number.isFinite(options.limit) ? Math.max(1, Math.floor(options.limit)) : undefined,
  })
  const data = await apiJson<{ deal_id?: string; dealId?: string; lane?: string; session_id?: string; sessionId?: string; messages?: HistoryRecord[] }>(
    `/api/primary-market/meeting/${encodeURIComponent(agentId)}/chat/history?${params.toString()}`,
    { signal },
  )
  return {
    dealId: data.dealId || data.deal_id || value,
    lane: data.lane || options.lane || 'main',
    sessionId: data.sessionId || data.session_id || null,
    messages: Array.isArray(data.messages) ? data.messages : [],
  }
}

export async function fetchPrimaryMarketMeetingChatSessions(
  agentId: string,
  dealId: string,
  options: { lane?: string; limit?: number } = {},
  signal?: AbortSignal,
): Promise<PrimaryMarketMeetingChatSessions> {
  const { dealId: value, params } = meetingChatParams(dealId, options.lane, {
    limit: options.limit && Number.isFinite(options.limit) ? Math.max(1, Math.floor(options.limit)) : undefined,
  })
  const data = await apiJson<{ deal_id?: string; dealId?: string; lane?: string; sessions?: ChatSessionSummary[] }>(
    `/api/primary-market/meeting/${encodeURIComponent(agentId)}/chat/sessions?${params.toString()}`,
    { signal },
  )
  return {
    dealId: data.dealId || data.deal_id || value,
    lane: data.lane || options.lane || 'main',
    sessions: Array.isArray(data.sessions) ? data.sessions : [],
  }
}

export async function createPrimaryMarketMeetingChatSession(
  agentId: string,
  dealId: string,
  options: { lane?: string } = {},
): Promise<PrimaryMarketMeetingSessionResponse> {
  const { dealId: value, params } = meetingChatParams(dealId, options.lane)
  const data = await apiJson<Record<string, unknown>>(
    `/api/primary-market/meeting/${encodeURIComponent(agentId)}/chat/session?${params.toString()}`,
    { method: 'POST' },
  )
  return {
    dealId: String(data.dealId || data.deal_id || value),
    lane: String(data.lane || options.lane || 'main'),
    sessionId: normalizeSessionId(data),
    created: Boolean(data.created),
  }
}

export async function switchPrimaryMarketMeetingChatSession(
  agentId: string,
  dealId: string,
  sessionId: string,
  options: { lane?: string } = {},
): Promise<PrimaryMarketMeetingSessionResponse> {
  const { dealId: value, params } = meetingChatParams(dealId, options.lane)
  const data = await apiJson<Record<string, unknown>>(
    `/api/primary-market/meeting/${encodeURIComponent(agentId)}/chat/session/${encodeURIComponent(sessionId)}?${params.toString()}`,
    { method: 'POST' },
  )
  return {
    dealId: String(data.dealId || data.deal_id || value),
    lane: String(data.lane || options.lane || 'main'),
    sessionId: normalizeSessionId(data),
  }
}

export async function deletePrimaryMarketMeetingChatSession(
  agentId: string,
  dealId: string,
  options: { lane?: string; sessionId?: string | null } = {},
): Promise<PrimaryMarketMeetingSessionResponse> {
  const { dealId: value, params } = meetingChatParams(dealId, options.lane, {
    session_id: options.sessionId || undefined,
  })
  const data = await apiJson<Record<string, unknown>>(
    `/api/primary-market/meeting/${encodeURIComponent(agentId)}/chat/session?${params.toString()}`,
    { method: 'DELETE' },
  )
  return {
    dealId: String(data.dealId || data.deal_id || value),
    lane: String(data.lane || options.lane || 'main'),
    sessionId: normalizeSessionId(data),
    deletedOld: typeof data.deleted_old === 'string' ? data.deleted_old : null,
  }
}

export async function stopPrimaryMarketMeetingChat(
  agentId: string,
  dealId: string,
  options: { lane?: string; sessionId?: string | null } = {},
): Promise<Record<string, unknown>> {
  const { params } = meetingChatParams(dealId, options.lane, {
    session_id: options.sessionId || undefined,
  })
  return apiJson<Record<string, unknown>>(
    `/api/primary-market/meeting/${encodeURIComponent(agentId)}/chat/stop?${params.toString()}`,
    { method: 'POST' },
  )
}
