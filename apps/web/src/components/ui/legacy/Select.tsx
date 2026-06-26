import type { SelectHTMLAttributes, ReactNode } from 'react'

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string
  helper?: string
  leftIcon?: ReactNode
}

export function Select({ label, helper, leftIcon, className = '', children, ...props }: SelectProps) {
  return (
    <label className="block space-y-2">
      {label && <span className="text-sm font-semibold text-text-muted">{label}</span>}
      <span className="relative block">
        {leftIcon && <span className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-text-muted">{leftIcon}</span>}
        <select className={`form-control w-full appearance-none px-4 pr-10 text-base ${leftIcon ? 'pl-11' : ''} ${className}`} {...props}>{children}</select>
        <span className="pointer-events-none absolute right-4 top-1/2 -translate-y-1/2 text-text-muted">⌄</span>
      </span>
      {helper && <span className="block text-sm text-text-muted">{helper}</span>}
    </label>
  )
}
