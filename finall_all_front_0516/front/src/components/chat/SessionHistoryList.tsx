import { X } from 'lucide-react'
import type { ChatSessionSummary } from '../../lib/useAgentChat'

interface SessionHistoryListProps {
  sessions: ChatSessionSummary[]
  compact?: boolean
  onSelect: (sessionId: string) => void | Promise<void>
  onClose?: () => void
}

function formatTime(value: string | null) {
  if (!value) return '空会话'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间未知'
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function SessionHistoryList({
  sessions,
  compact = false,
  onSelect,
  onClose,
}: SessionHistoryListProps) {
  return (
    <div className="border-b border-border bg-bg/80 px-3 py-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-xs font-semibold text-text-muted">历史会话</div>
        {onClose && (
          <button
            onClick={onClose}
            className="rounded p-1 text-text-muted hover:bg-card hover:text-text"
            aria-label="关闭历史会话"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <div className={`${compact ? 'max-h-44' : 'max-h-64'} space-y-2 overflow-y-auto pr-1`}>
        {sessions.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border bg-card px-3 py-3 text-xs font-medium text-text-muted">
            暂无历史会话
          </div>
        ) : (
          sessions.map((session) => (
            <button
              key={session.session_id}
              onClick={() => onSelect(session.session_id)}
              className={`w-full rounded-lg border px-3 py-2 text-left transition-colors ${
                session.current
                  ? 'border-primary/30 bg-primary/5 text-primary'
                  : 'border-border bg-card text-text hover:border-primary/30 hover:bg-primary/5'
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate text-xs font-semibold">{session.title}</span>
                <span className="shrink-0 text-[11px] font-medium text-text-muted">
                  {formatTime(session.last_message_at)}
                </span>
              </div>
              <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-text-muted">
                <span className="min-w-0 truncate">{session.preview || '尚无消息'}</span>
                <span className="shrink-0">{session.message_count} 条</span>
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  )
}
