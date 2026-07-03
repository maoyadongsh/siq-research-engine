import type { ElementType, ReactNode } from 'react'
import { cn } from '@/lib/utils'

export type StatusBadgeTone = 'neutral' | 'info' | 'success' | 'warning' | 'error'

interface StatusBadgeProps {
  children: ReactNode
  tone?: StatusBadgeTone
  icon?: ElementType
  className?: string
}

export function StatusBadge({ children, tone = 'neutral', icon: Icon, className }: StatusBadgeProps) {
  const toneClass = {
    neutral: '',
    info: 'secondary-status-info',
    success: 'secondary-status-success',
    warning: 'secondary-status-warning',
    error: 'secondary-status-error',
  }[tone]

  return (
    <span className={cn('secondary-status', toneClass, className)}>
      {Icon ? <Icon className="h-3.5 w-3.5" /> : null}
      {children}
    </span>
  )
}
