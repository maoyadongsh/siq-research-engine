import { useState, useRef, useEffect, useCallback } from 'react'
import {
  X,
  History,
  Trash2,
  Plus,
  Database,
  FileText,
  Search,
} from 'lucide-react'
import AgentFairy, { type AgentFairyState } from './AgentFairy'
import SessionHistoryList from './SessionHistoryList'
import ClearChatConfirmDialog from './ClearChatConfirmDialog'
import ChatComposer from './ChatComposer'
import ChatHeader from './ChatHeader'
import ChatMessageList, { type ChatQuickQuestion } from './ChatMessageList'
import ChatShell from './ChatShell'
import AgentProgressCard from '../agent/AgentProgressCard'
import type { VoiceRecorderFailure, VoiceRecording } from './useVoiceRecorder'
import { useToast } from '../../hooks/useToast'
import { useAgentChat, type AgentMessage } from '../../lib/useAgentChat'
import { useAutosizeTextarea } from '../../lib/useAutosizeTextarea'
import { copyText } from '../../lib/clipboard'
import { assistantQuickQuestions, quickQuestionLabel, quickQuestionPrompt } from '../../lib/quickQuestions'

function messageFairyState(msg: AgentMessage): AgentFairyState {
  if (msg.content.startsWith('[错误]')) return 'error'
  if (msg.streaming && msg.content) return 'replying'
  if (msg.streaming) return 'thinking'
  return 'idle'
}

export default function ChatBot() {
  const [open, setOpen] = useState(false)

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed z-50 flex h-16 w-16 items-center justify-center rounded-full border border-white/80 bg-white text-white shadow-[0_16px_44px_rgba(15,23,42,0.16)] transition-transform hover:scale-105"
        style={{
          bottom: 'max(1.25rem, env(safe-area-inset-bottom))',
          right: 'max(1.25rem, env(safe-area-inset-right))',
        }}
        aria-label="打开财报助手"
      >
        <AgentFairy state="idle" size="md" imageSrc="/agent/siq-avatar-preview.webp" />
      </button>
    )
  }

  return <OpenChatBot onClose={() => setOpen(false)} />
}

