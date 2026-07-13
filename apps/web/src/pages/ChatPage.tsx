import { useState, useRef, useEffect, useCallback } from 'react'
import {
  Trash2,
  History,
  Plus,
} from 'lucide-react'
import AgentFairy, { type AgentFairyState } from '../components/chat/AgentFairy'
import AgentProgressCard from '../components/agent/AgentProgressCard'
import SessionHistoryList from '../components/chat/SessionHistoryList'
import ClearChatConfirmDialog from '../components/chat/ClearChatConfirmDialog'
import ChatComposer from '../components/chat/ChatComposer'
import ChatHeader from '../components/chat/ChatHeader'
import ChatMessageList, { type ChatQuickQuestion } from '../components/chat/ChatMessageList'
import ChatShell from '../components/chat/ChatShell'
import type { VoiceRecorderFailure, VoiceRecording } from '../components/chat/useVoiceRecorder'
import { useToast } from '../hooks/useToast'
import { useAgentChat, type AgentMessage } from '../lib/useAgentChat'
import { useAutosizeTextarea } from '../lib/useAutosizeTextarea'
import { copyText } from '../lib/clipboard'
import { assistantQuickQuestions, quickQuestionLabel, quickQuestionPrompt } from '../lib/quickQuestions'

function messageFairyState(msg: AgentMessage): AgentFairyState {
  if (msg.content.startsWith('[错误]')) return 'error'
  if (msg.streaming && msg.content) return 'replying'
  if (msg.streaming) return 'thinking'
  return 'idle'
}

