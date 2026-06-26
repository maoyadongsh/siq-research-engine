import { cn } from '@/lib/utils'
import type { LucideIcon } from 'lucide-react'

interface StatusBadgeProps {
  children: React.ReactNode
  variant?: 'neutral' | 'success' | 'warning' | 'error' | 'info'
  icon?: LucideIcon
  className?: string
}

export function StatusBadge({ children, variant = 'neutral', icon: Icon, className }: StatusBadgeProps) {
  const variantClass = {
    neutral: 'bg-bg text-text-muted',
    success: 'bg-success/10 text-success',
    warning: 'bg-warning/10 text-warning',
    error: 'bg-error/10 text-error',
    info: 'bg-primary/10 text-primary',
  }[variant]

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold',
        variantClass,
        className
      )}
    >
      {Icon && <Icon className="h-3.5 w-3.5" />}
      {children}
    </span>
  )
}
