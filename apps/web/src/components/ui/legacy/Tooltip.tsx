import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface TooltipProps {
  children: ReactNode
  content: string
  className?: string
  delay?: 'none' | 'short' | 'medium'
}

const delayClass = {
  none: '',
  short: 'delay-100',
  medium: 'delay-200',
}

export function Tooltip({ children, content, className = '', delay = 'short' }: TooltipProps) {
  return (
    <span className={cn('group relative inline-flex', className)}>
      {children}
      <span
        className={cn(
          'pointer-events-none absolute left-1/2 top-full z-50 mt-2 -translate-x-1/2 whitespace-nowrap rounded-lg border border-border bg-card/98 px-2.5 py-1.5 text-xs font-semibold text-text opacity-0 shadow-[0_10px_28px_rgba(15,23,42,0.12)] backdrop-blur-xl transition-opacity duration-200 group-hover:opacity-100',
          delayClass[delay]
        )}
      >
        {content}
        <span
          className="absolute -top-1 left-1/2 h-2 w-2 -translate-x-1/2 rotate-45 border-l border-t border-border bg-card"
          aria-hidden="true"
        />
      </span>
    </span>
  )
}
