import { accessToken } from './apiClient'

const nativeFetch = globalThis.fetch.bind(globalThis)

function shouldAttachAuth(url: string) {
  if (url.startsWith('/api/') || url === '/api' || url.startsWith('/pdfapi/')) return true
  if (typeof window === 'undefined') return false
  try {
    const parsed = new URL(url)
    const isSiqApiPath = parsed.pathname.startsWith('/api/') ||
      parsed.pathname === '/api' ||
      parsed.pathname.startsWith('/pdfapi/')
    if (!isSiqApiPath) return false
    if (parsed.origin === window.location.origin) return true

    const apiBase = window.localStorage.getItem('api_base') || ''
    if (!apiBase) return false
    try {
      return parsed.origin === new URL(apiBase).origin
    } catch {
      return false
    }
  } catch {
    return false
  }
}

export async function fetchWithAuth(input: RequestInfo | URL, init: RequestInit = {}) {
  const url = typeof input === 'string'
    ? input
    : input instanceof URL
      ? input.toString()
      : input.url

  const headers = new Headers(init.headers || (typeof input !== 'string' && !(input instanceof URL) ? input.headers : undefined))
  const token = accessToken()
  if (token && shouldAttachAuth(url) && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  return nativeFetch(input, { ...init, headers })
}

export function installFetchAuth() {
  globalThis.fetch = fetchWithAuth
}
