import type { ButtonHTMLAttributes, ReactNode } from 'react'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type ButtonSize = 'sm' | 'md' | 'lg' | 'icon'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  leftIcon?: ReactNode
  rightIcon?: ReactNode
}

const variants: Record<ButtonVariant, string> = {
  primary: 'accent-gradient text-white shadow-[0_10px_24px_rgba(0,113,227,0.18)] hover:-translate-y-0.5 hover:brightness-105 active:scale-[0.98]',
  secondary: 'border border-[#cbd8e8] bg-transparent text-text shadow-none hover:border-[#0071e3] hover:bg-transparent hover:text-[#005bb5] active:scale-[0.98]',
  ghost: 'text-text-muted hover:bg-slate-950/[0.055] hover:text-text active:scale-[0.98]',
  danger: 'bg-error text-white shadow-sm hover:bg-red-700 active:scale-[0.98]',
}

const sizes: Record<ButtonSize, string> = {
  sm: 'h-10 rounded-xl px-3.5 text-sm',
  md: 'h-11 rounded-[14px] px-[1.125rem] text-sm',
  lg: 'h-12 rounded-[15px] px-5 text-base',
  icon: 'h-11 w-11 rounded-[14px] p-0',
}

export function Button({ variant = 'primary', size = 'md', leftIcon, rightIcon, className = '', children, ...props }: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 font-semibold transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-60 ${variants[variant]} ${sizes[size]} ${className}`}
      {...props}
    >
      {leftIcon}
      {children}
      {rightIcon}
    </button>
  )
}
