import { cn } from '@/lib/utils'
import type { LucideIcon } from 'lucide-react'

interface ArtifactCardProps {
  title: React.ReactNode
  description?: React.ReactNode
  icon?: LucideIcon
  actions?: React.ReactNode
  meta?: React.ReactNode
  className?: string
}

export function ArtifactCard({ title, description, icon: Icon, actions, meta, className }: ArtifactCardProps) {
  return (
    <div
      className={cn(
        'premium-card group flex min-h-[174px] flex-col rounded-[20px] p-5 transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/25',
        className
      )}
    >
      <div className="flex items-start justify-between gap-4">
        {Icon && (
          <span className="premium-icon h-12 w-12 shrink-0 rounded-2xl">
            <Icon className="h-6 w-6" />
          </span>
        )}
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      <h3 className="mt-4 text-lg font-bold text-text">{title}</h3>
      {description && <p className="mt-2 text-sm leading-6 text-text-muted">{description}</p>}
      {meta && <div className="mt-auto pt-3 text-xs text-text-muted">{meta}</div>}
    </div>
  )
}
