import type { ReactNode } from 'react'

interface ChatHeaderProps {
  avatar: ReactNode
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  className?: string
  leadingClassName?: string
  avatarClassName?: string
  titleClassName?: string
  subtitleClassName?: string
  actionsClassName?: string
  framedAvatar?: boolean
}

function renderText(value: ReactNode, className: string) {
  return typeof value === 'string' ? <div className={className}>{value}</div> : value
}

export default function ChatHeader({
  avatar,
  title,
  subtitle,
  actions,
  className = '',
  leadingClassName = 'flex min-w-0 items-center gap-3',
  avatarClassName = 'premium-icon h-12 w-12 shrink-0 rounded-2xl',
  titleClassName = 'truncate text-base font-semibold text-text',
  subtitleClassName = 'truncate text-xs font-medium text-text-muted',
  actionsClassName = 'flex shrink-0 items-center gap-1',
  framedAvatar = true,
}: ChatHeaderProps) {
  return (
    <div className={`chat-header flex shrink-0 items-center justify-between ${className}`.trim()}>
      <div className={leadingClassName}>
        {framedAvatar ? <div className={avatarClassName}>{avatar}</div> : avatar}
        <div className="min-w-0">
          {renderText(title, titleClassName)}
          {subtitle ? renderText(subtitle, subtitleClassName) : null}
        </div>
      </div>
      {actions ? <div className={actionsClassName}>{actions}</div> : null}
    </div>
  )
}
