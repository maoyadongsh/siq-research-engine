import { apiFetch, apiStreamFetch } from './apiClient'
import { displayLabelForPrompt } from './quickQuestions'
import type { AgentAttachment, AgentChatContext, AgentChatSnapshot, AgentMessage, AgentProgress, ChatSessionSummary, HistoryRecord, Listener } from './agentChatTypes'
import { buildAttachmentUploadItems, MAX_ATTACHMENTS, stripRenderedAttachmentMarkdown, validateAndSelectAttachments } from './agentChatAttachments'
import { createInitialAgentChatSnapshot, hasVisibleMessagePayload, hasVisibleSessionPayload, nowIso, SESSION_FETCH_TIMEOUT_MS } from './agentChatHistory'
import { createStreamConsumer, type StreamApi } from './agentChatStream'

const STOPPED_MESSAGE = '[已停止] 本次对话已停止，后台 Hermes run 已收到停止请求。'
const ANSWER_AUDIT_TRACE_ID_RE = /^aat_[a-f0-9]{32}$/i

function normalizeAnswerAuditTraceId(value?: string | null) {
  const traceId = String(value || '').trim()
  return ANSWER_AUDIT_TRACE_ID_RE.test(traceId) ? traceId : undefined
}

class AgentChatStore {
  private readonly apiPrefix: string

  private state: AgentChatSnapshot = createInitialAgentChatSnapshot()

  private listeners = new Set<Listener>()
  private abortController: AbortController | null = null
  private sessionsAbortController: AbortController | null = null
  private activeRunId: string | null = null
  private firstEventTimer: ReturnType<typeof setTimeout> | null = null
  private historyPromise: Promise<void> | null = null
  private initializePromise: Promise<void> | null = null
  private sessionsPromise: Promise<ChatSessionSummary[]> | null = null
  private streamConsumer: ReturnType<typeof createStreamConsumer>

  constructor(apiPrefix: string) {
    this.apiPrefix = apiPrefix
    this.streamConsumer = createStreamConsumer(this.buildStreamApi())
  }

  subscribe = (listener: Listener) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getSnapshot = () => this.state

  reset = () => {
    this.abortController?.abort()
    this.sessionsAbortController?.abort()
    this.clearFirstEventTimer()
    this.abortController = null
    this.sessionsAbortController = null
    this.activeRunId = null
    this.historyPromise = null
    this.initializePromise = null
    this.sessionsPromise = null
    this.setState(createInitialAgentChatSnapshot())
  }

  private setState(updater: AgentChatSnapshot | ((prev: AgentChatSnapshot) => AgentChatSnapshot)) {
    this.state = typeof updater === 'function' ? updater(this.state) : updater
    this.listeners.forEach((listener) => listener())
  }

  private setCurrentSession(sessionId?: string | null) {
    const nextSessionId = sessionId || null
    this.setState((prev) => ({
      ...prev,
      currentSessionId: nextSessionId,
      sessions: prev.sessions.map((session) => ({
        ...session,
        current: Boolean(nextSessionId && session.session_id === nextSessionId),
      })),
    }))
  }

  private historyUrl(sessionId = this.state.currentSessionId) {
    if (!sessionId) return `${this.apiPrefix}/chat/history`
    return `${this.apiPrefix}/chat/history?session_id=${encodeURIComponent(sessionId)}`
  }

  private activeUrl(path: string, sessionId = this.state.currentSessionId, extra = '') {
    const params = new URLSearchParams()
    if (sessionId) params.set('session_id', sessionId)
    if (extra) {
      const extraParams = new URLSearchParams(extra)
      extraParams.forEach((value, key) => params.set(key, value))
    }
    const query = params.toString()
    return `${this.apiPrefix}${path}${query ? `?${query}` : ''}`
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
      await Promise.allSettled([this.loadSessions(), this.loadHistory()])
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
    if (this.sessionsPromise && !force) return this.sessionsPromise
    this.sessionsAbortController?.abort()
    const abort = new AbortController()
    const timeoutId = setTimeout(() => abort.abort(), SESSION_FETCH_TIMEOUT_MS)
    this.sessionsAbortController = abort
    this.setState((prev) => ({ ...prev, loadingSessions: true }))
    const promise = this.fetchSessions(abort.signal).finally(() => {
      clearTimeout(timeoutId)
      if (this.sessionsAbortController === abort) {
        this.sessionsAbortController = null
        this.sessionsPromise = null
        this.setState((prev) => ({ ...prev, loadingSessions: false }))
      }
    })
    this.sessionsPromise = promise
    return promise
  }

