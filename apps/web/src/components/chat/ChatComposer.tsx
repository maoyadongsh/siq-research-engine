import type { KeyboardEvent, RefObject } from 'react'
import { Paperclip, Plus, Send } from 'lucide-react'
import type { AgentAttachment } from '../../lib/useAgentChat'
import ChatAttachmentList from './ChatAttachmentList'
import VoiceInputButton from './VoiceInputButton'
import { useVoiceRecorder, type UseVoiceRecorderOptions } from './useVoiceRecorder'

export const CHAT_ATTACHMENT_ACCEPT = 'image/png,image/jpeg,image/webp,image/gif,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/msword,text/markdown,text/plain,text/csv,application/json,application/rtf,.md,.markdown,.txt,.csv,.json,.rtf,.doc,.docx,.pdf'

export type ChatComposerVoiceProps = Omit<UseVoiceRecorderOptions, 'disabled'> & {
  disabled?: boolean
}

export interface ChatComposerProps {
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
  voice?: ChatComposerVoiceProps
}

const ignoreVoiceRecording = () => undefined

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
  voice,
}: ChatComposerProps) {
  const iconSize = textareaIconSize ?? (compact ? 'h-4 w-4' : 'h-5 w-5')
  const voiceDisabledByDraft = Boolean(input.trim() || attachments.length)
  const voiceRecorder = useVoiceRecorder({
    onRecordingComplete: voice?.onRecordingComplete ?? ignoreVoiceRecording,
    onError: voice?.onError,
    disabled: !voice || voice.disabled || sending || uploadingAttachments || voiceDisabledByDraft,
    minDurationMs: voice?.minDurationMs,
    maxDurationMs: voice?.maxDurationMs,
  })
  const voiceBusy = voiceRecorder.status === 'requesting'
    || voiceRecorder.status === 'recording'
    || voiceRecorder.status === 'transcribing'

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !composing) {
      event.preventDefault()
      onSend()
    }
  }

  return (
    <div className="chat-composer-field">
      <div className="chat-composer-input-row">
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
          disabled={voiceBusy}
        />
        <div className="chat-composer-inline-actions">
          <input
            ref={fileInputRef}
            type="file"
            accept={CHAT_ATTACHMENT_ACCEPT}
            multiple
            className="hidden"
            onChange={(event) => onAttachmentChange(event.target.files)}
          />
          <div className="chat-composer-secondary-tools">
            {showNewChat && (
              <button
                type="button"
                onClick={onNewChat}
                disabled={sending || voiceBusy}
                className="chat-composer-tool"
                aria-label="新建会话"
                title="新建会话"
              >
                <Plus className={iconSize} />
              </button>
            )}
            {voice && (
              <VoiceInputButton
                recorder={voiceRecorder}
                iconClassName={iconSize}
                disabledReason={voiceDisabledByDraft ? '请先发送或清空当前输入' : undefined}
              />
            )}
            <button
              className="chat-composer-tool"
              aria-label="添加附件"
              title="添加附件"
              type="button"
              disabled={sending || uploadingAttachments || voiceBusy}
              onClick={() => fileInputRef.current?.click()}
            >
              <Paperclip className={iconSize} />
            </button>
          </div>
          {sending ? (
            <button type="button" onClick={onStop} className="chat-composer-stop" aria-label="停止生成" title="停止生成">
              停止
            </button>
          ) : (
            <button
              type="button"
              onClick={onSend}
              disabled={uploadingAttachments || voiceBusy || (!input.trim() && attachments.length === 0)}
              className="chat-composer-send"
              aria-label="发送消息"
              title="发送消息"
            >
              <Send className={iconSize} />
            </button>
          )}
        </div>
      </div>
      <ChatAttachmentList attachments={attachments} composer onRemove={onRemoveAttachment} />
    </div>
  )
}
