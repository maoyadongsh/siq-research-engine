import { cn } from '@/lib/utils'

interface EvidenceBadgeProps {
  children: React.ReactNode
  variant?: 'default' | 'primary' | 'muted'
  className?: string
}

export function EvidenceBadge({ children, variant = 'default', className }: EvidenceBadgeProps) {
  const variantClass = {
    default: 'border-border bg-white/72 text-text',
    primary: 'border-primary/20 bg-primary/5 text-primary',
    muted: 'border-border bg-bg text-text-muted',
  }[variant]

  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold',
        variantClass,
        className
      )}
    >
      {children}
    </span>
  )
}
