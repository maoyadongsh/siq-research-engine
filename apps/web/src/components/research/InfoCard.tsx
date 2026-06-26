import { cn } from '@/lib/utils'
import type { LucideIcon } from 'lucide-react'

interface InfoCardProps {
  title?: React.ReactNode
  description?: React.ReactNode
  icon?: LucideIcon
  iconClassName?: string
  children?: React.ReactNode
  className?: string
}

export function InfoCard({ title, description, icon: Icon, iconClassName, children, className }: InfoCardProps) {
  return (
    <div className={cn('premium-shell rounded-[28px] p-6', className)}>
      {(title || description || Icon) && (
        <div className="mb-5 flex items-center gap-3">
          {Icon && (
            <span className={cn('premium-icon h-12 w-12 rounded-2xl', iconClassName)}>
              <Icon className="h-6 w-6" />
            </span>
          )}
          <div>
            {title && <h2 className="text-xl font-bold tracking-tight text-text">{title}</h2>}
            {description && <p className="text-sm text-text-muted">{description}</p>}
          </div>
        </div>
      )}
      {children}
    </div>
  )
}
