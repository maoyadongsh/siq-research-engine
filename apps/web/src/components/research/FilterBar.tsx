import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

interface FilterBarProps {
  children?: ReactNode
  className?: string
}

export function FilterBar({ children, className }: FilterBarProps) {
  return (
    <div
      className={cn(
        'flex flex-col gap-3 rounded-2xl border border-border bg-card p-4 shadow-sm sm:flex-row sm:items-center sm:justify-between',
        className
      )}
    >
      {children}
    </div>
  )
}
