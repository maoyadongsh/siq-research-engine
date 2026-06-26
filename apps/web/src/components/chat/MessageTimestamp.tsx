import {
  formatChatMessageTime,
  formatChatMessageTimeTitle,
  toChatDateTimeAttr,
} from '../../lib/chatTime'

interface MessageTimestampProps {
  value?: string | null
  align?: 'left' | 'right'
  className?: string
}

export default function MessageTimestamp({
  value,
  align = 'left',
  className = '',
}: MessageTimestampProps) {
  const label = formatChatMessageTime(value)
  if (!label) return null

  return (
    <time
      className={`chat-message-time ${align === 'right' ? 'chat-message-time-right' : 'chat-message-time-left'} ${className}`.trim()}
      dateTime={toChatDateTimeAttr(value)}
      title={formatChatMessageTimeTitle(value)}
    >
      {label}
    </time>
  )
}
