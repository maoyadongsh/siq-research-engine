import type { ReactNode } from 'react'

type CardVariant = 'default' | 'panel' | 'flat' | 'inverted' | 'gradient'
type CardPadding = 'none' | 'sm' | 'md' | 'lg'

interface CardProps {
  children: ReactNode
  variant?: CardVariant
  padding?: CardPadding
  className?: string
}

const padding: Record<CardPadding, string> = {
  none: '',
  sm: 'p-4',
  md: 'p-6',
  lg: 'p-8',
}

const variants: Record<Exclude<CardVariant, 'gradient'>, string> = {
  default: 'premium-card rounded-[20px]',
  panel: 'apple-panel rounded-[24px]',
  flat: 'rounded-[16px] border border-border bg-white/72 shadow-none backdrop-blur',
  inverted: 'inverted-section rounded-[24px]',
}

export function Card({ children, variant = 'default', padding: pad = 'md', className = '' }: CardProps) {
  if (variant === 'gradient') {
    return (
      <div className={`rounded-[22px] bg-gradient-to-br from-primary via-primary-light to-primary p-[1px] shadow-[0_12px_28px_rgba(0,113,227,0.12)] ${className}`}>
        <div className={`h-full rounded-[21px] bg-card/96 backdrop-blur ${padding[pad]}`}>{children}</div>
      </div>
    )
  }
  return <div className={`${variants[variant]} ${padding[pad]} ${className}`}>{children}</div>
}

export function EmptyState({ icon, title, description }: { icon: ReactNode; title: string; description: string }) {
  return (
    <div className="premium-card flex flex-col items-center justify-center rounded-[24px] px-6 py-16 text-center">
      <div className="premium-icon mb-4 h-14 w-14 rounded-2xl text-primary">{icon}</div>
      <h3 className="text-2xl font-semibold text-text">{title}</h3>
      <p className="mt-2 max-w-xl text-base leading-7 text-text-muted">{description}</p>
    </div>
  )
}
