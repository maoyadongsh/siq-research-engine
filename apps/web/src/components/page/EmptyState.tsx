import type { ElementType, ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface EmptyStateProps {
  icon?: ElementType
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
  className?: string
  size?: 'sm' | 'md' | 'lg'
}

const sizeClass = {
  sm: 'py-6',
  md: 'py-10',
  lg: 'py-16',
}

const iconSizeClass = {
  sm: 'h-8 w-8',
  md: 'h-10 w-10',
  lg: 'h-12 w-12',
}

export function EmptyState({ icon: Icon, title, description, action, className, size = 'md' }: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-center justify-center text-center', sizeClass[size], className)}>
      {Icon ? (
        <span className={cn('premium-icon rounded-2xl', iconSizeClass[size])}>
          <Icon className="h-1/2 w-1/2" />
        </span>
      ) : null}
      {title ? <h3 className="mt-4 text-base font-semibold text-text">{title}</h3> : null}
      {description ? <p className="mt-1 max-w-md text-sm leading-6 text-text-muted">{description}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  )
}
