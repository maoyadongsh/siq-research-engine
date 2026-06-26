import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

interface EntityTableProps {
  children: ReactNode
  className?: string
}

export function EntityTable({ children, className }: EntityTableProps) {
  return (
    <div className={cn('overflow-hidden rounded-2xl border border-border bg-card shadow-sm', className)}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">{children}</table>
      </div>
    </div>
  )
}

interface EntityTableHeadProps {
  children: ReactNode
  className?: string
}

export function EntityTableHead({ children, className }: EntityTableHeadProps) {
  return <thead className={cn('bg-bg', className)}>{children}</thead>
}

interface EntityTableRowProps {
  children: ReactNode
  className?: string
}

export function EntityTableRow({ children, className }: EntityTableRowProps) {
  return (
    <tr className={cn('border-b border-border last:border-b-0 transition-colors hover:bg-bg/60', className)}>
      {children}
    </tr>
  )
}

interface EntityTableCellProps {
  children?: ReactNode
  className?: string
  heading?: boolean
}

export function EntityTableCell({ children, className, heading }: EntityTableCellProps) {
  const Tag = heading ? 'th' : 'td'
  return (
    <Tag
      className={cn(
        'px-4 py-3 text-left',
        heading ? 'text-xs font-semibold text-text-muted' : 'text-text',
        className
      )}
    >
      {children}
    </Tag>
  )
}
