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
      className="premium-shell rounded-[30px]"
      style={{ height: 'calc(100dvh - var(--app-topbar-height) - var(--app-content-y))' }}
      header={
        <ChatHeader
          className="flex-col gap-4 border-b border-border/80 bg-white/54 px-5 py-4 backdrop-blur sm:flex-row sm:items-center sm:justify-between sm:px-6"
          avatar={<AgentFairy state={fairyState} size="sm" />}
          title={<h2 className="text-2xl font-semibold text-text">财报问答助手</h2>}
          subtitle="面向已入库财报的研究助理"
          subtitleClassName="text-sm font-medium text-text-muted"
          actionsClassName="flex flex-wrap items-center gap-2"
          actions={
            <>
              <button
                onClick={handleNewChat}
                disabled={sending}
                className="inline-flex min-h-10 items-center gap-1.5 rounded-xl border border-border bg-white/78 px-3 text-xs font-semibold text-text shadow-sm hover:bg-white disabled:opacity-50"
              >
                <Plus className="h-3.5 w-3.5" /> 新建会话
              </button>
              <button
                onClick={showHistory}
                className="inline-flex min-h-10 items-center gap-1.5 rounded-xl border border-border bg-white/78 px-3 text-xs font-semibold text-text shadow-sm hover:bg-white"
              >
                <History className="h-3.5 w-3.5" /> 查看历史
              </button>
              <button
                onClick={() => setClearConfirmOpen(true)}
                disabled={sending}
                className="inline-flex min-h-10 items-center gap-1.5 rounded-xl border border-border bg-white/78 px-3 text-xs font-semibold text-text shadow-sm hover:bg-white disabled:opacity-50"
              >
                <Trash2 className="h-3.5 w-3.5" /> 删除历史
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
          onSelect={openSession}
          onClose={() => setHistoryOpen(false)}
        />
      ) : null}
      messages={
        <ChatMessageList
          messages={messages}
          endRef={messagesEnd}
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
          listClassName="mx-auto max-w-3xl"
        />
      }
      messagesClassName="flex-1 overflow-y-auto px-5 py-5 sm:px-6"
      composer={
        <div className="mx-auto max-w-3xl">
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
            placeholder="输入你的问题，Enter 发送，Shift+Enter 换行"
          />
        </div>
      }
      composerClassName="chat-composer-section px-6 py-3"
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
