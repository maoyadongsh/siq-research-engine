import { useRef, useEffect, useCallback, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import {
  Send,
  Loader2,
  Trash2,
  History,
  Plus,
  ChevronRight,
  ChevronLeft,
  Copy,
} from 'lucide-react'
import { Tooltip, useToast } from '../ui'
import { useAgentChat, type AgentChatContext } from '../../lib/useAgentChat'
import { useAutosizeTextarea } from '../../lib/useAutosizeTextarea'
import AgentAvatar, { agentKindFromApiPrefix, type AgentAvatarState } from './AgentAvatar'
import type { AgentMessage } from '../../lib/useAgentChat'
import MessageRenderer from '../chat/MessageRenderer'
import SessionHistoryList from '../chat/SessionHistoryList'
import { copyText } from '../../lib/clipboard'

export interface AgentChatPanelProps {
  apiPrefix: string
  title: string
  description: string
  quickQuestions?: string[]
  context?: AgentChatContext
  collapsed: boolean
  onToggle: () => void
}

const PANEL_WIDTH_STORAGE_KEY = 'finsight_agent_panel_width'
const MIN_PANEL_WIDTH = 340
const DEFAULT_PANEL_WIDTH = 380

function getMaxPanelWidth() {
  if (typeof window === 'undefined') return 720
  return Math.max(MIN_PANEL_WIDTH, Math.floor(window.innerWidth * 0.5))
}

function clampPanelWidth(value: number, max = getMaxPanelWidth()) {
  return Math.min(Math.max(Math.round(value), MIN_PANEL_WIDTH), max)
}

function readStoredPanelWidth() {
  if (typeof window === 'undefined') return DEFAULT_PANEL_WIDTH
  const stored = window.localStorage.getItem(PANEL_WIDTH_STORAGE_KEY)
  const parsed = stored ? Number(stored) : DEFAULT_PANEL_WIDTH
  return Number.isFinite(parsed) ? parsed : DEFAULT_PANEL_WIDTH
}

function messageAvatarState(msg: AgentMessage): AgentAvatarState {
  if (msg.content.startsWith('[错误]')) return 'error'
  if (msg.streaming && msg.content) return 'replying'
  if (msg.streaming) return 'thinking'
  return 'idle'
}

export default function AgentChatPanel({
  apiPrefix,
  title,
  description,
  quickQuestions = [],
  context,
  collapsed,
  onToggle,
}: AgentChatPanelProps) {
  const { toast } = useToast()
  const [historyOpen, setHistoryOpen] = useState(false)
  const [maxPanelWidth, setMaxPanelWidth] = useState(getMaxPanelWidth)
  const [panelWidth, setPanelWidth] = useState(() => clampPanelWidth(readStoredPanelWidth()))
  const {
    messages,
    sessions,
    input,
    setInput,
    sending,
    composing,
    setComposing,
    sendMessage,
    newChat,
    loadSessions,
    switchSession,
    clearChat,
    stop,
  } = useAgentChat(apiPrefix)

  const messagesEnd = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const resizeStart = useRef({ x: 0, width: DEFAULT_PANEL_WIDTH })
  const avatarKind = agentKindFromApiPrefix(apiPrefix)
  const runningMessage = [...messages].reverse().find((msg) => msg.role === 'assistant' && msg.streaming)
  const lastAssistantMessage = [...messages].reverse().find((msg) => msg.role === 'assistant')
  const hasError = Boolean(lastAssistantMessage?.content.startsWith('[错误]'))
  const avatarState: AgentAvatarState = runningMessage
    ? messageAvatarState(runningMessage)
    : sending
      ? 'thinking'
      : hasError
        ? 'error'
        : 'idle'
  useAutosizeTextarea(textareaRef, input)

  const scrollToBottom = useCallback(() => {
    messagesEnd.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    if (!collapsed) scrollToBottom()
  }, [messages, collapsed, scrollToBottom])

  useEffect(() => {
    const handleResize = () => {
      const nextMax = getMaxPanelWidth()
      setMaxPanelWidth(nextMax)
      setPanelWidth((current) => clampPanelWidth(current, nextMax))
    }

    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  useEffect(() => {
    window.localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, String(panelWidth))
  }, [panelWidth])

  const resizePanel = useCallback((nextWidth: number) => {
    const nextMax = getMaxPanelWidth()
    setMaxPanelWidth(nextMax)
    setPanelWidth(clampPanelWidth(nextWidth, nextMax))
  }, [])

  const startResize = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return
    event.preventDefault()
    resizeStart.current = { x: event.clientX, width: panelWidth }
    document.body.classList.add('agent-panel-resizing')

    const handleMove = (moveEvent: PointerEvent) => {
      const delta = resizeStart.current.x - moveEvent.clientX
      resizePanel(resizeStart.current.width + delta)
    }

    const stopResize = () => {
      document.body.classList.remove('agent-panel-resizing')
      window.removeEventListener('pointermove', handleMove)
      window.removeEventListener('pointerup', stopResize)
      window.removeEventListener('pointercancel', stopResize)
    }

    window.addEventListener('pointermove', handleMove)
    window.addEventListener('pointerup', stopResize)
    window.addEventListener('pointercancel', stopResize)
  }, [panelWidth, resizePanel])

  const resizeWithKeyboard = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 48 : 16
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      resizePanel(panelWidth + step)
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      resizePanel(panelWidth - step)
    } else if (event.key === 'Home') {
      event.preventDefault()
      resizePanel(MIN_PANEL_WIDTH)
    } else if (event.key === 'End') {
      event.preventDefault()
      resizePanel(getMaxPanelWidth())
    }
  }, [panelWidth, resizePanel])

  const copyMessage = async (content: string) => {
    if (await copyText(content)) {
      toast({ type: 'success', title: '消息已复制' })
    } else {
      toast({ type: 'error', title: '复制失败', description: '浏览器未授权剪贴板访问，请手动选中文本复制。' })
    }
  }

  const showHistory = async () => {
    const list = await loadSessions()
    setHistoryOpen(true)
    toast({
      type: 'success',
      title: list.length ? `已找到 ${list.length} 个历史会话` : '当前没有历史会话',
    })
  }

  const openSession = async (sessionId: string) => {
    await switchSession(sessionId)
    setHistoryOpen(false)
    toast({ type: 'success', title: '已打开历史会话' })
    scrollToBottom()
  }

  const createNewChat = async () => {
    await newChat()
    setHistoryOpen(false)
    toast({ type: 'success', title: '已新建会话' })
  }

  const deleteHistory = async () => {
    await clearChat()
    toast({ type: 'success', title: '历史会话已删除' })
  }

  if (collapsed) {
    return (
      <div className="premium-shell flex h-full w-14 shrink-0 flex-col items-center rounded-[22px] py-4">
        <Tooltip content={`展开${title}`}>
          <button
            onClick={onToggle}
            className="icon-button"
            aria-label={`展开${title}`}
          >
            <ChevronLeft className="h-5 w-5" />
          </button>
        </Tooltip>
        <div
          className="mt-3 flex-1 text-xs font-semibold text-text-muted"
          style={{ writingMode: 'vertical-rl' }}
        >
          {title}
        </div>
        <Tooltip content={`展开${title}`}>
          <button
            onClick={onToggle}
            className="mb-1 rounded-2xl transition-transform hover:scale-105 focus-visible:outline focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-primary/25"
            aria-label={`展开${title}`}
          >
            <AgentAvatar kind={avatarKind} state={avatarState} size="sm" label={title} />
          </button>
        </Tooltip>
      </div>
    )
  }

  return (
    <div
      className="premium-shell relative flex h-full shrink-0 flex-col overflow-hidden rounded-[26px] min-h-0"
      style={{ width: `min(${panelWidth}px, calc(100vw - 1rem))` }}
    >
      <div
        className="agent-panel-resize-handle"
        role="separator"
        aria-label="调整助手宽度"
        aria-orientation="vertical"
        aria-valuemin={MIN_PANEL_WIDTH}
        aria-valuemax={maxPanelWidth}
        aria-valuenow={panelWidth}
        tabIndex={0}
        onPointerDown={startResize}
        onKeyDown={resizeWithKeyboard}
      />
      <div className="flex shrink-0 items-center justify-between border-b border-border/80 bg-white/52 px-4 py-3 backdrop-blur">
        <div className="flex min-w-0 items-center gap-3">
          <div className="premium-icon h-12 w-12 shrink-0 rounded-2xl">
            <AgentAvatar kind={avatarKind} state={avatarState} size="sm" label={title} />
          </div>
          <div className="min-w-0">
            <div className="truncate text-base font-semibold tracking-tight text-text">{title}</div>
            <div className="truncate text-xs font-medium text-text-muted">
              {avatarState === 'thinking'
                ? '正在思考'
                : avatarState === 'replying'
                  ? '正在输出'
                  : avatarState === 'error'
                    ? '运行异常'
                    : '随时待命'}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <Tooltip content="新建会话">
            <button
              onClick={createNewChat}
              disabled={sending}
              className="icon-button h-10 w-10 min-h-10 min-w-10 disabled:opacity-50"
              aria-label="新建会话"
            >
              <Plus className="h-4 w-4" />
            </button>
          </Tooltip>
          <Tooltip content="查看历史">
            <button
              onClick={showHistory}
              className="icon-button h-10 w-10 min-h-10 min-w-10"
              aria-label="查看历史"
            >
              <History className="h-4 w-4" />
            </button>
          </Tooltip>
          <Tooltip content="清空会话">
            <button
              onClick={deleteHistory}
              disabled={sending}
              className="icon-button h-10 w-10 min-h-10 min-w-10 disabled:opacity-50"
              aria-label="删除历史"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </Tooltip>
          <Tooltip content="收起">
            <button
              onClick={onToggle}
              className="icon-button h-10 w-10 min-h-10 min-w-10"
              aria-label="收起助手"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </Tooltip>
        </div>
      </div>

      {historyOpen && (
        <SessionHistoryList
          sessions={sessions}
          compact
          onSelect={openSession}
          onClose={() => setHistoryOpen(false)}
        />
      )}

      <div className="flex-1 overflow-y-auto px-4 py-4 min-h-0">
        {messages.length === 0 && (
          <div className="flex flex-col items-center py-6 text-center">
            <AgentAvatar kind={avatarKind} state={avatarState} size="xl" className="mb-3" label={title} />
            <p className="mb-4 max-w-[18rem] text-sm leading-6 text-text-muted">{description}</p>
            {quickQuestions.length > 0 && (
              <div className="flex flex-wrap justify-center gap-2">
                {quickQuestions.map((q) => (
                  <button
                    key={q}
                    onClick={() => sendMessage(q, context)}
                    className="premium-chip min-h-9 text-text-muted transition-colors hover:border-primary/20 hover:bg-primary/5 hover:text-primary"
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="space-y-3">
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex items-start gap-2 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              {msg.role === 'assistant' && msg.streaming && i === messages.length - 1 && (
                <div className="pointer-events-none mr-1 mt-auto -mb-1 shrink-0 self-end">
                  <AgentAvatar
                    kind={avatarKind}
                    state={messageAvatarState(msg)}
                    size="lg"
                    label="当前智能体运行状态"
                  />
                </div>
              )}
              <div
                className={`rounded-[20px] px-3.5 py-2.5 text-sm leading-relaxed shadow-sm ${msg.role === 'user' ? 'max-w-[84%]' : 'max-w-[94%]'} ${
                  msg.role === 'user'
                    ? 'rounded-br-md bg-primary text-white'
                    : 'rounded-bl-md border border-border bg-white/82 text-text'
                }`}
              >
                {msg.content ? (
                  <MessageRenderer
                    content={msg.content}
                    streaming={msg.streaming}
                    variant={msg.role === 'user' ? 'user' : 'assistant'}
                  />
                ) : (
                  msg.streaming ? '正在思考...' : ''
                )}
                {msg.streaming && msg.content && (
                  <span className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-primary" />
                )}
                {msg.content && !msg.streaming && (
                  <div className="chat-message-actions">
                    <button
                      type="button"
                      className="chat-message-copy"
                      onClick={() => copyMessage(msg.content)}
                      aria-label="复制消息"
                    >
                      <Copy className="h-3 w-3" />
                      复制
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
        <div ref={messagesEnd} />
      </div>

      <div className="chat-composer-section shrink-0 px-4 py-3">
        <div className="chat-composer-wrap">
          <div className="chat-composer-field">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !composing) {
                e.preventDefault()
                sendMessage(undefined, context)
              }
            }}
            onCompositionStart={() => setComposing(true)}
            onCompositionEnd={() => setComposing(false)}
            placeholder="输入问题..."
            rows={1}
            className="chat-composer-textarea chat-composer-textarea-compact"
          />
            <div className="chat-composer-footer">
              <span className="chat-composer-hint">Enter 发送 · Shift+Enter 换行</span>
              <div className="chat-composer-actions">
                <button
                  type="button"
                  onClick={createNewChat}
                  disabled={sending}
                  className="chat-composer-tool"
                  aria-label="新建会话"
                  title="新建会话"
                >
                  <Plus className="h-4 w-4" />
                </button>
                {sending && (
                  <button onClick={stop} className="chat-composer-stop">
                    停止
                  </button>
                )}
                <button
                  onClick={() => sendMessage(undefined, context)}
                  disabled={sending || !input.trim()}
                  className="chat-composer-send chat-composer-send-sm"
                  aria-label="发送消息"
                >
                  {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
