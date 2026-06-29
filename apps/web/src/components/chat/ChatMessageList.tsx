import type { ReactNode } from 'react'
import { Copy } from 'lucide-react'
import type { AgentMessage } from '../../lib/useAgentChat'
import ChatAttachmentList from './ChatAttachmentList'
import MessageRenderer from './MessageRenderer'
import MessageTimestamp from './MessageTimestamp'

export interface ChatQuickQuestion {
  key: string
  label: string
  featured?: boolean
  className?: string
  onClick: () => void
}

interface ChatMessageListProps {
  messages: AgentMessage[]
  endRef: React.RefObject<HTMLDivElement | null>
  emptyAvatar: ReactNode
  emptyDescription: ReactNode
  quickQuestions?: ChatQuickQuestion[]
  onCopyMessage: (content: string) => void
  notice?: ReactNode
  renderStreamingAvatar?: (message: AgentMessage) => ReactNode
  renderProgress?: (message: AgentMessage) => ReactNode
  compact?: boolean
  emptyClassName?: string
  emptyDescriptionClassName?: string
  quickQuestionClassName?: string
  listClassName?: string
  messageGapClassName?: string
  userMessageClassName?: string
  assistantMessageClassName?: string
  cursorClassName?: string
}

export default function ChatMessageList({
  messages,
  endRef,
  emptyAvatar,
  emptyDescription,
  quickQuestions = [],
  onCopyMessage,
  notice,
  renderStreamingAvatar,
  renderProgress,
  compact = false,
  emptyClassName,
  emptyDescriptionClassName,
  quickQuestionClassName = '',
  listClassName = '',
  messageGapClassName = compact ? 'space-y-3' : 'space-y-4',
  userMessageClassName,
  assistantMessageClassName,
  cursorClassName,
}: ChatMessageListProps) {
  const emptyWrapClass = emptyClassName ?? (compact
    ? 'flex w-full flex-col items-center py-6 text-center'
    : 'mx-auto flex max-w-2xl flex-col items-center py-16 text-center')
  const descriptionClass = emptyDescriptionClassName ?? (compact
    ? 'mb-4 max-w-[18rem] text-sm leading-6 text-text-muted'
    : 'mb-6 max-w-md text-base leading-7 text-text-muted')
  const userBubbleClass = userMessageClassName ?? (compact
    ? 'chat-message-bubble w-fit max-w-full rounded-[18px] rounded-br-md bg-primary px-3.5 py-2.5 text-sm leading-relaxed text-white shadow-sm'
    : 'chat-message-bubble w-fit max-w-full rounded-[22px] rounded-br-md bg-primary px-4 py-3 text-sm leading-relaxed text-white shadow-sm')
  const assistantBubbleClass = assistantMessageClassName ?? (compact
    ? 'chat-message-bubble w-fit max-w-full rounded-[18px] rounded-bl-md border border-border bg-white/82 px-3.5 py-2.5 text-sm leading-relaxed text-text shadow-sm'
    : 'chat-message-bubble w-fit max-w-full rounded-[22px] rounded-bl-md border border-border bg-white/82 px-4 py-3 text-sm leading-relaxed text-text shadow-sm')
  const streamingCursorClass = cursorClassName ?? (compact
    ? 'ml-0.5 inline-block h-3 w-1 animate-pulse bg-primary'
    : 'ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-primary')

  return (
    <>
      {messages.length === 0 && (
        <div className={emptyWrapClass}>
          {emptyAvatar}
          {typeof emptyDescription === 'string' ? (
            <p className={descriptionClass}>{emptyDescription}</p>
          ) : (
            emptyDescription
          )}
          {quickQuestions.length > 0 && (
            <div className={`quick-question-cloud ${compact ? 'quick-question-cloud-compact' : ''} ${quickQuestionClassName}`.trim()}>
              {quickQuestions.map((q) => (
                <button
                  key={q.key}
                  onClick={q.onClick}
                  className={`premium-chip quick-question-chip ${q.featured ? 'quick-question-chip-featured' : ''} ${q.className ?? ''}`.trim()}
                >
                  {q.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {notice ? (
        <div className="mx-auto mb-3 max-w-3xl rounded-xl border border-border bg-white/74 px-4 py-2 text-sm font-semibold text-text-muted shadow-sm">
          {notice}
        </div>
      ) : null}

      <div className={`${messageGapClassName} ${listClassName}`.trim()}>
        {messages.map((msg, i) => {
          const isUser = msg.role === 'user'
          const streamingAvatar = msg.role === 'assistant' && msg.streaming && i === messages.length - 1
            ? renderStreamingAvatar?.(msg)
            : null

          return (
            <div
              key={`${msg.role}-${msg.createdAt ?? i}-${i}`}
              className={`flex items-start gap-2 ${isUser ? 'justify-end' : 'justify-start'}`}
            >
              {streamingAvatar}
              <div className={`flex flex-col max-w-[85%] ${isUser ? 'items-end' : 'items-start'}`}>
                <div className={isUser ? userBubbleClass : assistantBubbleClass}>
                  {msg.content ? (
                    <MessageRenderer
                      content={msg.content}
                      streaming={msg.streaming}
                      variant={isUser ? 'user' : 'assistant'}
                    />
                  ) : (
                    msg.streaming ? '正在思考…' : ''
                  )}
                  <ChatAttachmentList attachments={msg.attachments} />
                  {msg.role === 'assistant' ? renderProgress?.(msg) : null}
                  {msg.streaming && msg.content && <span className={streamingCursorClass} />}
                  {msg.content && !msg.streaming && (
                    <div className="chat-message-actions">
                      <button
                        type="button"
                        className="chat-message-copy"
                        onClick={() => onCopyMessage(msg.content)}
                        aria-label="复制消息"
                      >
                        <Copy className="h-3 w-3" />
                        复制
                      </button>
                    </div>
                  )}
                </div>
                <MessageTimestamp value={msg.createdAt} align={isUser ? 'right' : 'left'} />
              </div>
            </div>
          )
        })}
      </div>
      <div ref={endRef} />
    </>
  )
}
