import type { ReactNode } from 'react'

export function Tooltip({ children, content, className = '' }: { children: ReactNode; content: string; className?: string }) {
  return (
    <span className={`group relative inline-flex ${className}`}>
      {children}
      <span className="pointer-events-none absolute left-1/2 top-full z-50 mt-2 -translate-x-1/2 whitespace-nowrap rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs font-semibold text-text opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
        {content}
      </span>
    </span>
  )
}
