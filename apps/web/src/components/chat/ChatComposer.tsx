import type { KeyboardEvent, RefObject } from 'react'
import { Loader2, Paperclip, Plus, Send } from 'lucide-react'
import type { AgentAttachment } from '../../lib/useAgentChat'
import ChatAttachmentList from './ChatAttachmentList'

export const CHAT_ATTACHMENT_ACCEPT = 'image/png,image/jpeg,image/webp,image/gif,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/msword,text/markdown,text/plain,text/csv,application/json,application/rtf,.md,.markdown,.txt,.csv,.json,.rtf,.doc,.docx,.pdf'

interface ChatComposerProps {
  input: string
  setInput: (value: string) => void
  composing: boolean
  setComposing: (value: boolean) => void
  sending: boolean
  uploadingAttachments: boolean
  attachments: AgentAttachment[]
  textareaRef: RefObject<HTMLTextAreaElement | null>
  fileInputRef: RefObject<HTMLInputElement | null>
  onSend: () => void
  onStop: () => void
  onNewChat: () => void
  onAttachmentChange: (files: FileList | null) => void
  onRemoveAttachment: (id: string) => void
  placeholder?: string
  compact?: boolean
  textareaIconSize?: string
  showNewChat?: boolean
}

export default function ChatComposer({
  input,
  setInput,
  composing,
  setComposing,
  sending,
  uploadingAttachments,
  attachments,
  textareaRef,
  fileInputRef,
  onSend,
  onStop,
  onNewChat,
  onAttachmentChange,
  onRemoveAttachment,
  placeholder = '输入你的问题…',
  compact = false,
  textareaIconSize,
  showNewChat = true,
}: ChatComposerProps) {
  const iconSize = textareaIconSize ?? (compact ? 'h-4 w-4' : 'h-5 w-5')

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !composing) {
      event.preventDefault()
      onSend()
    }
  }

  return (
    <div className="chat-composer-field">
      <textarea
        ref={textareaRef}
        value={input}
        onChange={(event) => setInput(event.target.value)}
        onKeyDown={handleKeyDown}
        onCompositionStart={() => setComposing(true)}
        onCompositionEnd={() => setComposing(false)}
        placeholder={placeholder}
        rows={1}
        className={`chat-composer-textarea ${compact ? 'chat-composer-textarea-compact' : ''}`.trim()}
      />
      <ChatAttachmentList attachments={attachments} composer onRemove={onRemoveAttachment} />
      <div className="chat-composer-footer">
        <input
          ref={fileInputRef}
          type="file"
          accept={CHAT_ATTACHMENT_ACCEPT}
          multiple
          className="hidden"
          onChange={(event) => onAttachmentChange(event.target.files)}
        />
        <button
          className="chat-composer-tool"
          aria-label="添加附件"
          title="添加附件"
          type="button"
          disabled={sending || uploadingAttachments}
          onClick={() => fileInputRef.current?.click()}
        >
          <Paperclip className={iconSize} />
        </button>
        <div className="chat-composer-actions">
          {showNewChat && (
            <button
              type="button"
              onClick={onNewChat}
              disabled={sending}
              className="chat-composer-tool"
              aria-label="新建会话"
              title="新建会话"
            >
              <Plus className={iconSize} />
            </button>
          )}
          {sending && (
            <button type="button" onClick={onStop} className="chat-composer-stop">
              停止
            </button>
          )}
          <button
            type="button"
            onClick={onSend}
            disabled={sending || uploadingAttachments || (!input.trim() && attachments.length === 0)}
            className="chat-composer-send"
            aria-label="发送消息"
          >
            {sending ? <Loader2 className={`${iconSize} animate-spin`} /> : <Send className={iconSize} />}
          </button>
        </div>
      </div>
    </div>
  )
}
