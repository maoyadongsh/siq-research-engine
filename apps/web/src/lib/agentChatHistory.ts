import type { AgentChatSnapshot, AgentMessage, ChatSessionSummary, HistoryRecord } from './agentChatTypes'

export const SESSION_FETCH_TIMEOUT_MS = 8000

export function createInitialAgentChatSnapshot(): AgentChatSnapshot {
  return {
    messages: [],
    sessions: [],
    currentSessionId: null,
    loadingSessions: false,
    sessionsLoaded: false,
    attachments: [],
    uploadingAttachments: false,
    input: '',
    sending: false,
    composing: false,
    loaded: false,
  }
}

export function nowIso() {
  return new Date().toISOString()
}

export function hasVisibleMessagePayload(message: AgentMessage | HistoryRecord) {
  return Boolean(message.content?.trim() || message.attachments?.length)
}

export function hasVisibleSessionPayload(session: ChatSessionSummary) {
  return Number(session.message_count || 0) > 0
    && Boolean(session.title?.trim() || session.preview?.trim())
}

export function isFileSearchCommand(preview?: string | null) {
  const text = String(preview || '').trim()
  return /(^|\s)(rg|grep|find)\s/.test(text)
    || text.includes('resolve_company.py')
    || text.includes('note_detail_lookup.py')
    || text.includes('/home/maoyd/wiki/')
}

export function toolDisplayName(tool?: string | null, preview?: string | null) {
  const name = String(tool || '').trim()
  if (name === 'search_files') return 'Search file'
  if (name === 'read_file') return 'Read file'
  if (name === 'terminal' && isFileSearchCommand(preview)) return 'Search file'
  if (name === 'execute_code') return 'Code execution'
  if (!name) return '工具'
  return name
}
