import { useState, useEffect, useCallback } from 'react'

export function useTheme() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    if (typeof window !== 'undefined') {
      return (localStorage.getItem('theme') as 'light' | 'dark') || 'light'
    }
    return 'light'
  })

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    localStorage.setItem('theme', theme)
  }, [theme])

  const toggle = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))

  return { theme, toggle }
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
