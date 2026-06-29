import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { AlertCircle, CheckCircle2, Info, X, XCircle } from 'lucide-react'

import { ToastContext, type ToastInput, type ToastItem, type ToastType } from './toastContext'

const styles: Record<ToastType, { icon: typeof CheckCircle2; tone: string }> = {
  success: { icon: CheckCircle2, tone: 'text-success bg-success/10' },
  error: { icon: XCircle, tone: 'text-error bg-error/10' },
  info: { icon: Info, tone: 'text-primary bg-primary/10' },
  warning: { icon: AlertCircle, tone: 'text-warning bg-warning/10' },
}

export type { ToastInput, ToastItem, ToastType }

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const [exitingIds, setExitingIds] = useState<Set<number>>(new Set())

  const remove = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id))
    setExitingIds((current) => {
      const next = new Set(current)
      next.delete(id)
      return next
    })
  }, [])

  const requestRemove = useCallback((id: number) => {
    setExitingIds((current) => new Set(current).add(id))
    window.setTimeout(() => remove(id), 180)
  }, [remove])

  const toast = useCallback((input: ToastInput) => {
    const id = Date.now() + Math.floor(Math.random() * 1000)
    const item: ToastItem = {
      id,
      title: input.title,
      description: input.description || '',
      type: input.type || 'info',
    }
    setItems((current) => [item, ...current].slice(0, 4))
    window.setTimeout(() => requestRemove(id), input.duration ?? 3200)
  }, [requestRemove])

  const value = useMemo(() => ({ toast }), [toast])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className="fixed right-4 top-24 z-[90] flex w-[min(360px,calc(100vw-2rem))] flex-col gap-3"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {items.map((item) => {
          const meta = styles[item.type]
          const Icon = meta.icon
          const isExiting = exitingIds.has(item.id)
          return (
            <div
              key={item.id}
              className={[
                'rounded-2xl border border-border bg-card/96 p-4 shadow-2xl shadow-slate-900/12 backdrop-blur-xl',
                isExiting
                  ? 'animate-out slide-out-to-right fade-out zoom-out-95 duration-200'
                  : 'animate-in slide-in-from-right fade-in zoom-in-95 duration-200',
              ].join(' ')}
            >
              <div className="flex items-start gap-3">
                <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${meta.tone}`} aria-hidden="true">
                  <Icon className="h-5 w-5" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-text">{item.title}</p>
                  {item.description && <p className="mt-1 text-sm leading-6 text-text-muted">{item.description}</p>}
                </div>
                <button
                  className="rounded-lg p-1 text-text-muted hover:bg-bg hover:text-text focus-visible:outline focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-primary/20"
                  onClick={() => requestRemove(item.id)}
                  aria-label="关闭提示"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}
