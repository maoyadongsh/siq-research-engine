import { useId, useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface MobileActionBarProps {
  children: ReactNode
  className?: string
  rootClassName?: string
  defaultExpanded?: boolean
}

export function MobileActionBar({
  children,
  className,
  rootClassName,
  defaultExpanded = true,
}: MobileActionBarProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const contentId = useId()

  return (
    <div
      className={cn(
        'mobile-action-bar',
        expanded ? 'is-expanded' : 'is-collapsed',
        rootClassName
      )}
    >
      <button
        type="button"
        className="mobile-action-bar-handle"
        onClick={() => setExpanded((e) => !e)}
        aria-label={expanded ? '收起操作栏' : '展开操作栏'}
        aria-controls={contentId}
        aria-expanded={expanded}
      >
        <span className="mobile-action-bar-knob" />
      </button>
      <div id={contentId} className={cn('mobile-action-bar-content', className)}>
        {children}
      </div>
    </div>
  )
}
