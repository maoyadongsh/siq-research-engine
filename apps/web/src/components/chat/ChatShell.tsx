import type { ReactNode } from 'react'

interface ChatShellProps {
  header?: ReactNode
  history?: ReactNode
  messages: ReactNode
  composer: ReactNode
  clearDialog?: ReactNode
  className?: string
  style?: React.CSSProperties
  messagesClassName?: string
  composerClassName?: string
  minimized?: boolean
}

export default function ChatShell({
  header,
  history,
  messages,
  composer,
  clearDialog,
  className = '',
  style,
  messagesClassName = 'min-h-0 flex-1 overflow-y-auto px-4 py-4',
  composerClassName = 'chat-composer-section shrink-0 px-4 py-3',
  minimized = false,
}: ChatShellProps) {
  return (
    <div className={`chat-shell relative flex min-h-0 flex-col overflow-hidden ${className}`.trim()} style={style}>
      {header}
      {!minimized && history}
      {!minimized && <div className={messagesClassName}>{messages}</div>}
      {!minimized && (
        <div className={composerClassName}>
          <div className="chat-composer-wrap">{composer}</div>
        </div>
      )}
      {clearDialog}
    </div>
  )
}