export default function ChatPage() {
  const { toast } = useToast()
  const [historyNotice, setHistoryNotice] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false)
  const messagesEnd = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
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

  const handleSendMessage = async (text?: string, displayText?: string) => {
    setHistoryNotice('')
    await sendMessage(text, undefined, displayText)
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

  const copyMessage = async (content: string) => {
    if (await copyText(content)) {
      toast({ type: 'success', title: '消息已复制' })
    } else {
      toast({ type: 'error', title: '复制失败', description: '浏览器未授权剪贴板访问，请手动选中文本复制。' })
    }
  }

  const handleClearChat = async () => {
    await clearChat()
    setHistoryOpen(false)
    setHistoryNotice('历史会话已删除')
  }

  const handleNewChat = async () => {
    await newChat()
    setHistoryOpen(false)
    setHistoryNotice('已新建会话')
  }

  const showHistory = async () => {
    setHistoryOpen(true)
    setHistoryNotice('正在加载历史会话…')
    const list = await loadSessions()
    setHistoryNotice(list.length ? `已找到 ${list.length} 个历史会话` : '当前没有历史会话')
  }

  const openSession = async (sessionId: string) => {
    await switchSession(sessionId)
    setHistoryOpen(false)
    setHistoryNotice('已打开历史会话')
    scrollToBottom()
  }

  const quickQuestions: ChatQuickQuestion[] = assistantQuickQuestions.map((q) => {
    const label = quickQuestionLabel(q)
    const featured = typeof q !== 'string' && q.featured
    return {
      key: label,
      label,
      featured,
      className: featured ? '' : 'text-primary',
      onClick: () => { handleSendMessage(quickQuestionPrompt(q), label).catch(() => {}) },
    }
  })

  return (
    <ChatShell
      className="chat-page-shell premium-shell rounded-[var(--radius-panel)]"
      style={{ height: 'calc(100dvh - var(--app-topbar-height) - var(--app-content-y))' }}
      header={
        <ChatHeader
          className="gap-3 border-b border-border/80 bg-white/54 px-4 py-3 backdrop-blur sm:px-6 sm:py-4"
          leadingClassName="flex min-w-0 flex-1 items-center gap-2.5 sm:gap-3"
          avatar={<AgentFairy state={fairyState} size="sm" />}
          avatarClassName="premium-icon h-11 w-11 shrink-0 rounded-2xl sm:h-12 sm:w-12"
          title={<h2 className="truncate text-base font-semibold text-text sm:text-2xl">财报问答助手</h2>}
          subtitle="面向已入库财报的研究助理"
          subtitleClassName="hidden truncate text-sm font-medium text-text-muted sm:block"
          actionsClassName="flex shrink-0 items-center justify-end gap-1 sm:gap-2"
          actions={
            <>
              <button
                onClick={handleNewChat}
                disabled={sending}
                className="inline-flex h-11 w-11 min-w-11 items-center justify-center gap-1.5 rounded-xl border border-border bg-white/78 px-0 text-xs font-semibold text-text shadow-sm hover:bg-white disabled:opacity-50 sm:w-auto sm:px-3"
                aria-label="新建会话"
                title="新建会话"
              >
                <Plus className="h-3.5 w-3.5" /><span className="hidden sm:inline">新建会话</span>
              </button>
              <button
                onClick={showHistory}
                className="inline-flex h-11 w-11 min-w-11 items-center justify-center gap-1.5 rounded-xl border border-border bg-white/78 px-0 text-xs font-semibold text-text shadow-sm hover:bg-white sm:w-auto sm:px-3"
                aria-label="查看历史"
                title="查看历史"
              >
                <History className="h-3.5 w-3.5" /><span className="hidden sm:inline">查看历史</span>
              </button>
              <button
                onClick={() => setClearConfirmOpen(true)}
                disabled={sending}
                className="inline-flex h-11 w-11 min-w-11 items-center justify-center gap-1.5 rounded-xl border border-border bg-white/78 px-0 text-xs font-semibold text-text shadow-sm hover:bg-white disabled:opacity-50 sm:w-auto sm:px-3"
                aria-label="删除历史"
                title="删除历史"
              >
                <Trash2 className="h-3.5 w-3.5" /><span className="hidden sm:inline">删除历史</span>
              </button>
            </>
          }
        />
      }
      history={
        <SessionHistoryList
          sessions={sessions}
          loading={loadingSessions}
          loaded={sessionsLoaded}
          onSelect={openSession}
          onClose={() => setHistoryOpen(false)}
          open={historyOpen}
        />
      }
      messages={
        <ChatMessageList
          messages={messages}
          endRef={messagesEnd}
          auditTraceApiPrefix="/api"
          emptyAvatar={<AgentFairy state={fairyState} size="xl" className="mb-4" />}
          emptyDescription="你好！我是财报分析助手，可以回答关于已入库财报的问题。支持数据查询、趋势分析、对比研究等。"
          quickQuestions={quickQuestions}
          notice={historyNotice}
          onCopyMessage={copyMessage}
          renderStreamingAvatar={(msg) => (
            <div className="pointer-events-none mr-3 mt-auto -mb-2 shrink-0 self-end">
              <AgentFairy state={messageFairyState(msg)} size="xl" label="当前助手状态" />
            </div>
          )}
          renderProgress={(msg) => msg.streaming ? <AgentProgressCard progress={msg.progress} /> : null}
          listClassName="chat-page-message-list mx-auto w-full"
        />
      }
      messagesClassName="chat-page-messages flex-1 overflow-y-auto px-4 py-4 sm:px-5 lg:px-6"
      composer={
        <div className="chat-page-composer mx-auto w-full">
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
            onSend={() => { handleSendMessage().catch(() => {}) }}
            onStop={stop}
            onNewChat={() => { handleNewChat().catch(() => {}) }}
            onAttachmentChange={(files) => { handleAttachmentChange(files).catch(() => {}) }}
            onRemoveAttachment={removeAttachment}
            voice={{ onRecordingComplete: handleVoiceRecording, onError: handleVoiceError }}
            placeholder="输入你的问题，Enter 发送，Shift+Enter 换行"
            showNewChat={false}
          />
        </div>
      }
      composerClassName="chat-page-composer-section chat-composer-section px-4 py-3 sm:px-5 lg:px-6"
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
