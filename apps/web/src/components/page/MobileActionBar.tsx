import { useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface MobileActionBarProps {
  children: ReactNode
  className?: string
  defaultExpanded?: boolean
}

export function MobileActionBar({ children, className, defaultExpanded = true }: MobileActionBarProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  return (
    <div
      className={cn(
        'mobile-action-bar',
        expanded ? 'is-expanded' : 'is-collapsed',
        className
      )}
    >
      <button
        type="button"
        className="mobile-action-bar-handle"
        onClick={() => setExpanded((e) => !e)}
        aria-label={expanded ? '收起操作栏' : '展开操作栏'}
        aria-expanded={expanded}
      >
        <span className="mobile-action-bar-knob" />
      </button>
      <div className="mobile-action-bar-content">{children}</div>
    </div>
  )
}
