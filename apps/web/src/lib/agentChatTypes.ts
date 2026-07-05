export interface AgentAttachment {
  id: string
  filename: string
  content_type: string
  size: number
  path: string
  url?: string
  kind: 'image' | 'document'
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

export interface AgentChatContext {
  company?: {
    code?: string
    name?: string
    dir?: string
  }
  report?: {
    type?: string
    title?: string
    filename?: string
    url?: string
    mtime?: string
  }
  page?: {
    title?: string
  }
}

export interface HistoryRecord {
  role: string
  content: string
  created_at?: string | null
  timestamp?: string | null
  attachments?: AgentAttachment[] | null
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
