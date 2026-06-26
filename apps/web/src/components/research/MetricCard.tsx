import { cn } from '@/lib/utils'
import type { LucideIcon } from 'lucide-react'

interface MetricCardProps {
  label: React.ReactNode
  value: React.ReactNode
  icon?: LucideIcon
  trend?: React.ReactNode
  status?: 'neutral' | 'success' | 'warning' | 'error'
  className?: string
}

export function MetricCard({ label, value, icon: Icon, trend, status = 'neutral', className }: MetricCardProps) {
  const statusClass = {
    neutral: 'text-text',
    success: 'text-success',
    warning: 'text-warning',
    error: 'text-error',
  }[status]

  return (
    <div
      className={cn(
        'rounded-[24px] border border-border bg-card p-5 shadow-sm',
        className
      )}
    >
      <p className="text-sm font-semibold text-text-muted">{label}</p>
      <p className={cn('mt-2 font-display text-4xl', statusClass)}>{value}</p>
      <div className="mt-1 flex items-center gap-2 text-sm text-text-muted">
        {Icon && <Icon className="h-4 w-4" />}
        {trend}
      </div>
    </div>
  )
}