  private fetchSessions = async (signal?: AbortSignal) => {
    try {
      const res = await apiFetch(`${this.apiPrefix}/chat/sessions`, { signal })
      if (!res.ok) return this.state.sessions
      const data = await res.json()
      const sessions: ChatSessionSummary[] = (Array.isArray(data) ? data : data.sessions || []).filter(hasVisibleSessionPayload)
      const serverCurrentSessionId = sessions.find((session) => session.current)?.session_id || null
      const currentSessionId = serverCurrentSessionId
        || this.state.currentSessionId
        || sessions[0]?.session_id
        || null
      this.setState((prev) => ({
        ...prev,
        currentSessionId,
        sessions: sessions.map((session) => ({
          ...session,
          current: Boolean(currentSessionId && session.session_id === currentSessionId),
        })),
        sessionsLoaded: true,
      }))
      return sessions
    } catch {
      return this.state.sessions
    }
  }

  switchSession = async (sessionId: string) => {
    if (this.state.sending) return
    try {
      const res = await apiFetch(`${this.apiPrefix}/chat/session/${encodeURIComponent(sessionId)}`, { method: 'POST' })
      if (!res.ok) return
      this.setCurrentSession(sessionId)
      this.historyPromise = null
      await this.fetchHistory({ force: true, sessionId })
      await this.loadSessions(true)
    } catch {
      /* ignore */
    }
  }

