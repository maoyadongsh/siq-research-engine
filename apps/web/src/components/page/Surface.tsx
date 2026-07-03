import type { ComponentPropsWithoutRef, ElementType, ReactNode } from 'react'
import { cn } from '@/lib/utils'
export { StatusBadge } from './StatusBadge'

type SurfaceKind = 'card' | 'panel' | 'row' | 'muted'
type Padding = 'none' | 'sm' | 'md' | 'lg'

type SurfaceProps<T extends ElementType> = {
  as?: T
  kind?: SurfaceKind
  padding?: Padding
  children: ReactNode
  className?: string
} & Omit<ComponentPropsWithoutRef<T>, 'as' | 'children' | 'className'>

const kindClass: Record<SurfaceKind, string> = {
  card: 'surface-card',
  panel: 'surface-panel',
  row: 'surface-row',
  muted: 'surface-muted',
}

const paddingClass: Record<Padding, string> = {
  none: '',
  sm: 'p-3',
  md: 'p-4 sm:p-5',
  lg: 'p-5 sm:p-6',
}

export function Surface<T extends ElementType = 'div'>({
  as,
  kind = 'card',
  padding = 'md',
  children,
  className,
  ...props
}: SurfaceProps<T>) {
  const Component = as || 'div'
  return (
    <Component className={cn(kindClass[kind], paddingClass[padding], className)} {...props}>
      {children}
    </Component>
  )
}
