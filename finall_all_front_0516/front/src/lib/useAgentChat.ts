import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react'

export interface AgentMessage {
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
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

interface HistoryRecord {
  role: string
  content: string
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

interface AgentChatSnapshot {
  messages: AgentMessage[]
  sessions: ChatSessionSummary[]
  input: string
  sending: boolean
  composing: boolean
  loaded: boolean
}

interface ActiveRunSnapshot {
  running: boolean
  status?: string
  run_id?: string
  session_id?: string
  content?: string
  event_count?: number
}

type Listener = () => void

class AgentChatStore {
  private readonly apiPrefix: string

  private state: AgentChatSnapshot = {
    messages: [],
    sessions: [],
    input: '',
    sending: false,
    composing: false,
    loaded: false,
  }

  private listeners = new Set<Listener>()
  private abortController: AbortController | null = null
  private activeRunId: string | null = null
  private historyPromise: Promise<void> | null = null
  private initializePromise: Promise<void> | null = null

  constructor(apiPrefix: string) {
    this.apiPrefix = apiPrefix
  }

  subscribe = (listener: Listener) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getSnapshot = () => this.state

  private setState(updater: AgentChatSnapshot | ((prev: AgentChatSnapshot) => AgentChatSnapshot)) {
    this.state = typeof updater === 'function' ? updater(this.state) : updater
    this.listeners.forEach((listener) => listener())
  }

  loadHistory = async () => {
    if (this.state.loaded || this.state.messages.length > 0 || this.state.sending) return
    if (this.historyPromise) return this.historyPromise
    this.historyPromise = this.fetchHistory().finally(() => {
      this.historyPromise = null
    })
    return this.historyPromise
  }

  initialize = async () => {
    if (this.initializePromise) return this.initializePromise
    this.initializePromise = (async () => {
      await this.loadHistory()
      await this.resumeActiveRun()
    })().finally(() => {
      this.initializePromise = null
    })
    return this.initializePromise
  }

  refreshHistory = async () => {
    if (this.state.sending) return this.state.messages.length
    this.historyPromise = null
    await this.fetchHistory({ force: true })
    return this.state.messages.length
  }

  loadSessions = async (force = false) => {
    if (this.state.sending && !force) return this.state.sessions
    try {
      const res = await fetch(`${this.apiPrefix}/chat/sessions`)
      if (!res.ok) return this.state.sessions
      const data: ChatSessionSummary[] = await res.json()
      this.setState((prev) => ({ ...prev, sessions: data }))
      return data
    } catch {
      return this.state.sessions
    }
  }

  switchSession = async (sessionId: string) => {
    if (this.state.sending) return
    try {
      const res = await fetch(`${this.apiPrefix}/chat/session/${encodeURIComponent(sessionId)}`, { method: 'POST' })
      if (!res.ok) return
      this.historyPromise = null
      await this.fetchHistory({ force: true })
      await this.loadSessions(true)
    } catch {
      /* ignore */
    }
  }

  private fetchHistory = async ({ force = false }: { force?: boolean } = {}) => {
    if (!force && (this.state.sending || this.state.messages.some((message) => message.streaming))) return
    try {
      const res = await fetch(`${this.apiPrefix}/chat/history`)
      if (!res.ok) return
      const data: HistoryRecord[] = await res.json()
      if (!force && (this.state.sending || this.state.messages.some((message) => message.streaming))) return
      this.setState((prev) => ({
        ...prev,
        loaded: true,
        messages: data.map((m) => ({
          role: m.role as 'user' | 'assistant',
          content: m.content,
        })),
      }))
    } catch {
      /* ignore */
    }
  }

  setInput = (input: string) => {
    this.setState((prev) => ({ ...prev, input }))
  }

  setComposing = (composing: boolean) => {
    this.setState((prev) => ({ ...prev, composing }))
  }

  private appendAssistantDelta(content: string) {
    if (!content) return
    this.setState((prev) => {
      const last = prev.messages[prev.messages.length - 1]
      if (last?.role === 'assistant') {
        return {
          ...prev,
          messages: [...prev.messages.slice(0, -1), { ...last, content: last.content + content }],
        }
      }
      return {
        ...prev,
        messages: [...prev.messages, { role: 'assistant', content, streaming: true }],
      }
    })
  }

  private finishStreamingMessage() {
    this.setState((prev) => {
      const last = prev.messages[prev.messages.length - 1]
      if (last?.role === 'assistant') {
        return {
          ...prev,
          messages: [...prev.messages.slice(0, -1), { ...last, streaming: false }],
        }
      }
      return prev
    })
  }

  private failStreamingMessage(error: Error) {
    this.setState((prev) => {
      const last = prev.messages[prev.messages.length - 1]
      if (last?.role === 'assistant') {
        return {
          ...prev,
          messages: [
            ...prev.messages.slice(0, -1),
            {
              ...last,
              content:
                error.name === 'AbortError'
                  ? last.content || '[已停止]'
                  : `[错误] ${error.message}`,
              streaming: false,
            },
          ],
        }
      }
      return prev
    })
  }

