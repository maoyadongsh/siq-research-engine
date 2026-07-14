import { useCallback, useSyncExternalStore } from 'react'

export function useMediaQuery(query: string, serverFallback = false) {
  const subscribe = useCallback((onStoreChange: () => void) => {
    const media = window.matchMedia(query)
    media.addEventListener('change', onStoreChange)
    return () => media.removeEventListener('change', onStoreChange)
  }, [query])

  const getSnapshot = useCallback(
    () => typeof window !== 'undefined' && window.matchMedia(query).matches,
    [query],
  )
  const getServerSnapshot = useCallback(() => serverFallback, [serverFallback])

  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}
