/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const { apiFetch, authCookieModeEnabled } = await import('./client.ts')

function installMemoryLocalStorage() {
  const values = new Map<string, string>()
  const storage = {
    get length() {
      return values.size
    },
    clear() {
      values.clear()
    },
    getItem(key: string) {
      return values.has(key) ? values.get(key) ?? null : null
    },
    key(index: number) {
      return Array.from(values.keys())[index] ?? null
    },
    removeItem(key: string) {
      values.delete(key)
    },
    setItem(key: string, value: string) {
      values.set(key, String(value))
    },
  } satisfies Storage

  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: storage,
  })
  return storage
}

function installFetchRecorder() {
  let captured: RequestInit | undefined
  const fetchMock = async (_input: RequestInfo | URL, init?: RequestInit) => {
    captured = init
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  Object.defineProperty(globalThis, 'fetch', {
    configurable: true,
    value: fetchMock,
  })
  return () => captured
}

function installDocumentCookie(cookie: string) {
  Object.defineProperty(globalThis, 'document', {
    configurable: true,
    value: { cookie },
  })
}

test('apiFetch attaches bearer token from localStorage by default', async (t) => {
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const storage = installMemoryLocalStorage()
  const captured = installFetchRecorder()
  t.after(() => {
    Object.defineProperty(globalThis, 'fetch', { configurable: true, value: originalFetch })
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage)
    else Reflect.deleteProperty(globalThis, 'localStorage')
  })

  storage.setItem('access_token', 'bearer-token')

  await apiFetch('/api/example')

  const request = captured()
  const headers = request?.headers as Headers
  assert.equal(headers.get('Authorization'), 'Bearer bearer-token')
  assert.equal(request?.credentials, undefined)
})

test('apiFetch includes cookies for SIQ API requests in cookie mode', async (t) => {
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const storage = installMemoryLocalStorage()
  const captured = installFetchRecorder()
  t.after(() => {
    Object.defineProperty(globalThis, 'fetch', { configurable: true, value: originalFetch })
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage)
    else Reflect.deleteProperty(globalThis, 'localStorage')
  })

  storage.setItem('SIQ_AUTH_COOKIE_MODE', '1')

  await apiFetch('/api/example')

  const request = captured()
  const headers = request?.headers as Headers
  assert.equal(authCookieModeEnabled(), true)
  assert.equal(headers.has('Authorization'), false)
  assert.equal(request?.credentials, 'include')
})

test('apiFetch attaches CSRF token for cookie-mode unsafe SIQ API requests', async (t) => {
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')
  const storage = installMemoryLocalStorage()
  const captured = installFetchRecorder()
  installDocumentCookie('siq_csrf_token=csrf-token')
  t.after(() => {
    Object.defineProperty(globalThis, 'fetch', { configurable: true, value: originalFetch })
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage)
    else Reflect.deleteProperty(globalThis, 'localStorage')
    if (originalDocument) Object.defineProperty(globalThis, 'document', originalDocument)
    else Reflect.deleteProperty(globalThis, 'document')
  })

  storage.setItem('SIQ_AUTH_COOKIE_MODE', '1')

  await apiFetch('/api/example', { method: 'POST', body: { ok: true } })

  const request = captured()
  const headers = request?.headers as Headers
  assert.equal(headers.get('X-CSRF-Token'), 'csrf-token')
  assert.equal(headers.get('Content-Type'), 'application/json')
  assert.equal(request?.credentials, 'include')
})

test('apiFetch does not attach CSRF token to bearer unsafe requests', async (t) => {
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')
  const storage = installMemoryLocalStorage()
  const captured = installFetchRecorder()
  installDocumentCookie('siq_csrf_token=csrf-token')
  t.after(() => {
    Object.defineProperty(globalThis, 'fetch', { configurable: true, value: originalFetch })
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage)
    else Reflect.deleteProperty(globalThis, 'localStorage')
    if (originalDocument) Object.defineProperty(globalThis, 'document', originalDocument)
    else Reflect.deleteProperty(globalThis, 'document')
  })

  storage.setItem('SIQ_AUTH_COOKIE_MODE', '1')
  storage.setItem('access_token', 'bearer-token')

  await apiFetch('/api/example', { method: 'POST', body: { ok: true } })

  const request = captured()
  const headers = request?.headers as Headers
  assert.equal(headers.get('Authorization'), 'Bearer bearer-token')
  assert.equal(headers.has('X-CSRF-Token'), false)
  assert.equal(request?.credentials, 'include')
})
