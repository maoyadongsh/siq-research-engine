import type { InputHTMLAttributes, ReactNode } from 'react'

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string
  helper?: string
  leftIcon?: ReactNode
}

export function Input({ label, helper, leftIcon, className = '', ...props }: InputProps) {
  return (
    <label className="block space-y-2">
      {label && <span className="text-sm font-semibold text-text-muted">{label}</span>}
      <span className="relative block">
        {leftIcon && <span className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-text-muted">{leftIcon}</span>}
        <input className={`form-control w-full px-4 text-base placeholder:text-text-muted/60 ${leftIcon ? 'pl-11' : ''} ${className}`} {...props} />
      </span>
      {helper && <span className="block text-sm text-text-muted">{helper}</span>}
    </label>
  )
}
