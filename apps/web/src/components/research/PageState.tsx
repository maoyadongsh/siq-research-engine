import { cn } from '@/lib/utils'
import { AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react'

interface PageStateProps {
  state: 'loading' | 'error' | 'empty' | 'success'
  title?: React.ReactNode
  description?: React.ReactNode
  children?: React.ReactNode
  className?: string
}

export function PageState({ state, title, description, children, className }: PageStateProps) {
  const config = {
    loading: { icon: Loader2, iconClass: 'text-primary', animate: 'animate-spin' },
    error: { icon: AlertTriangle, iconClass: 'text-error', animate: '' },
    empty: { icon: AlertTriangle, iconClass: 'text-warning', animate: '' },
    success: { icon: CheckCircle2, iconClass: 'text-success', animate: '' },
  }[state]

  const Icon = config.icon

  return (
    <div
      className={cn(
        'flex min-h-[320px] flex-col items-center justify-center rounded-2xl border border-border bg-card p-8 text-center shadow-sm',
        className
      )}
    >
      <Icon className={cn('h-10 w-10', config.iconClass, config.animate)} />
      {title && <h3 className="mt-4 text-lg font-semibold text-text">{title}</h3>}
      {description && <p className="mt-2 max-w-md text-sm text-text-muted">{description}</p>}
      {children && <div className="mt-5">{children}</div>}
    </div>
  )
}
