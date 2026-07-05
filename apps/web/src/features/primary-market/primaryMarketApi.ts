import { apiJson } from '@/shared/api/client'
import { fetchWithAuth } from '@/lib/fetchWithAuth'
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
import type { DealDetailResponse, DealListResponse, DealQuery, DealStatusResponse } from '@/lib/dealTypes'
import type { AgentAttachment, ChatSessionSummary, HistoryRecord } from '@/lib/useAgentChat'
import type { BadgeTone, MeetingEvent, MeetingEventType } from '@/features/primary-market/primaryMarketViewModel'

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
  const suffix = params.toString() ? `?${params.toString()}` : ''
  return apiJson<DealListResponse>(`/api/primary-market/projects${suffix}`, { signal })
}

export function fetchPrimaryMarketProject(dealId: string, signal?: AbortSignal) {
  return apiJson<DealDetailResponse>(`/api/primary-market/projects/${encodeURIComponent(dealId)}`, { signal })
}

export function fetchPrimaryMarketProjectStatus(dealId: string, signal?: AbortSignal) {
  return apiJson<DealStatusResponse>(`/api/primary-market/projects/${encodeURIComponent(dealId)}/status`, { signal })
}

export interface PrimaryMarketMeetingChatRequest {
  agentId: string
  agentLabel: string
  message: string
  displayMessage: string
  dealId: string
  companyName?: string | null
  lane?: string
  sessionId?: string | null
  attachments?: AgentAttachment[]
  signal?: AbortSignal
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
  meta?: string | null
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

function primaryMarketMeetingChatPayload(request: PrimaryMarketMeetingChatRequest) {
  const dealId = requireDealId(request.dealId, '缺少项目 ID，无法调用一级市场会议室智能体。')
  const lane = request.lane || 'main'
  const payload: Record<string, unknown> = {
    message: request.message,
    display_message: request.displayMessage,
    deal_id: dealId,
    company_name: request.companyName || dealId,
    lane,
    context: {
      deal_id: dealId,
      company_name: request.companyName || dealId,
      lane,
      page: { title: '一级市场多智能体投研会议室' },
      agent: {
        id: request.agentId,
        label: request.agentLabel,
      },
    },
  }
  if (request.attachments?.length) payload.attachments = request.attachments
  if (request.sessionId) payload.session_id = request.sessionId
  return { dealId, lane, payload }
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
  ]
  return allowed.includes(value as MeetingEventType) ? value as MeetingEventType : 'phase_summary'
}

function normalizeTone(value: unknown): BadgeTone {
  const allowed: BadgeTone[] = ['neutral', 'info', 'success', 'warning', 'error']
  return allowed.includes(value as BadgeTone) ? value as BadgeTone : 'neutral'
}

function normalizeMeetingEvent(event: RawPrimaryMarketMeetingEvent, index: number): MeetingEvent {
  const createdAt = event.createdAt || event.created_at || null
  return {
    id: String(event.id || `meeting-transcript-${index}`),
    phase: String(event.phase || 'R0'),
    type: normalizeMeetingEventType(event.type || event.event_type),
    speaker: String(event.speaker || 'SIQ 会议室'),
    title: String(event.title || '会议事件'),
    body: String(event.body || ''),
    tone: normalizeTone(event.tone),
    meta: typeof event.meta === 'string' && event.meta.trim() ? event.meta : undefined,
    agentId: event.agentId || event.agent_id || null,
    attachments: Array.isArray(event.attachments) ? event.attachments : undefined,
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
  return fetchWithAuth(`/api/primary-market/meeting/${encodeURIComponent(request.agentId)}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    signal: request.signal,
    body: JSON.stringify(payload),
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
