import type { ElementType, ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface PageShellProps {
  children: ReactNode
  className?: string
}

interface PageHeaderProps {
  icon?: ElementType
  eyebrow: string
  title: string
  description?: ReactNode
  meta?: ReactNode
  actions?: ReactNode
  children?: ReactNode
  className?: string
}

interface PageSectionProps {
  children: ReactNode
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  className?: string
  contentClassName?: string
}

export function PageShell({ children, className }: PageShellProps) {
  return <div className={cn('page-shell min-w-0 overflow-x-hidden', className)}>{children}</div>
}

export function PageHeader({
  icon: Icon,
  eyebrow,
  title,
  description,
  meta,
  actions,
  children,
  className,
}: PageHeaderProps) {
  return (
    <section className={cn('page-header', className)}>
      <div className="page-header-inner">
        <div className="min-w-0">
          <div className="page-kicker">
            {Icon ? <Icon className="h-3.5 w-3.5" /> : null}
            {eyebrow}
          </div>
          <h1 className="page-title">{title}</h1>
          {description ? <p className="page-description">{description}</p> : null}
          {meta ? <div className="mt-4 flex flex-wrap items-center gap-2">{meta}</div> : null}
        </div>
        {actions ? <div className="min-w-0">{actions}</div> : null}
      </div>
      {children}
    </section>
  )
}

export function PageSection({
  children,
  title,
  description,
  actions,
  className,
  contentClassName,
}: PageSectionProps) {
  return (
    <section className={cn('surface-panel', className)}>
      {(title || description || actions) && (
        <div className="flex flex-col gap-3 border-b border-border/70 px-4 py-4 sm:px-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0">
            {title ? <h2 className="text-lg font-bold text-text sm:text-xl">{title}</h2> : null}
            {description ? <p className="mt-1 text-sm leading-6 text-text-muted">{description}</p> : null}
          </div>
          {actions ? <div className="shrink-0">{actions}</div> : null}
        </div>
      )}
      <div className={cn('p-4 sm:p-5', contentClassName)}>{children}</div>
    </section>
  )
}
