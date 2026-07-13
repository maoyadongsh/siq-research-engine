import { useState, useRef, useEffect, useCallback } from 'react'
import {
  X,
  Minus,
  History,
  Trash2,
  Plus,
} from 'lucide-react'
import AgentFairy, { type AgentFairyState } from './AgentFairy'
import SessionHistoryList from './SessionHistoryList'
import ClearChatConfirmDialog from './ClearChatConfirmDialog'
import ChatComposer from './ChatComposer'
import ChatHeader from './ChatHeader'
import ChatMessageList, { type ChatQuickQuestion } from './ChatMessageList'
import ChatShell from './ChatShell'
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
  const [minimized, setMinimized] = useState(false)
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
  useAutosizeTextarea(textareaRef, input)

  const scrollToBottom = useCallback(() => {
    messagesEnd.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

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
    setHistoryOpen(true)
    await loadSessions()
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

  return (
    <ChatShell
      minimized={minimized}
      className="global-chat-window fixed z-50 rounded-[var(--radius-panel)] border border-border bg-white/96 shadow-[0_24px_80px_rgba(15,23,42,0.18)] backdrop-blur-2xl"
      style={{
        position: 'fixed',
        bottom: 'max(1rem, env(safe-area-inset-bottom))',
        right: 'max(1rem, env(safe-area-inset-right))',
        height: minimized ? 'auto' : 'min(720px, calc(100dvh - 2rem))',
        width: 'min(480px, calc(100vw - 2rem))',
      }}
      header={
        <ChatHeader
          className="border-b border-border px-4 py-3"
          leadingClassName="flex min-w-0 items-center gap-2"
          framedAvatar={false}
          avatar={<AgentFairy state={fairyState} size="sm" />}
          title="财报助手"
          titleClassName="truncate text-sm font-semibold text-text"
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
                onClick={() => setMinimized((v) => !v)}
                className="flex h-9 w-9 items-center justify-center rounded-xl text-text-muted hover:bg-bg hover:text-text"
                aria-label={minimized ? '展开' : '最小化'}
                title={minimized ? '展开' : '最小化'}
              >
                {minimized ? <Plus className="h-4 w-4" /> : <Minus className="h-4 w-4" />}
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
        <SessionHistoryList
          sessions={sessions}
          loading={loadingSessions}
          loaded={sessionsLoaded}
          compact
          onSelect={handleSwitchSession}
          onClose={() => setHistoryOpen(false)}
        />
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
          userMessageClassName="chat-message-bubble w-fit max-w-full rounded-[18px] rounded-br-md bg-primary px-4 py-2.5 text-sm leading-relaxed text-white"
          assistantMessageClassName="chat-message-bubble w-fit max-w-full rounded-[18px] rounded-bl-md border border-border bg-white/82 px-4 py-2.5 text-sm leading-relaxed text-text"
          messageGapClassName="space-y-3"
        />
      }
      messagesClassName="flex-1 overflow-y-auto px-4 py-3"
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
      composerClassName="chat-composer-section px-4 py-2"
      clearDialog={
        <ClearChatConfirmDialog
          open={clearConfirmOpen}
          disabled={sending}
          onOpenChange={setClearConfirmOpen}
          onConfirm={handleClearChat}
        />
      }
    />
  )
}
