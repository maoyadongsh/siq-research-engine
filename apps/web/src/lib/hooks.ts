import { useEffect, useCallback } from 'react'

export function useTheme() {
  useEffect(() => {
    document.documentElement.classList.remove('dark')
    localStorage.setItem('theme', 'light')
  }, [])

  return { theme: 'light' as 'light' | 'dark', toggle: () => {} }
}

export function useApi() {
  const getBaseUrl = useCallback(() => {
    return localStorage.getItem('api_base') || ''
  }, [])

  const apiUrl = useCallback((path: string) => {
    const base = getBaseUrl().replace(/\/$/, '')
    return base ? base + path : path
  }, [getBaseUrl])

  return { apiUrl }
}
