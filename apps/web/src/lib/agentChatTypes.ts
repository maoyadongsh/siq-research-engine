export interface AgentAttachment {
  id: string
  filename: string
  content_type: string
  size: number
  path: string
  url?: string
  kind: 'image' | 'document' | 'audio'
  metadata?: {
    duration?: number
    duration_ms?: number
    transcript?: string
    transcription_status?: string
    language?: string
    provider?: string
    codec?: string
    [key: string]: unknown
  }
}

export interface VoiceTranscriptionResponse {
  text: string
  duration: number
  language: string
  provider: string
  attachment: AgentAttachment
}

export interface AgentMessage {
  role: 'user' | 'assistant'
  content: string
  createdAt?: string
  streaming?: boolean
  progress?: AgentProgress
  attachments?: AgentAttachment[]
  agentId?: string
  agentName?: string
  auditTraceId?: string
}

export type AgentProgressStatus = 'queued' | 'running' | 'completed' | 'error' | 'stopped'

export interface AgentProgress {
  status: AgentProgressStatus
  title: string
  detail?: string
  current?: number
  total?: number
  percent?: number
  source?: string
  tool?: string
  updated_at?: string
}

/** Stable document identity supplied by the selected market/report record. */
export interface ResearchIdentity {
  market?: string
  company_id?: string
  filing_id?: string
  parse_run_id?: string
}

export interface AgentChatContext {
  company?: {
    code?: string
    name?: string
    dir?: string
    market?: string
    company_id?: string
    filing_id?: string
    parse_run_id?: string
  }
  report?: {
    type?: string
    title?: string
    filename?: string
    url?: string
    mtime?: string
    market?: string
    company_id?: string
    filing_id?: string
    parse_run_id?: string
  }
  page?: {
    title?: string
  }
  research_identity?: ResearchIdentity
}

export interface HistoryRecord {
  role: string
  content: string
  created_at?: string | null
  timestamp?: string | null
  attachments?: AgentAttachment[] | null
  audit_trace_id?: string | null
  auditTraceId?: string | null
}

export interface ChatSessionSummary {
  session_id: string
  title: string
  preview: string
  message_count: number
  first_message_at: string | null
  last_message_at: string | null
  current: boolean
}

export interface AgentChatSnapshot {
  messages: AgentMessage[]
  sessions: ChatSessionSummary[]
  currentSessionId: string | null
  loadingSessions: boolean
  sessionsLoaded: boolean
  attachments: AgentAttachment[]
  uploadingAttachments: boolean
  input: string
  sending: boolean
  composing: boolean
  loaded: boolean
}

export interface ActiveRunSnapshot {
  running: boolean
  status?: string
  run_id?: string
  session_id?: string
  content?: string
  progress?: AgentProgress
  event_count?: number
  started_at?: string
  updated_at?: string
  audit_trace_id?: string
  diagnostic?: {
    scope?: 'session' | 'profile'
    profile?: string
    profile_label?: string
    severity?: 'info' | 'warning' | 'error'
    issue?: string
    title?: string
    detail?: string
    recovery_action?: string
  }
}

export type Listener = () => void

export interface UseAgentChatOptions {
  autoInitialize?: boolean
}
