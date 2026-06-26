import { useLayoutEffect, type RefObject } from 'react'

export function useAutosizeTextarea(ref: RefObject<HTMLTextAreaElement | null>, value: string) {
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return

    el.style.height = 'auto'
    const maxHeight = Number.parseFloat(window.getComputedStyle(el).maxHeight)
    const nextHeight = Number.isFinite(maxHeight)
      ? Math.min(el.scrollHeight, maxHeight)
      : el.scrollHeight

    el.style.height = `${nextHeight}px`
    el.style.overflowY = el.scrollHeight > nextHeight + 1 ? 'auto' : 'hidden'
  }, [ref, value])
}
