import { apiFetch } from './apiClient'

export async function fetchWithAuth(input: RequestInfo | URL, init: RequestInit = {}) {
  return apiFetch(input, init)
}

export function installFetchAuth() {
  // Deprecated compatibility shim. Auth is applied by explicit apiFetch calls.
}
