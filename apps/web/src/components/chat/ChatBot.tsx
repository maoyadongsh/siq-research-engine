import { useState, useRef, useEffect, useCallback } from 'react'
import {
  X,
  Minus,
  Send,
  Loader2,
  Paperclip,
  History,
  Trash2,
  Plus,
  Copy,
} from 'lucide-react'
import ChatAttachmentList from './ChatAttachmentList'
import PetFairy, { type PetFairyState } from './PetFairy'
import MessageRenderer from './MessageRenderer'
import MessageTimestamp from './MessageTimestamp'
import SessionHistoryList from './SessionHistoryList'
import ClearChatConfirmDialog from './ClearChatConfirmDialog'
import { useToast } from '../../hooks/useToast'
import { useAgentChat, type AgentMessage } from '../../lib/useAgentChat'
import { useAutosizeTextarea } from '../../lib/useAutosizeTextarea'
import { copyText } from '../../lib/clipboard'
import { assistantQuickQuestions, quickQuestionLabel, quickQuestionPrompt } from '../../lib/quickQuestions'

function messageFairyState(msg: AgentMessage): PetFairyState {
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
        <PetFairy state="idle" size="md" imageSrc="/pet/siq-avatar-preview.webp" />
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
  const fairyState: PetFairyState = hadError ? 'error' : assistantHasContent ? 'replying' : assistantStreaming || sending ? 'thinking' : 'idle'
  useAutosizeTextarea(textareaRef, input)

  const scrollToBottom = useCallback(() => {
    messagesEnd.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && !composing) {
      e.preventDefault()
      sendMessage()
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

  return (
    <div
      className="fixed z-50 flex flex-col overflow-hidden rounded-[24px] border border-border bg-white/96 shadow-[0_24px_80px_rgba(15,23,42,0.18)] backdrop-blur-2xl"
      style={{
        bottom: 'max(1rem, env(safe-area-inset-bottom))',
        right: 'max(1rem, env(safe-area-inset-right))',
        height: 'min(620px, calc(100dvh - 2rem))',
        width: 'min(400px, calc(100vw - 2rem))',
      }}
    >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <div className="flex items-center gap-2">
              <PetFairy state={fairyState} size="sm" />
              <span className="text-sm font-semibold text-text">财报助手</span>
            </div>
            <div className="flex gap-1">
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
                className="flex h-9 w-9 items-center justify-center rounded-xl text-text-muted hover:bg-bg hover:text-text"
                aria-label="最小化"
              >
                <Minus className="h-4 w-4" />
              </button>
              <button
                onClick={onClose}
                className="flex h-9 w-9 items-center justify-center rounded-xl text-text-muted hover:bg-red-50 hover:text-error"
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>

          {historyOpen && (
            <SessionHistoryList
              sessions={sessions}
              loading={loadingSessions}
              loaded={sessionsLoaded}
              compact
              onSelect={handleSwitchSession}
              onClose={() => setHistoryOpen(false)}
            />
          )}

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-4 py-3">
            {messages.length === 0 && (
              <div className="flex flex-col items-center py-8 text-center">
                <PetFairy state={fairyState} size="float" className="mb-3" />
                <p className="mb-4 text-sm text-text-muted">
                  你好！我是财报分析助手，可以回答关于已入库财报的问题。
                </p>
                <div className="quick-question-cloud quick-question-cloud-compact">
                  {assistantQuickQuestions.map((q) => {
                    const label = quickQuestionLabel(q)
                    const featured = typeof q !== 'string' && q.featured
                    return (
                    <button
                      key={label}
                      onClick={() => sendMessage(quickQuestionPrompt(q), undefined, label)}
                      className={`premium-chip quick-question-chip ${featured ? 'quick-question-chip-featured' : ''}`}
                    >
                      {label}
                    </button>
                  )})}
                </div>
              </div>
            )}

            {messages.map((msg, i) => (
              <div
                key={i}
                className={`mb-3 flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                {msg.role === 'assistant' && msg.streaming && i === messages.length - 1 && (
                  <div className="pointer-events-none mr-2 mt-auto -mb-2 shrink-0 self-end">
                    <PetFairy state={messageFairyState(msg)} size="lg" label="当前助手状态" />
                  </div>
                )}
                <div className={`flex flex-col ${msg.role === 'user' ? 'max-w-[84%] items-end' : 'max-w-[96%] items-start'}`}>
                  <div
                    className={`w-fit max-w-full rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                      msg.role === 'user'
                        ? 'rounded-br-md bg-blue-100 text-blue-900'
                        : 'rounded-bl-md bg-bg text-text'
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
            <div ref={messagesEnd} />
          </div>

          {/* Input */}
          <div className="chat-composer-section px-4 py-2">
            <div className="chat-composer-wrap">
              <div className="chat-composer-field">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                onCompositionStart={() => setComposing(true)}
                onCompositionEnd={() => setComposing(false)}
                placeholder="输入你的问题…"
                rows={1}
                className="chat-composer-textarea chat-composer-textarea-compact"
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
                    <Paperclip className="h-4 w-4" />
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
                      <Plus className="h-4 w-4" />
                    </button>
                    {sending && (
                      <button onClick={stop} className="chat-composer-stop">
                        停止
                      </button>
                    )}
                    <button
                      onClick={() => sendMessage()}
                      disabled={sending || uploadingAttachments || (!input.trim() && attachments.length === 0)}
                      className="chat-composer-send"
                      aria-label="发送消息"
                    >
                      {sending ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Send className="h-4 w-4" />
                      )}
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