function OpenChatBot({ onClose }: { onClose: () => void }) {
  const { toast } = useToast()
  const [historyOpen, setHistoryOpen] = useState(false)
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false)
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
    sendMessage,
    transcribeVoice,
    uploadAttachments,
    removeAttachment,
    newChat,
    loadSessions,
    switchSession,
    clearChat,
    stop,
  } = useAgentChat('/api')
  const messagesEnd = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const assistantStreaming = messages.some((msg) => msg.role === 'assistant' && msg.streaming)
  const assistantHasContent = messages.some((msg) => msg.role === 'assistant' && msg.streaming && msg.content)
  const hadError = messages.some((msg) => msg.role === 'assistant' && msg.content.startsWith('[错误]'))
  const fairyState: AgentFairyState = hadError ? 'error' : assistantHasContent ? 'replying' : assistantStreaming || sending ? 'thinking' : 'idle'
  const fallbackProgress = (sending || assistantStreaming) ? {
    status: 'running' as const,
    title: '正在执行任务',
    detail: '正在连接智能体并处理当前问题',
    source: 'runtime' as const,
  } : undefined
  useAutosizeTextarea(textareaRef, input)

  const scrollToBottom = useCallback(() => {
    messagesEnd.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  useEffect(() => {
    if (!sessionsLoaded && !loadingSessions) {
      loadSessions().catch(() => {})
    }
  }, [loadSessions, loadingSessions, sessionsLoaded])

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

  const handleVoiceRecording = async (recording: VoiceRecording) => {
    const result = await transcribeVoice(recording)
    void sendMessage(result.text, undefined, result.text, [result.attachment]).catch((error) => {
      toast({
        type: 'error',
        title: '语音消息发送失败',
        description: error instanceof Error ? error.message : '请重试。',
      })
    })
  }

  const handleVoiceError = (failure: VoiceRecorderFailure) => {
    toast({ type: 'error', title: '语音输入失败', description: failure.message })
  }

  const copyMessage = async (content: string) => {
    if (await copyText(content)) {
      toast({ type: 'success', title: '消息已复制' })
    } else {
      toast({ type: 'error', title: '复制失败', description: '浏览器未授权剪贴板访问，请手动选中文本复制。' })
    }
  }

  const handleRefreshHistory = async () => {
    await loadSessions()
    setHistoryOpen(true)
  }

  const handleNewChat = async () => {
    await newChat()
    setHistoryOpen(false)
  }

  const handleClearChat = async () => {
    await clearChat()
    setHistoryOpen(false)
  }

  const handleSwitchSession = async (sessionId: string) => {
    await switchSession(sessionId)
    setHistoryOpen(false)
  }

  const quickQuestions: ChatQuickQuestion[] = assistantQuickQuestions.map((q) => {
    const label = quickQuestionLabel(q)
    const featured = typeof q !== 'string' && q.featured
    return {
      key: label,
      label,
      featured,
      onClick: () => { sendMessage(quickQuestionPrompt(q), undefined, label).catch(() => {}) },
    }
  })

  const visibleSessions = sessions
    .filter((session) => Number(session.message_count || 0) > 0 && Boolean(session.title?.trim() || session.preview?.trim()))
    .slice(0, 8)

  return (
    <div
      className="global-chat-dialog fixed inset-0 z-50 flex items-center justify-center bg-slate-950/20 p-0 backdrop-blur-sm sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-label="财报问答助手"
    >
      <div className="global-chat-dialog-shell grid h-[100dvh] w-full min-w-0 grid-cols-1 overflow-hidden border-0 bg-card shadow-none sm:h-[min(760px,calc(100dvh-3rem))] sm:w-[min(1180px,calc(100dvw-3rem))] sm:rounded-2xl sm:border sm:border-border sm:shadow-[0_30px_90px_rgba(15,23,42,0.22)] lg:grid-cols-[220px_minmax(0,1fr)_250px]">
        <aside className="hidden min-h-0 flex-col border-r border-border bg-bg/80 p-5 lg:flex">
          <div className="chat-brand-mark mb-7" aria-label="SIQ">SIQ</div>
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="text-xs font-bold uppercase tracking-wider text-text-muted">会话历史</h2>
            <button
              type="button"
              onClick={() => { handleNewChat().catch(() => {}) }}
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-text-muted hover:bg-white hover:text-primary"
              aria-label="新建会话"
              title="新建会话"
            >
              <Plus className="h-4 w-4" />
            </button>
          </div>
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
            {loadingSessions && visibleSessions.length === 0 ? (
              <p className="rounded-xl border border-border bg-white/70 px-3 py-3 text-xs text-text-muted">正在加载会话…</p>
            ) : visibleSessions.length === 0 ? (
              <p className="rounded-xl border border-border bg-white/70 px-3 py-3 text-xs leading-5 text-text-muted">暂无历史会话</p>
            ) : visibleSessions.map((session) => (
              <button
                key={session.session_id}
                type="button"
                onClick={() => { handleSwitchSession(session.session_id).catch(() => {}) }}
                className={`w-full rounded-xl border px-3 py-3 text-left transition-colors ${
                  session.current
                    ? 'border-primary/25 bg-primary/5 text-primary'
                    : 'border-border bg-white/70 text-text hover:border-primary/25 hover:bg-white'
                }`}
              >
                <span className="block truncate text-xs font-semibold">{session.title || '未命名会话'}</span>
                <span className="mt-1 block truncate text-[11px] text-text-muted">{session.preview || `${session.message_count} 条消息`}</span>
              </button>
            ))}
          </div>
        </aside>

        <ChatShell
          className="global-chat-window min-w-0 bg-white"
          style={{ height: '100%', width: '100%' }}
          header={
        <ChatHeader
          className="global-chat-header border-b border-border px-5 py-4"
          leadingClassName="flex min-w-0 items-center gap-3"
          framedAvatar={false}
          avatar={<AgentFairy state={fairyState} size="sm" />}
          title="财报问答助手"
          subtitle="面向已入库财报的研究助理"
          titleClassName="truncate text-base font-semibold text-text"
          subtitleClassName="truncate text-xs text-text-muted"
          actionsClassName="flex gap-1"
          actions={
            <>
              <button
                onClick={handleNewChat}
                disabled={sending}
                className="flex h-9 w-9 items-center justify-center rounded-xl text-text-muted hover:bg-bg hover:text-text disabled:opacity-50"
                aria-label="新建会话"
                title="新建会话"
              >
                <Plus className="h-4 w-4" />
              </button>
              <button
                onClick={handleRefreshHistory}
                className="flex h-9 w-9 items-center justify-center rounded-xl text-text-muted hover:bg-bg hover:text-text"
                aria-label="查看历史"
                title="查看历史"
              >
                <History className="h-4 w-4" />
              </button>
              <button
                onClick={() => setClearConfirmOpen(true)}
                disabled={sending}
                className="flex h-9 w-9 items-center justify-center rounded-xl text-text-muted hover:bg-bg hover:text-text disabled:opacity-50"
                aria-label="删除历史"
                title="删除历史"
              >
                <Trash2 className="h-4 w-4" />
              </button>
              <button
                onClick={onClose}
                className="flex h-9 w-9 items-center justify-center rounded-xl text-text-muted hover:bg-red-50 hover:text-error"
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </>
          }
        />
      }
          history={historyOpen ? (
            <div className="lg:hidden">
              <SessionHistoryList
                sessions={sessions}
                loading={loadingSessions}
                loaded={sessionsLoaded}
                compact
                onSelect={handleSwitchSession}
                onClose={() => setHistoryOpen(false)}
              />
            </div>
          ) : null}
          messages={
        <ChatMessageList
          messages={messages}
          endRef={messagesEnd}
          auditTraceApiPrefix="/api"
          compact
          emptyAvatar={<AgentFairy state={fairyState} size="float" className="mb-3" />}
          emptyDescription="你好！我是财报分析助手，可以回答关于已入库财报的问题。"
          emptyClassName="flex flex-col items-center py-8 text-center"
          emptyDescriptionClassName="mb-4 text-sm text-text-muted"
          quickQuestions={quickQuestions}
          onCopyMessage={copyMessage}
          renderStreamingAvatar={(msg) => (
            <div className="pointer-events-none mr-2 mt-auto -mb-2 shrink-0 self-end">
              <AgentFairy state={messageFairyState(msg)} size="lg" label="当前助手状态" />
            </div>
          )}
          renderProgress={(msg) => msg.streaming ? (
            <AgentProgressCard progress={msg.progress ?? fallbackProgress} compact />
          ) : null}
          userMessageClassName="chat-message-bubble w-fit max-w-full rounded-[18px] rounded-br-md border border-border bg-bg px-4 py-3 text-sm leading-relaxed text-text"
          assistantMessageClassName="chat-message-bubble w-fit max-w-full rounded-[18px] rounded-bl-md border border-border bg-bg px-4 py-3 text-sm leading-relaxed text-text"
          messageGapClassName="space-y-4"
        />
          }
          messagesClassName="global-chat-messages flex-1 overflow-y-auto px-5 py-5"
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
          onSend={() => { sendMessage().catch(() => {}) }}
          onStop={stop}
          onNewChat={() => { handleNewChat().catch(() => {}) }}
          onAttachmentChange={(files) => { handleAttachmentChange(files).catch(() => {}) }}
          onRemoveAttachment={removeAttachment}
          voice={{ onRecordingComplete: handleVoiceRecording, onError: handleVoiceError }}
          placeholder="输入你的问题…"
          compact
          textareaIconSize="h-4 w-4"
        />
          }
          composerClassName="global-chat-composer chat-composer-section px-5 py-3"
          clearDialog={
        <ClearChatConfirmDialog
          open={clearConfirmOpen}
          disabled={sending}
          onOpenChange={setClearConfirmOpen}
          onConfirm={handleClearChat}
        />
          }
        />

        <aside className="hidden min-h-0 flex-col border-l border-border bg-bg/70 p-5 lg:flex">
          <h2 className="mb-5 text-xs font-bold uppercase tracking-wider text-text-muted">研究上下文</h2>
          <div className="space-y-3">
            <div className="rounded-xl border border-border bg-white/75 p-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-text"><Database className="h-4 w-4 text-primary" />数据来源</div>
              <p className="mt-2 text-xs leading-5 text-text-muted">年报、公告、Wiki 与结构化财务数据</p>
            </div>
            <div className="rounded-xl border border-border bg-white/75 p-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-text"><FileText className="h-4 w-4 text-primary" />报告范围</div>
              <p className="mt-2 text-xs leading-5 text-text-muted">指标、经营质量、风险点与证据链</p>
            </div>
            <div className="rounded-xl border border-border bg-white/75 p-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-text"><Search className="h-4 w-4 text-primary" />追问建议</div>
              <p className="mt-2 text-xs leading-5 text-text-muted">要求引用原文、解释差异或比较历年趋势</p>
            </div>
          </div>
          <p className="mt-auto border-t border-border pt-4 text-xs leading-5 text-text-muted">回答会结合当前工作台中的研究材料。</p>
        </aside>
      </div>
    </div>
  )
}
