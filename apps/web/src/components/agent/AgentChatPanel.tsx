import { useRef, useEffect, useCallback, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import {
  Trash2,
  History,
  Plus,
  ChevronRight,
  ChevronLeft,
} from 'lucide-react'
import { Tooltip } from '../ui'
import { useToast } from '../../hooks/useToast'
import { useAgentChat, type AgentChatContext } from '../../lib/useAgentChat'
import { useAutosizeTextarea } from '../../lib/useAutosizeTextarea'
import AgentAvatar, { type AgentAvatarState } from './AgentAvatar'
import { agentKindFromApiPrefix } from './agentAvatarTypes'
import AgentProgressCard from './AgentProgressCard'
import type { AgentMessage } from '../../lib/useAgentChat'
import SessionHistoryList from '../chat/SessionHistoryList'
import ClearChatConfirmDialog from '../chat/ClearChatConfirmDialog'
import ChatComposer from '../chat/ChatComposer'
import ChatHeader from '../chat/ChatHeader'
import ChatMessageList, { type ChatQuickQuestion } from '../chat/ChatMessageList'
import ChatShell from '../chat/ChatShell'
import { copyText } from '../../lib/clipboard'
import { quickQuestionLabel, quickQuestionPrompt, type AgentQuickQuestionInput } from '../../lib/quickQuestions'

export interface AgentChatPanelProps {
  apiPrefix: string
  title: string
  description: string
  quickQuestions?: AgentQuickQuestionInput[]
  quickQuestionClassName?: string
  context?: AgentChatContext
  collapsed: boolean
  onToggle: () => void
}

const PANEL_WIDTH_STORAGE_KEY = 'siq_agent_panel_width'
const MIN_PANEL_WIDTH = 340
const DEFAULT_PANEL_WIDTH = 380
const AGENT_AUTO_INIT_DELAY_MS = 1800

function scheduleIdleWork(callback: () => void) {
  if (typeof window === 'undefined') return () => {}
  let cancelled = false
  let idleId = 0
  const timerId = window.setTimeout(() => {
    if (cancelled) return
    if ('requestIdleCallback' in window) {
      idleId = window.requestIdleCallback(() => {
        if (!cancelled) callback()
      }, { timeout: 2000 })
    } else {
      callback()
    }
  }, AGENT_AUTO_INIT_DELAY_MS)

  return () => {
    cancelled = true
    window.clearTimeout(timerId)
    if (idleId && 'cancelIdleCallback' in window) window.cancelIdleCallback(idleId)
  }
}

function getMaxPanelWidth() {
  if (typeof window === 'undefined') return 720
  if (window.innerWidth < 640) return Math.max(MIN_PANEL_WIDTH, window.innerWidth - 24)
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
  quickQuestionClassName = '',
  context,
  collapsed,
  onToggle,
}: AgentChatPanelProps) {
  const { toast } = useToast()
  const [historyOpen, setHistoryOpen] = useState(false)
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false)
  const [maxPanelWidth, setMaxPanelWidth] = useState(getMaxPanelWidth)
  const [panelWidth, setPanelWidth] = useState(() => clampPanelWidth(readStoredPanelWidth()))
  const {
    messages,
    sessions,
    loadingSessions,
    sessionsLoaded,
    input,
    setInput,
    sending,
    attachments,
    uploadingAttachments,
    composing,
    setComposing,
    initialize,
    sendMessage,
    uploadAttachments,
    removeAttachment,
    newChat,
    loadSessions,
    switchSession,
    clearChat,
    stop,
  } = useAgentChat(apiPrefix, { autoInitialize: false })

  const messagesEnd = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
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
    if (collapsed) return undefined
    return scheduleIdleWork(() => {
      initialize().catch(() => {})
    })
  }, [collapsed, initialize])

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

  const handleAttachmentChange = async (files: FileList | null) => {
    if (!files?.length) return
    try {
      await uploadAttachments(files)
    } catch (error) {
      toast({
        type: 'error',
        title: '附件上传失败',
        description: error instanceof Error ? error.message : '请检查附件格式和大小。',
      })
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const showHistory = async () => {
    setHistoryOpen(true)
    await initialize()
    const list = await loadSessions()
    toast({
      type: 'success',
      title: list.length ? `已找到 ${list.length} 个历史会话` : '当前没有历史会话',
    })
  }

  const openSession = async (sessionId: string) => {
    await initialize()
    await switchSession(sessionId)
    setHistoryOpen(false)
    toast({ type: 'success', title: '已打开历史会话' })
    scrollToBottom()
  }

  const createNewChat = async () => {
    await initialize()
    await newChat()
    setHistoryOpen(false)
    toast({ type: 'success', title: '已新建会话' })
  }

  const deleteHistory = async () => {
    await initialize()
    await clearChat()
    toast({ type: 'success', title: '历史会话已删除' })
  }

  const sendPanelMessage = async (text?: string, messageContext?: AgentChatContext, displayMessage?: string) => {
    await initialize()
    return sendMessage(text, messageContext, displayMessage)
  }

  const quickQuestionItems: ChatQuickQuestion[] = quickQuestions.map((q) => {
    const label = quickQuestionLabel(q)
    const featured = typeof q !== 'string' && q.featured
    return {
      key: label,
      label,
      featured,
      onClick: () => { sendPanelMessage(quickQuestionPrompt(q), context, label).catch(() => {}) },
    }
  })

  const avatarStatusText = avatarState === 'thinking'
    ? '正在思考'
    : avatarState === 'replying'
      ? '正在输出'
      : avatarState === 'error'
        ? '运行异常'
        : '随时待命'

  if (collapsed) {
    return (
      <div className="premium-shell flex h-full w-14 shrink-0 flex-col overflow-hidden rounded-[22px]">
        <Tooltip content={`展开${title}`} className="h-full w-full">
          <button
            type="button"
            onClick={onToggle}
            className="flex h-full w-full flex-col items-center rounded-[22px] px-0 py-4 text-text-muted transition-colors hover:bg-primary/5 hover:text-text focus-visible:outline focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-primary/25"
            aria-label={`展开${title}`}
          >
            <span className="icon-button pointer-events-none">
              <ChevronLeft className="h-5 w-5" />
            </span>
            <span
              className="mt-3 flex-1 text-xs font-semibold"
              style={{ writingMode: 'vertical-rl' }}
            >
              {title}
            </span>
            <span className="mb-1 rounded-2xl transition-transform">
              <AgentAvatar kind={avatarKind} state={avatarState} size="sm" label={title} />
            </span>
          </button>
        </Tooltip>
      </div>
    )
  }

  return (
    <ChatShell
      className="agent-chat-panel premium-shell relative flex h-full min-h-0 shrink-0 flex-col overflow-hidden rounded-[var(--radius-panel)]"
      style={{ width: `min(${panelWidth}px, calc(100vw - 1rem))` }}
      header={
        <>
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
          <ChatHeader
            className="agent-chat-panel-header border-b border-border/80 bg-white/52 px-4 py-3 backdrop-blur"
            avatar={<AgentAvatar kind={avatarKind} state={avatarState} size="sm" label={title} />}
            title={title}
            subtitle={avatarStatusText}
            actions={
              <>
                <Tooltip content="新建会话">
                  <button
                    onClick={createNewChat}
                    disabled={sending}
                    className="icon-button h-11 w-11 min-h-11 min-w-11 disabled:opacity-50"
                    aria-label="新建会话"
                  >
                    <Plus className="h-4 w-4" />
                  </button>
                </Tooltip>
                <Tooltip content="查看历史">
                  <button
                    onClick={showHistory}
                    className="icon-button h-11 w-11 min-h-11 min-w-11"
                    aria-label="查看历史"
                  >
                    <History className="h-4 w-4" />
                  </button>
                </Tooltip>
                <Tooltip content="清空会话">
                  <button
                    onClick={() => setClearConfirmOpen(true)}
                    disabled={sending}
                    className="icon-button h-11 w-11 min-h-11 min-w-11 disabled:opacity-50"
                    aria-label="删除历史"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </Tooltip>
                <Tooltip content="收起">
                  <button
                    onClick={onToggle}
                    className="icon-button h-11 w-11 min-h-11 min-w-11"
                    aria-label="收起助手"
                  >
                    <ChevronRight className="h-4 w-4" />
                  </button>
                </Tooltip>
              </>
            }
          />
        </>
      }
      history={historyOpen ? (
        <SessionHistoryList
          sessions={sessions}
          loading={loadingSessions}
          loaded={sessionsLoaded}
          compact
          onSelect={openSession}
          onClose={() => setHistoryOpen(false)}
        />
      ) : null}
      messages={
        <ChatMessageList
          messages={messages}
          endRef={messagesEnd}
          auditTraceApiPrefix={apiPrefix}
          compact
          emptyAvatar={<AgentAvatar kind={avatarKind} state={avatarState} size="xl" className="mb-3" label={title} />}
          emptyDescription={description}
          quickQuestions={quickQuestionItems}
          quickQuestionClassName={`agent-quick-question-cloud ${quickQuestionClassName}`}
          onCopyMessage={copyMessage}
          renderStreamingAvatar={(msg) => (
            <div className="pointer-events-none mr-1 mt-auto -mb-1 shrink-0 self-end">
              <AgentAvatar
                kind={avatarKind}
                state={messageAvatarState(msg)}
                size="lg"
                label="当前智能体运行状态"
              />
            </div>
          )}
          renderProgress={(msg) => msg.streaming ? <AgentProgressCard progress={msg.progress} compact /> : null}
          userMessageClassName="chat-message-bubble w-fit max-w-full rounded-[18px] rounded-br-md bg-primary px-3.5 py-2.5 text-sm leading-relaxed text-white shadow-sm"
          assistantMessageClassName="chat-message-bubble w-fit max-w-full rounded-[18px] rounded-bl-md border border-border bg-white/82 px-3.5 py-2.5 text-sm leading-relaxed text-text shadow-sm"
          messageGapClassName="space-y-3"
        />
      }
      messagesClassName="agent-chat-panel-messages min-h-0 flex-1 overflow-y-auto px-4 py-4"
      composer={
        <ChatComposer
          input={input}
          setInput={setInput}
          composing={composing}
          setComposing={setComposing}
          sending={sending}
          uploadingAttachments={uploadingAttachments}
          attachments={attachments}
          textareaRef={textareaRef}
          fileInputRef={fileInputRef}
          onSend={() => { sendPanelMessage(undefined, context).catch(() => {}) }}
          onStop={stop}
          onNewChat={() => { createNewChat().catch(() => {}) }}
          onAttachmentChange={(files) => { handleAttachmentChange(files).catch(() => {}) }}
          onRemoveAttachment={removeAttachment}
          placeholder="输入问题…"
          compact
          textareaIconSize="h-4 w-4"
        />
      }
      composerClassName="agent-chat-panel-composer chat-composer-section shrink-0 px-4 py-3"
      clearDialog={
        <ClearChatConfirmDialog
          open={clearConfirmOpen}
          disabled={sending}
          onOpenChange={setClearConfirmOpen}
          onConfirm={deleteHistory}
        />
      }
    />
  )
}