  private async consumeEventStream(res: Response) {
    if (!res.ok) throw new Error('请求失败')

    const reader = res.body?.getReader()
    if (!reader) throw new Error('不支持流式响应')

    const decoder = new TextDecoder()
    let buffer = ''
    let eventName = ''

    while (true) {
      const result = await reader.read()
      if (result.done) break
      buffer += decoder.decode(result.value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed || trimmed.startsWith(':')) continue

        if (trimmed.startsWith('event:')) {
          eventName = trimmed.slice(6).trim()
          continue
        }

        if (trimmed.startsWith('data:')) {
          const data = trimmed.slice(5).trim()
          if (data === '[DONE]') continue
          try {
            const payload = JSON.parse(data)
            if (eventName === 'run' && payload.run_id) {
              this.activeRunId = payload.run_id
              eventName = ''
              continue
            }
            if (payload.content) {
              this.appendAssistantDelta(payload.content)
            }
            eventName = ''
          } catch {
            if (data && data !== '[DONE]') {
              this.appendAssistantDelta(data)
            }
            eventName = ''
          }
        }
      }
    }
  }

  private resumeActiveRun = async () => {
    if (this.state.sending || this.state.messages.some((message) => message.streaming)) return
    try {
      const activeRes = await fetch(`${this.apiPrefix}/chat/active`)
      if (!activeRes.ok) return
      const active: ActiveRunSnapshot = await activeRes.json()
      if (!active.running || !active.run_id) {
        await this.fetchHistory({ force: true })
        return
      }

      const abort = new AbortController()
      this.abortController = abort
      this.activeRunId = active.run_id
      this.setState((prev) => ({
        ...prev,
        sending: true,
        loaded: true,
        messages: [
          ...prev.messages.filter((message) => !message.streaming),
          { role: 'assistant', content: active.content || '', streaming: true },
        ],
      }))

      const offset = Math.max(0, active.event_count || 0)
      const streamRes = await fetch(`${this.apiPrefix}/chat/active/stream?offset=${encodeURIComponent(offset)}`, {
        headers: { Accept: 'text/event-stream' },
        signal: abort.signal,
      })
      await this.consumeEventStream(streamRes)
      this.finishStreamingMessage()
      await this.fetchHistory({ force: true })
      await this.loadSessions(true)
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        this.finishStreamingMessage()
        this.setState((prev) => ({ ...prev, sending: false }))
        await this.fetchHistory({ force: true })
      }
    } finally {
      this.setState((prev) => ({ ...prev, sending: false }))
      this.abortController = null
      this.activeRunId = null
    }
  }

  sendMessage = async (text?: string, context?: AgentChatContext) => {
    const content = (text || this.state.input).trim()
    if (!content || this.state.sending) return

    this.setState((prev) => ({
      ...prev,
      input: '',
      sending: true,
      messages: [
        ...prev.messages,
        { role: 'user', content },
        { role: 'assistant', content: '', streaming: true },
      ],
    }))
    const abort = new AbortController()
    this.abortController = abort

    try {
      const res = await fetch(`${this.apiPrefix}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ message: content, context }),
        signal: abort.signal,
      })

      await this.consumeEventStream(res)
      this.finishStreamingMessage()
      await this.fetchHistory({ force: true })
      await this.loadSessions(true)
    } catch (e) {
      this.failStreamingMessage(e as Error)
    } finally {
      this.setState((prev) => ({ ...prev, sending: false }))
      this.abortController = null
      this.activeRunId = null
    }
  }

  clearChat = async () => {
    if (this.state.sending) return
    try {
      await fetch(`${this.apiPrefix}/chat/session`, { method: 'DELETE' })
    } catch {
      /* ignore */
    }
    this.setState((prev) => ({ ...prev, messages: [], loaded: true }))
    await this.loadSessions(true)
  }

  newChat = async () => {
    if (this.state.sending) return
    try {
      await fetch(`${this.apiPrefix}/chat/session`, { method: 'POST' })
    } catch {
      /* ignore */
    }
    this.historyPromise = null
    this.setState((prev) => ({ ...prev, messages: [], input: '', loaded: true }))
    await this.loadSessions(true)
  }

  stop = async () => {
    if (this.activeRunId) {
      try {
        await fetch(`${this.apiPrefix}/chat/stop`, { method: 'POST' })
      } catch {
        /* ignore */
      }
    }
    this.abortController?.abort()
  }
}

const stores = new Map<string, AgentChatStore>()

function getAgentChatStore(apiPrefix: string) {
  const key = apiPrefix.replace(/\/$/, '')
  let store = stores.get(key)
  if (!store) {
    store = new AgentChatStore(key)
    stores.set(key, store)
  }
  return store
}

export function useAgentChat(apiPrefix: string) {
  const store = useMemo(() => getAgentChatStore(apiPrefix), [apiPrefix])
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot)

  useEffect(() => {
    store.initialize()
  }, [store])

  const sendMessage = useCallback((text?: string, context?: AgentChatContext) => store.sendMessage(text, context), [store])
  const newChat = useCallback(() => store.newChat(), [store])
  const clearChat = useCallback(() => store.clearChat(), [store])
  const refreshHistory = useCallback(() => store.refreshHistory(), [store])
  const loadSessions = useCallback(() => store.loadSessions(), [store])
  const switchSession = useCallback((sessionId: string) => store.switchSession(sessionId), [store])
  const stop = useCallback(() => store.stop(), [store])
  const setInput = useCallback((value: string) => store.setInput(value), [store])
  const setComposing = useCallback((value: boolean) => store.setComposing(value), [store])

  return {
    messages: snapshot.messages,
    sessions: snapshot.sessions,
    input: snapshot.input,
    setInput,
    sending: snapshot.sending,
    composing: snapshot.composing,
    setComposing,
    sendMessage,
    newChat,
    refreshHistory,
    loadSessions,
    switchSession,
    clearChat,
    stop,
  }
}
