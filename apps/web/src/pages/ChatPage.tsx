import { useState, useRef, useEffect, useCallback } from 'react'
import {
  Send,
  Loader2,
  Paperclip,
  Trash2,
  History,
  Plus,
  Copy,
} from 'lucide-react'
import ChatAttachmentList from '../components/chat/ChatAttachmentList'
import PetFairy, { type PetFairyState } from '../components/chat/PetFairy'
import AgentProgressCard from '../components/agent/AgentProgressCard'
import MessageRenderer from '../components/chat/MessageRenderer'
import MessageTimestamp from '../components/chat/MessageTimestamp'
import SessionHistoryList from '../components/chat/SessionHistoryList'
import ClearChatConfirmDialog from '../components/chat/ClearChatConfirmDialog'
import { useToast } from '../hooks/useToast'
import { useAgentChat, type AgentMessage } from '../lib/useAgentChat'
import { useAutosizeTextarea } from '../lib/useAutosizeTextarea'
import { copyText } from '../lib/clipboard'
import { assistantQuickQuestions, quickQuestionLabel, quickQuestionPrompt } from '../lib/quickQuestions'

function messageFairyState(msg: AgentMessage): PetFairyState {
  if (msg.content.startsWith('[错误]')) return 'error'
  if (msg.streaming && msg.content) return 'replying'
  if (msg.streaming) return 'thinking'
  return 'idle'
}

export default function ChatPage() {
  const { toast } = useToast()
  const [hadError, setHadError] = useState(false)
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
  const fairyState: PetFairyState = hadError ? 'error' : assistantHasContent ? 'replying' : assistantStreaming || sending ? 'thinking' : 'idle'
  useAutosizeTextarea(textareaRef, input)

  const scrollToBottom = useCallback(() => {
    messagesEnd.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  const handleSendMessage = async (text?: string, displayText?: string) => {
    setHadError(false)
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

  return (
    <div className="premium-shell flex flex-col overflow-hidden rounded-[30px]" style={{ height: 'calc(100dvh - var(--app-topbar-height) - var(--app-content-y))' }}>
      {/* Header */}
      <div className="flex flex-col gap-4 border-b border-border/80 bg-white/54 px-5 py-4 backdrop-blur sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div className="flex items-center gap-3">
          <div className="premium-icon h-12 w-12 rounded-2xl">
            <PetFairy state={fairyState} size="sm" />
          </div>
          <div>
            <h2 className="text-2xl font-semibold tracking-tight text-text">财报问答助手</h2>
            <p className="text-sm font-medium text-text-muted">面向已入库财报的研究助理</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
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
        </div>
      </div>

      {historyOpen && (
        <SessionHistoryList
          sessions={sessions}
          loading={loadingSessions}
          loaded={sessionsLoaded}
          onSelect={openSession}
          onClose={() => setHistoryOpen(false)}
        />
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-5 py-5 sm:px-6">
        {messages.length === 0 && (
          <div className="mx-auto flex max-w-2xl flex-col items-center py-16 text-center">
            <PetFairy state={fairyState} size="xl" className="mb-4" />
            <p className="mb-6 max-w-md text-base leading-7 text-text-muted">
              你好！我是财报分析助手，可以回答关于已入库财报的问题。支持数据查询、趋势分析、对比研究等。
            </p>
            <div className="quick-question-cloud">
              {assistantQuickQuestions.map((q) => {
                const label = quickQuestionLabel(q)
                const featured = typeof q !== 'string' && q.featured
                return (
                <button
                  key={label}
                  onClick={() => handleSendMessage(quickQuestionPrompt(q), label)}
                  className={`premium-chip quick-question-chip ${featured ? 'quick-question-chip-featured' : 'text-primary'}`}
                >
                  {label}
                </button>
              )})}
            </div>
          </div>
        )}
        {historyNotice && (
          <div className="mx-auto mb-3 max-w-3xl rounded-xl border border-border bg-white/74 px-4 py-2 text-sm font-semibold text-text-muted shadow-sm">
            {historyNotice}
          </div>
        )}

        <div className="mx-auto max-w-3xl space-y-4">
          {messages.map((msg, i) => (
            <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              {msg.role === 'assistant' && msg.streaming && i === messages.length - 1 && (
                <div className="pointer-events-none mr-3 mt-auto -mb-2 shrink-0 self-end">
                  <PetFairy state={messageFairyState(msg)} size="xl" label="当前助手状态" />
                </div>
              )}
              <div className={`flex flex-col ${msg.role === 'user' ? 'max-w-[86%] items-end' : 'max-w-[96%] items-start'}`}>
                <div
                  className={`w-fit max-w-full rounded-[22px] px-4 py-3 text-sm leading-relaxed shadow-sm ${
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
                    msg.streaming ? '正在思考…' : ''
                  )}
                  <ChatAttachmentList attachments={msg.attachments} />
                  {msg.role === 'assistant' && msg.streaming && (
                    <AgentProgressCard progress={msg.progress} />
                  )}
                  {msg.streaming && msg.content && (
                    <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-primary" />
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
                <MessageTimestamp value={msg.createdAt} align={msg.role === 'user' ? 'right' : 'left'} />
              </div>
            </div>
          ))}
        </div>
        <div ref={messagesEnd} />
      </div>

      {/* Input */}
      <div className="chat-composer-section px-6 py-3">
        <div className="mx-auto max-w-3xl">
          <div className="chat-composer-field">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !composing) {
                e.preventDefault()
                handleSendMessage()
              }
            }}
            onCompositionStart={() => setComposing(true)}
            onCompositionEnd={() => setComposing(false)}
            placeholder="输入你的问题，Enter 发送，Shift+Enter 换行"
            rows={1}
            className="chat-composer-textarea"
          />
            <ChatAttachmentList attachments={attachments} composer onRemove={removeAttachment} />
            <div className="chat-composer-footer">
              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/msword,text/markdown,text/plain,text/csv,application/json,application/rtf,.md,.markdown,.txt,.csv,.json,.rtf,.doc,.docx,.pdf"
                multiple
                className="hidden"
                onChange={(e) => handleAttachmentChange(e.target.files)}
              />
              <button
                className="chat-composer-tool"
                aria-label="添加附件"
                type="button"
                disabled={sending || uploadingAttachments}
                onClick={() => fileInputRef.current?.click()}
              >
                <Paperclip className="h-5 w-5" />
              </button>
              <div className="chat-composer-actions">
                <button
                  type="button"
                  onClick={handleNewChat}
                  disabled={sending}
                  className="chat-composer-tool"
                  aria-label="新建会话"
                  title="新建会话"
                >
                  <Plus className="h-5 w-5" />
                </button>
                {sending && (
                  <button onClick={stop} className="chat-composer-stop">
                    停止
                  </button>
                )}
                <button
                  onClick={() => handleSendMessage()}
                  disabled={sending || uploadingAttachments || (!input.trim() && attachments.length === 0)}
                  className="chat-composer-send"
                  aria-label="发送消息"
                >
                  {sending ? <Loader2 className="h-5 w-5 animate-spin" /> : <Send className="h-5 w-5" />}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
      <ClearChatConfirmDialog
        open={clearConfirmOpen}
        disabled={sending}
        onOpenChange={setClearConfirmOpen}
        onConfirm={handleClearChat}
      />
    </div>
  )
}
