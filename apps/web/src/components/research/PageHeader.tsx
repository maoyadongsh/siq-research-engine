import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

interface PageHeaderProps {
  eyebrow?: ReactNode
  title: ReactNode
  description?: ReactNode
  meta?: ReactNode
  actions?: ReactNode
  className?: string
}

export function PageHeader({ eyebrow, title, description, meta, actions, className }: PageHeaderProps) {
  return (
    <section className={cn('secondary-hero', className)}>
      <div className="secondary-hero-inner">
        <div className="max-w-4xl">
          {eyebrow && <div className="secondary-kicker">{eyebrow}</div>}
          <h1 className="secondary-title">{title}</h1>
          {description && <p className="secondary-description">{description}</p>}
          {meta && <div className="mt-4 flex flex-wrap items-center gap-3">{meta}</div>}
        </div>
        {actions && <div className="mt-5 flex flex-wrap items-center gap-3 lg:mt-0">{actions}</div>}
      </div>
    </section>
  )
}
