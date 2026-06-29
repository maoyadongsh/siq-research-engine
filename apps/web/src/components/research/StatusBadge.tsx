import type { LucideIcon } from 'lucide-react'
import { StatusBadge as BaseStatusBadge } from '@/components/page/Surface'

interface StatusBadgeProps {
  children: React.ReactNode
  variant?: 'neutral' | 'success' | 'warning' | 'error' | 'info'
  icon?: LucideIcon
  className?: string
}

const variantToTone = {
  neutral: 'neutral',
  success: 'success',
  warning: 'warning',
  error: 'error',
  info: 'info',
} as const

export function StatusBadge({ children, variant = 'neutral', icon, className }: StatusBadgeProps) {
  return (
    <BaseStatusBadge tone={variantToTone[variant]} icon={icon} className={className}>
      {children}
    </BaseStatusBadge>
  )
}