  private fetchHistory = async ({ force = false, sessionId }: { force?: boolean; sessionId?: string | null } = {}) => {
    if (!force && (this.state.sending || this.state.messages.some((message) => message.streaming))) return
    try {
      const res = await apiFetch(this.historyUrl(sessionId))
      if (!res.ok) return
      const payload = await res.json()
      const data: HistoryRecord[] = Array.isArray(payload) ? payload : payload.messages || []
      const responseSessionId = Array.isArray(payload) ? sessionId : payload.session_id
      if (!force && (this.state.sending || this.state.messages.some((message) => message.streaming))) return
      this.setState((prev) => {
        const assistantAuditTraceIds = new Map<string, string[]>()
        for (const message of prev.messages) {
          if (message.role !== 'assistant' || !message.content || !message.auditTraceId) continue
          const values = assistantAuditTraceIds.get(message.content) || []
          values.push(message.auditTraceId)
          assistantAuditTraceIds.set(message.content, values)
        }
        return {
          ...prev,
          loaded: true,
          currentSessionId: responseSessionId || prev.currentSessionId,
          sessions: responseSessionId
            ? prev.sessions.map((session) => ({ ...session, current: session.session_id === responseSessionId }))
            : prev.sessions,
          messages: data.map((m) => {
            const attachments = m.attachments || undefined
            const content = stripRenderedAttachmentMarkdown(m.content, attachments)
            const role = m.role as 'user' | 'assistant'
            const serverAuditTraceId = normalizeAnswerAuditTraceId(m.audit_trace_id || m.auditTraceId)
            const carriedAuditTraceId = role === 'assistant' && content
              ? assistantAuditTraceIds.get(content)?.shift()
              : undefined
            return {
              role,
              content: role === 'user' ? displayLabelForPrompt(content) : content,
              createdAt: m.created_at || m.timestamp || undefined,
              attachments,
              auditTraceId: serverAuditTraceId || carriedAuditTraceId,
            }
          }).filter(hasVisibleMessagePayload),
        }
      })
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

  uploadAttachments = async (files: FileList | File[]) => {
    if (this.state.sending) return this.state.attachments
    const selected = validateAndSelectAttachments(files, this.state.attachments.length)
    if (!selected.length) return this.state.attachments

    const prepared = buildAttachmentUploadItems(selected)
    const tempAttachments = prepared.map((item) => item.tempAttachment)
    const tempIds = new Set(tempAttachments.map((item) => item.id))
    this.setState((prev) => ({
      ...prev,
      uploadingAttachments: true,
      attachments: [...prev.attachments, ...tempAttachments].slice(0, MAX_ATTACHMENTS),
    }))
    try {
      const payloadFiles = await Promise.all(prepared.map((item) => item.payloadPromise))
      const res = await apiFetch('/api/chat/attachments', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ files: payloadFiles }),
      })
      if (!res.ok) {
        let detail = '附件上传失败'
        try {
          const body = await res.json()
          detail = body.detail || body.message || detail
        } catch {
          /* ignore */
        }
        throw new Error(detail)
      }
      const data: { attachments?: AgentAttachment[] } = await res.json()
      const uploaded = data.attachments || []
      this.setState((prev) => ({
        ...prev,
        attachments: prev.attachments
          .flatMap((item) => {
            if (!tempIds.has(item.id)) return [item]
            const index = tempAttachments.findIndex((temp) => temp.id === item.id)
            return uploaded[index] ? [uploaded[index]] : []
          })
          .slice(0, MAX_ATTACHMENTS),
      }))
      return uploaded
    } catch (error) {
      this.setState((prev) => ({
        ...prev,
        attachments: prev.attachments.filter((item) => !tempIds.has(item.id)),
      }))
      throw error
    } finally {
      prepared.forEach((item) => URL.revokeObjectURL(item.previewUrl))
      this.setState((prev) => ({ ...prev, uploadingAttachments: false }))
    }
  }

  removeAttachment = (id: string) => {
    this.setState((prev) => ({
      ...prev,
      attachments: prev.attachments.filter((item) => item.id !== id),
    }))
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
        messages: [...prev.messages, { role: 'assistant', content, createdAt: nowIso(), streaming: true }],
      }
    })
  }

  private replaceAssistantContent(content: string) {
    this.setState((prev) => {
      const last = prev.messages[prev.messages.length - 1]
      if (last?.role === 'assistant') {
        return {
          ...prev,
          messages: [...prev.messages.slice(0, -1), { ...last, content }],
        }
      }
      return {
        ...prev,
        messages: [...prev.messages, { role: 'assistant', content, createdAt: nowIso(), streaming: true }],
      }
    })
  }

  private updateAssistantProgress(progress: AgentProgress) {
    this.setState((prev) => {
      const last = prev.messages[prev.messages.length - 1]
      if (last?.role === 'assistant') {
        return {
          ...prev,
          messages: [...prev.messages.slice(0, -1), { ...last, progress }],
        }
      }
      return {
        ...prev,
        messages: [...prev.messages, { role: 'assistant', content: '', createdAt: nowIso(), streaming: true, progress }],
      }
    })
  }

  private setAssistantAuditTraceId(traceId?: string | null) {
    const auditTraceId = normalizeAnswerAuditTraceId(traceId)
    if (!auditTraceId) return
    this.setState((prev) => {
      const last = prev.messages[prev.messages.length - 1]
      if (last?.role !== 'assistant') return prev
      return {
        ...prev,
        messages: [...prev.messages.slice(0, -1), { ...last, auditTraceId }],
      }
    })
  }

  private clearFirstEventTimer() {
    if (this.firstEventTimer) {
      clearTimeout(this.firstEventTimer)
      this.firstEventTimer = null
    }
  }

  private startFirstEventTimer() {
    this.clearFirstEventTimer()
    this.firstEventTimer = setTimeout(() => {
      this.updateAssistantProgress({
        status: 'running',
        title: '等待模型首轮输出',
        detail: '本地模型正在读取上下文并生成首轮结果；如需停止，可点击停止按钮。',
        percent: 8,
        source: 'runtime',
      })
    }, 8000)
  }

  private finishStreamingMessage() {
    this.setState((prev) => {
      const last = prev.messages[prev.messages.length - 1]
      if (last?.role === 'assistant') {
        return {
          ...prev,
          messages: [
            ...prev.messages.slice(0, -1),
            { ...last, streaming: false, progress: last.progress ? { ...last.progress, status: last.progress.status === 'error' ? 'error' : 'completed', percent: 100 } : last.progress },
          ],
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
                  ? STOPPED_MESSAGE
                  : `[错误] ${error.message}`,
              streaming: false,
              progress: error.name === 'AbortError'
                ? { status: 'stopped', title: '任务已停止', detail: STOPPED_MESSAGE, source: 'runtime' }
                : last.progress,
            },
          ],
        }
      }
      return prev
    })
  }

  private async responseErrorMessage(res: Response, fallback: string) {
    try {
      const payload = await res.json()
      const detail = payload?.detail
      if (typeof detail === 'string') return detail
      if (detail && typeof detail === 'object') return detail.message || detail.error || fallback
      return payload?.message || payload?.error || fallback
    } catch {
      const text = await res.text().catch(() => '')
      return text || fallback
    }
  }

  private async consumeEventStream(res: Response) {
    return this.streamConsumer.consumeEventStream(res)
  }

  private buildStreamApi(): StreamApi {
    return {
      setCurrentSession: (sessionId) => this.setCurrentSession(sessionId),
      setActiveRunId: (runId) => { this.activeRunId = runId },
      startFirstEventTimer: () => this.startFirstEventTimer(),
      clearFirstEventTimer: () => this.clearFirstEventTimer(),
      appendAssistantDelta: (content) => this.appendAssistantDelta(content),
      replaceAssistantContent: (content) => this.replaceAssistantContent(content),
      setAssistantAuditTraceId: (traceId) => this.setAssistantAuditTraceId(traceId),
      updateAssistantProgress: (progress) => this.updateAssistantProgress(progress),
      responseErrorMessage: (res, fallback) => this.responseErrorMessage(res, fallback),
    }
  }

  private resumeActiveRun = async () => {
    if (this.state.sending || this.state.messages.some((message) => message.streaming)) return
    try {
      const activeRes = await apiFetch(this.activeUrl('/chat/active'))
      if (!activeRes.ok) return
      const active = await activeRes.json()
      if (active.session_id) this.setCurrentSession(active.session_id)
      if (!active.running || !active.run_id) {
        const diagnostic = active.diagnostic
        if (diagnostic?.detail && diagnostic.scope !== 'profile') {
          const content = [
            `[${diagnostic.title || '运行状态'}] ${diagnostic.detail}`,
            diagnostic.recovery_action ? `建议：${diagnostic.recovery_action}` : '',
          ].filter(Boolean).join('\n\n')
          this.setState((prev) => {
            const last = prev.messages[prev.messages.length - 1]
            if (last?.role === 'assistant' && last.content === content) return prev
            return {
              ...prev,
              loaded: true,
              messages: [...prev.messages.filter((message: AgentMessage) => !message.streaming), { role: 'assistant', content, createdAt: nowIso() }],
            }
          })
          return
        }
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
          ...prev.messages.filter((message: AgentMessage) => !message.streaming),
          { role: 'assistant', content: active.content || '', createdAt: active.started_at || nowIso(), streaming: true },
        ],
      }))
      if (active.progress) this.updateAssistantProgress(active.progress)

      const offset = Math.max(0, active.event_count || 0)
      const streamRes = await apiStreamFetch(this.activeUrl('/chat/active/stream', active.session_id || this.state.currentSessionId, `offset=${encodeURIComponent(String(offset))}`), {
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

  private reconnectActiveRun = async () => {
    const activeRes = await apiFetch(this.activeUrl('/chat/active'))
    if (!activeRes.ok) return false
    const active = await activeRes.json()
    if (active.session_id) this.setCurrentSession(active.session_id)
    if (!active.run_id) return false
    if (!active.running) {
      if (active.content) this.replaceAssistantContent(active.content)
      if (active.progress) this.updateAssistantProgress(active.progress)
      await this.fetchHistory({ force: true })
      return true
    }
    this.activeRunId = active.run_id
    if (active.progress) this.updateAssistantProgress(active.progress)
    const offset = Math.max(0, active.event_count || 0)
    const streamRes = await apiStreamFetch(this.activeUrl('/chat/active/stream', active.session_id || this.state.currentSessionId, `offset=${encodeURIComponent(String(offset))}`), {
      headers: { Accept: 'text/event-stream' },
      signal: this.abortController?.signal,
    })
    await this.consumeEventStream(streamRes)
    return true
  }

  private recoverCompletedRunFromHistory = async (messageCountBeforeSend: number) => {
    const pendingMessages = this.state.messages
    await this.fetchHistory({ force: true })
    const last = this.state.messages[this.state.messages.length - 1]
    const recovered = this.state.messages.length >= messageCountBeforeSend + 2 && last?.role === 'assistant' && !last.streaming
    if (!recovered) {
      this.setState((prev) => ({ ...prev, messages: pendingMessages }))
    }
    return recovered
  }

  sendMessage = async (text?: string, context?: AgentChatContext, displayMessage?: string) => {
    const content = (text || this.state.input).trim()
    const attachments = [...this.state.attachments]
    if ((!content && attachments.length === 0) || this.state.sending || this.state.uploadingAttachments) return
    const messageCountBeforeSend = this.state.messages.length
    const visibleContent = (displayMessage || content).trim() || (attachments.length ? '请分析这些附件' : content)
    const userCreatedAt = nowIso()
    const assistantCreatedAt = nowIso()

    this.setState((prev) => ({
      ...prev,
      input: '',
      attachments: [],
      sending: true,
      messages: [
        ...prev.messages,
        { role: 'user', content: visibleContent, createdAt: userCreatedAt, attachments },
        { role: 'assistant', content: '', createdAt: assistantCreatedAt, streaming: true, progress: { status: 'queued', title: '任务已提交', detail: '正在连接智能体', percent: 0, source: 'runtime' } },
      ],
    }))
    const abort = new AbortController()
    this.abortController = abort
    const payload = {
      message: content || visibleContent,
      session_id: this.state.currentSessionId,
      display_message: visibleContent,
      context,
      attachments,
    }

    try {
      const res = await apiStreamFetch(`${this.apiPrefix}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify(payload),
        signal: abort.signal,
      })

      await this.consumeEventStream(res)
      this.finishStreamingMessage()
      await this.fetchHistory({ force: true })
      await this.loadSessions(true)
    } catch (e) {
      const error = e as Error
      if (error.name === 'AbortError') {
        this.failStreamingMessage(error)
        return
      }
      this.updateAssistantProgress({
        status: 'running',
        title: '正在恢复状态流',
        detail: this.activeRunId
          ? '后台 Hermes run 已创建，正在重新连接工具调用状态。'
          : '连接中断，正在检查后台 Hermes run 是否已经启动。',
        source: 'runtime',
      })
      try {
        const reconnected = await this.reconnectActiveRun()
        const recoveredFromHistory = reconnected ? false : await this.recoverCompletedRunFromHistory(messageCountBeforeSend)
        if (!reconnected && !recoveredFromHistory) {
          this.failStreamingMessage(error)
          return
        }
        this.finishStreamingMessage()
        await this.fetchHistory({ force: true })
        await this.loadSessions(true)
      } catch (reconnectError) {
        this.failStreamingMessage(reconnectError as Error)
        return
      }
    } finally {
      this.clearFirstEventTimer()
      this.setState((prev) => ({ ...prev, sending: false }))
      this.abortController = null
      this.activeRunId = null
    }
  }

  clearChat = async () => {
    if (this.state.sending) return
    try {
      const res = await apiFetch(this.activeUrl('/chat/session'), { method: 'DELETE' })
      if (res.ok) {
        const data = await res.json().catch(() => null)
        if (data?.session_id) this.setCurrentSession(data.session_id)
      }
    } catch {
      /* ignore */
    }
    this.setState((prev) => ({ ...prev, messages: [], loaded: true }))
    await this.loadSessions(true)
  }

  newChat = async () => {
    if (this.state.sending) return
    try {
      const res = await apiFetch(`${this.apiPrefix}/chat/session`, { method: 'POST' })
      if (res.ok) {
        const data = await res.json().catch(() => null)
        if (data?.session_id) this.setCurrentSession(data.session_id)
      }
    } catch {
      /* ignore */
    }
    this.historyPromise = null
    this.setState((prev) => ({ ...prev, messages: [], input: '', loaded: true }))
    await this.loadSessions(true)
  }

  stop = async () => {
    const runId = this.activeRunId
    this.clearFirstEventTimer()
    this.abortController?.abort()
    this.abortController = null
    this.activeRunId = null
    this.setState((prev) => {
      const last = prev.messages[prev.messages.length - 1]
      if (last?.role !== 'assistant') return { ...prev, sending: false }
      return {
        ...prev,
        messages: [
          ...prev.messages.slice(0, -1),
          {
            ...last,
            content: STOPPED_MESSAGE,
            streaming: false,
            progress: { status: 'stopped', title: '任务已停止', detail: STOPPED_MESSAGE, source: 'runtime' },
          },
        ],
        sending: false,
      }
    })
    if (runId) {
      try {
        await apiFetch(this.activeUrl('/chat/stop'), { method: 'POST' })
      } catch {
        /* ignore */
      }
    }
  }
}

const stores = new Map<string, AgentChatStore>()

export function getAgentChatStore(apiPrefix: string, authKey: string) {
  const normalizedPrefix = apiPrefix.replace(/\/$/, '')
  const key = `${normalizedPrefix}|${authKey}`
  let store = stores.get(key)
  if (!store) {
    store = new AgentChatStore(normalizedPrefix)
    stores.set(key, store)
  }
  return store
}

export function resetAgentChatStores() {
  for (const store of stores.values()) {
    store.reset()
  }
  stores.clear()
}

export { AgentChatStore }
