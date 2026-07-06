import { accessToken, attachCsrfHeader, authCookieModeEnabled, shouldAttachAuth } from './apiClient'

const nativeFetch = globalThis.fetch.bind(globalThis)

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
  const method = init.method || (typeof input !== 'string' && !(input instanceof URL) ? input.method : undefined) || 'GET'
  attachCsrfHeader(headers, url, method)

  const requestInit: RequestInit = { ...init, headers }
  if (requestInit.credentials === undefined && authCookieModeEnabled() && shouldAttachAuth(url)) {
    requestInit.credentials = 'include'
  }

  return nativeFetch(input, requestInit)
}

export function installFetchAuth() {
  globalThis.fetch = fetchWithAuth
}
