import { X } from 'lucide-react'
import type { ChatSessionSummary } from '../../lib/useAgentChat'
import { displayLabelForPrompt } from '../../lib/quickQuestions'
import { formatChatSessionTime } from '../../lib/chatTime'

interface SessionHistoryListProps {
  sessions: ChatSessionSummary[]
  compact?: boolean
  loading?: boolean
  loaded?: boolean
  onSelect: (sessionId: string) => void | Promise<void>
  onClose?: () => void
}

export default function SessionHistoryList({
  sessions,
  compact = false,
  loading = false,
  loaded = true,
  onSelect,
  onClose,
}: SessionHistoryListProps) {
  const visibleSessions = sessions.filter(
    (session) => Number(session.message_count || 0) > 0 && Boolean(session.title?.trim() || session.preview?.trim())
  )

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
        {loading && visibleSessions.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border bg-card px-3 py-3 text-xs font-medium text-text-muted">
            正在加载历史会话…
          </div>
        ) : visibleSessions.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border bg-card px-3 py-3 text-xs font-medium text-text-muted">
            {loaded ? '暂无历史会话' : '历史会话加载失败，请稍后重试'}
          </div>
        ) : (
          visibleSessions.map((session) => (
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
                <span className="min-w-0 truncate text-xs font-semibold">{displayLabelForPrompt(session.title)}</span>
                <span className="shrink-0 text-[11px] font-medium text-text-muted">
                  {formatChatSessionTime(session.last_message_at)}
                </span>
              </div>
              <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-text-muted">
                <span className="min-w-0 truncate">{displayLabelForPrompt(session.preview || '尚无消息')}</span>
                <span className="shrink-0">{session.message_count} 条</span>
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  )
}
