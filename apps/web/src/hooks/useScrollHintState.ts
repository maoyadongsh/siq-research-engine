import { useEffect } from 'react'

const SCROLL_HINT_SELECTOR = '.scroll-hint'
const SCROLLABLE_CLASS = 'is-scrollable'
const SCROLLABLE_LEFT_CLASS = 'is-scrollable-left'
const SCROLLABLE_RIGHT_CLASS = 'is-scrollable-right'

function updateScrollHint(element: HTMLElement) {
  const maxScrollLeft = element.scrollWidth - element.clientWidth
  const scrollLeft = Math.max(0, element.scrollLeft)
  const canScroll = maxScrollLeft > 1

  element.classList.toggle(SCROLLABLE_CLASS, canScroll)
  element.classList.toggle(SCROLLABLE_LEFT_CLASS, canScroll && scrollLeft > 1)
  element.classList.toggle(SCROLLABLE_RIGHT_CLASS, canScroll && scrollLeft < maxScrollLeft - 1)
}

export function useScrollHintState(dependency: unknown) {
  useEffect(() => {
    if (typeof document === 'undefined') return

    let frame = 0
    const observedElements = new Set<HTMLElement>()
    const resizeObserver = new ResizeObserver((entries) => {
      entries.forEach((entry) => updateScrollHint(entry.target as HTMLElement))
    })

    const handleScroll = (event: Event) => {
      updateScrollHint(event.currentTarget as HTMLElement)
    }

    const observeElement = (element: HTMLElement) => {
      if (observedElements.has(element)) return
      observedElements.add(element)
      element.addEventListener('scroll', handleScroll, { passive: true })
      resizeObserver.observe(element)
    }

    const syncElements = () => {
      const currentElements = new Set(document.querySelectorAll<HTMLElement>(SCROLL_HINT_SELECTOR))

      observedElements.forEach((element) => {
        if (!currentElements.has(element)) {
          element.removeEventListener('scroll', handleScroll)
          resizeObserver.unobserve(element)
          observedElements.delete(element)
        }
      })

      currentElements.forEach((element) => {
        observeElement(element)
        updateScrollHint(element)
      })
    }

    const scheduleSync = () => {
      window.cancelAnimationFrame(frame)
      frame = window.requestAnimationFrame(syncElements)
    }

    syncElements()

    const mutationObserver = new MutationObserver(scheduleSync)
    mutationObserver.observe(document.body, { childList: true, subtree: true })
    window.addEventListener('resize', scheduleSync)

    return () => {
      window.cancelAnimationFrame(frame)
      window.removeEventListener('resize', scheduleSync)
      mutationObserver.disconnect()
      resizeObserver.disconnect()
      observedElements.forEach((element) => element.removeEventListener('scroll', handleScroll))
      observedElements.clear()
    }
  }, [dependency])
}
