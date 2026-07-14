import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface MeetingToggleProps {
  id: string
  checked: boolean
  onChange: (checked: boolean) => void
  label: ReactNode
  description?: ReactNode
  disabled?: boolean
}

export function MeetingToggle({ id, checked, onChange, label, description, disabled = false }: MeetingToggleProps) {
  return (
    <label htmlFor={id} className={cn(
      'flex min-h-14 cursor-pointer items-center justify-between gap-4 rounded-md border border-border bg-card px-4 py-3',
      disabled && 'cursor-not-allowed opacity-50',
    )}>
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-text">{label}</span>
        {description ? <span className="mt-0.5 block text-xs leading-5 text-text-muted">{description}</span> : null}
      </span>
      <span className="relative inline-flex h-6 w-11 shrink-0">
        <input
          id={id}
          type="checkbox"
          role="switch"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
          className="peer sr-only"
        />
        <span className="absolute inset-0 rounded-full bg-muted-foreground/35 transition-colors peer-checked:bg-primary peer-focus-visible:ring-3 peer-focus-visible:ring-ring/50" />
        <span className="absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform peer-checked:translate-x-5" />
      </span>
    </label>
  )
}
