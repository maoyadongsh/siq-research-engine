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
