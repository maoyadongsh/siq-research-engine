import { X, History, Inbox } from 'lucide-react'
import type { ChatSessionSummary } from '../../lib/useAgentChat'
import { displayLabelForPrompt } from '../../lib/quickQuestions'
import { formatChatSessionTime } from '../../lib/chatTime'
import { EmptyState } from '@/components/page'

interface SessionHistoryListProps {
  sessions: ChatSessionSummary[]
  compact?: boolean
  loading?: boolean
  loaded?: boolean
  onSelect: (sessionId: string) => void | Promise<void>
  onClose?: () => void
  open?: boolean
}

export default function SessionHistoryList({
  sessions,
  loading = false,
  loaded = true,
  onSelect,
  onClose,
  open = true,
}: SessionHistoryListProps) {
  const visibleSessions = sessions.filter(
    (session) => Number(session.message_count || 0) > 0 && Boolean(session.title?.trim() || session.preview?.trim())
  )

  return (
    <>
      {onClose && (
        <div
          className={`absolute inset-0 z-40 bg-black/20 backdrop-blur-sm transition-opacity duration-300 ease-out ${
            open ? 'opacity-100' : 'opacity-0 pointer-events-none'
          }`}
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <div
        className={`absolute right-0 top-0 z-50 h-full w-80 max-w-[85vw] transform border-l border-border bg-bg/98 shadow-2xl transition-transform duration-300 ease-out ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
        role="dialog"
        aria-modal="true"
        aria-label="历史会话"
      >
        <div className={`flex h-full flex-col p-4`}>
          <div className="mb-4 flex items-center justify-between border-b border-border pb-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-text">
              <History className="h-4 w-4 text-primary" />
              历史会话
            </div>
            {onClose && (
              <button
                onClick={onClose}
                className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-border bg-card text-text shadow-sm transition-colors hover:bg-primary/5 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
                aria-label="关闭历史会话"
                title="关闭历史会话"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
          <div className="flex-1 space-y-2 overflow-y-auto pr-1">
            {loading && visibleSessions.length === 0 ? (
              <EmptyState icon={History} title="正在加载历史会话…" size="sm" />
            ) : visibleSessions.length === 0 ? (
              <EmptyState icon={Inbox} title={loaded ? '暂无历史会话' : '历史会话加载失败，请稍后重试'} size="sm" />
            ) : (
              visibleSessions.map((session) => (
                <button
                  key={session.session_id}
                  onClick={() => onSelect(session.session_id)}
                  className={`w-full rounded-lg border px-3 py-2.5 text-left transition-colors ${
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
      </div>
    </>
  )
}
