type PlainObject = Record<string, unknown> | unknown[]

export interface ApiErrorInit {
  status: number
  payload?: unknown
  responseText?: string
  url?: string
}

export interface ApiRequestInit extends Omit<RequestInit, 'body'> {
  body?: BodyInit | object | null
}

export class ApiError extends Error {
  status: number

  payload?: unknown

  response?: unknown

  responseText?: string

  url?: string

  constructor(message: string, init: ApiErrorInit) {
    super(message)
    this.name = 'ApiError'
    this.status = init.status
    this.payload = init.payload
    this.response = init.payload
    this.responseText = init.responseText
    this.url = init.url
  }
}

function isPlainJsonBody(body: unknown): body is PlainObject {
  if (!body || typeof body !== 'object') return false
  if (body instanceof FormData || body instanceof Blob || body instanceof URLSearchParams) return false
  if (ArrayBuffer.isView(body) || body instanceof ArrayBuffer) return false
  const proto = Object.getPrototypeOf(body)
  return proto === Object.prototype || proto === Array.prototype || proto === null
}

function toUrlString(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.toString()
  return input.url
}

interface SiqNativeConfig {
  SIQ_API_BASE?: unknown
}

function isNativeAssetShell() {
  return typeof window !== 'undefined'
    && window.location.protocol === 'capacitor:'
    && window.location.hostname === 'localhost'
}

function trustedNativeApiOrigin(): string | null {
  if (!isNativeAssetShell()) return null
  const config = (globalThis as typeof globalThis & {
    __SIQ_NATIVE_CONFIG__?: SiqNativeConfig
  }).__SIQ_NATIVE_CONFIG__
  if (!config || typeof config.SIQ_API_BASE !== 'string') return null
  try {
    const parsed = new URL(config.SIQ_API_BASE)
    if (
      parsed.protocol !== 'https:'
      || parsed.username
      || parsed.password
      || parsed.pathname !== '/'
      || parsed.search
      || parsed.hash
    ) return null
    return parsed.origin
  } catch {
    return null
  }
}

/** Resolve only SIQ API paths in the trusted native WebView; ordinary Web stays relative and same-origin. */
export function resolveSiqApiUrl(url: string): string {
  if (!(url === '/api' || url.startsWith('/api/'))) return url
  if (!isNativeAssetShell()) return url
  const nativeOrigin = trustedNativeApiOrigin()
  if (!nativeOrigin) throw new Error('SIQ_NATIVE_API_CONFIGURATION_INVALID')
  return new URL(url, `${nativeOrigin}/`).toString()
}

function nativeApiPath(input: URL) {
  if (
    input.protocol !== 'capacitor:'
    || input.hostname !== 'localhost'
    || !(input.pathname === '/api' || input.pathname.startsWith('/api/'))
  ) return null
  return `${input.pathname}${input.search}${input.hash}`
}

function resolveApiRequestInput(input: RequestInfo | URL): RequestInfo | URL {
  if (typeof input === 'string') {
    if (input === '/api' || input.startsWith('/api/')) return resolveSiqApiUrl(input)
    let parsed: URL
    try {
      parsed = new URL(input)
    } catch {
      return input
    }
    const path = nativeApiPath(parsed)
    return path ? resolveSiqApiUrl(path) : input
  }
  if (input instanceof URL) {
    const path = nativeApiPath(input)
    return path ? new URL(resolveSiqApiUrl(path)) : input
  }
  const path = nativeApiPath(new URL(input.url))
  return path ? new Request(resolveSiqApiUrl(path), input) : input
}

function parseApiUrl(url: string): URL {
  try {
    return new URL(url)
  } catch {
    return new URL(url, window.location.origin)
  }
}

export function shouldAttachAuth(url: string): boolean {
  if (url.startsWith('/api/') || url === '/api') return true
  if (typeof window === 'undefined') return false
  try {
    const parsed = parseApiUrl(url)
    const isSiqApiPath = parsed.pathname.startsWith('/api/') || parsed.pathname === '/api'
    if (!isSiqApiPath) return false
    if (parsed.origin === window.location.origin) return true

    const nativeOrigin = trustedNativeApiOrigin()
    if (nativeOrigin && parsed.origin === nativeOrigin) return true

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

function readStoredAccessToken() {
  try {
    return localStorage.getItem('access_token') || ''
  } catch {
    return ''
  }
}

export function accessToken() {
  return readStoredAccessToken()
}

export const SESSION_INVALIDATED_EVENT = 'siq:session-invalidated'
let sessionInvalidationPending = false

/** Notify the auth boundary once when an authenticated same-origin API session expires. */
function notifySessionInvalidated(url: string, status: number) {
  if (status !== 401 || !shouldAttachAuth(url) || typeof window === 'undefined') return
  let pathname: string
  try {
    pathname = parseApiUrl(url).pathname
  } catch {
    return
  }
  if (/\/api\/auth\/(?:login|logout)$/.test(pathname) || sessionInvalidationPending) return
  sessionInvalidationPending = true
  window.dispatchEvent(new CustomEvent(SESSION_INVALIDATED_EVENT, { detail: { url, status } }))
}

export function resetSessionInvalidationChannel() {
  sessionInvalidationPending = false
}

function truthyFlag(value: unknown): boolean {
  return ['1', 'true', 'yes', 'on'].includes(String(value ?? '').trim().toLowerCase())
}

function runtimeFlag(name: string): string {
  const metaEnv = (import.meta as unknown as { env?: Record<string, string | undefined> }).env
  const envValue = metaEnv?.[name] ?? metaEnv?.[`VITE_${name}`]
  if (envValue !== undefined) return envValue

  const runtimeConfig = (globalThis as typeof globalThis & {
    __SIQ_CONFIG__?: Record<string, unknown>
  }).__SIQ_CONFIG__
  const configValue = runtimeConfig?.[name] ?? runtimeConfig?.[`VITE_${name}`]
  if (configValue !== undefined) return String(configValue)

  try {
    return localStorage.getItem(name) ?? localStorage.getItem(`VITE_${name}`) ?? ''
  } catch {
    return ''
  }
}

export function authCookieModeEnabled() {
  return truthyFlag(runtimeFlag('SIQ_AUTH_COOKIE_MODE'))
}

export function csrfCookieName() {
  return runtimeFlag('SIQ_AUTH_CSRF_COOKIE_NAME') || 'siq_csrf_token'
}

function readCookie(name: string): string {
  if (typeof document === 'undefined') return ''
  const prefix = `${encodeURIComponent(name)}=`
  const item = document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part === prefix.slice(0, -1) || part.startsWith(prefix))
  if (!item || !item.startsWith(prefix)) return ''
  try {
    return decodeURIComponent(item.slice(prefix.length))
  } catch {
    return item.slice(prefix.length)
  }
}

export function csrfToken() {
  return readCookie(csrfCookieName())
}

function requestMethod(input: RequestInfo | URL, init: ApiRequestInit | RequestInit): string {
  const method = init.method || (typeof input !== 'string' && !(input instanceof URL) ? input.method : undefined)
  return String(method || 'GET').toUpperCase()
}

function isUnsafeMethod(method: string): boolean {
  return !['GET', 'HEAD', 'OPTIONS', 'TRACE'].includes(method.toUpperCase())
}

export function attachCsrfHeader(headers: Headers, url: string, method: string) {
  if (!authCookieModeEnabled() || !shouldAttachAuth(url) || !isUnsafeMethod(method)) return
  if (headers.has('Authorization') || headers.has('X-CSRF-Token') || headers.has('X-SIQ-CSRF-Token')) return
  const token = csrfToken()
  if (token) headers.set('X-CSRF-Token', token)
}

function attachAuthorization(headers: Headers, url: string) {
  const token = accessToken()
  if (token && shouldAttachAuth(url) && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }
}

function prepareBodyAndHeaders(init: ApiRequestInit, headers: Headers): BodyInit | null | undefined {
  const body = init.body
  if (isPlainJsonBody(body)) {
    if (!headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }
    return JSON.stringify(body)
  }
  return body as BodyInit | null | undefined
}

function safeJsonParse(text: string): unknown | undefined {
  const trimmed = text.trim()
  if (!trimmed) return undefined
  try {
    return JSON.parse(trimmed) as unknown
  } catch {
    return undefined
  }
}

function extractMessage(payload: unknown): string | undefined {
  if (typeof payload === 'string') {
    return payload.trim() || undefined
  }
  if (!payload || typeof payload !== 'object') return undefined
  const record = payload as Record<string, unknown>
  const detail = record.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object') {
    const nested = detail as Record<string, unknown>
    for (const key of ['message', 'error', 'detail']) {
      const value = nested[key]
      if (typeof value === 'string' && value.trim()) return value
    }
  }
  for (const key of ['error', 'message', 'stderr', 'stdout']) {
    const value = record[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  return undefined
}

function buildErrorMessage(response: Response, responseText: string): { message: string; payload?: unknown } {
  const trimmed = responseText.trim()
  if (!trimmed) {
    return { message: `HTTP ${response.status}` }
  }

  if (/^<!doctype html/i.test(trimmed) || /^<html[\s>]/i.test(trimmed)) {
    return {
      message: `接口返回了 HTML 页面，未命中后端 API（HTTP ${response.status}）。请检查 /api 代理或后端 18081。`,
      payload: trimmed,
    }
  }

  const payload = safeJsonParse(trimmed)
  const parsedMessage = extractMessage(payload)
  if (parsedMessage) {
    return { message: parsedMessage, payload }
  }

  const preview = trimmed.replace(/\s+/g, ' ').slice(0, 200)
  return {
    message: `HTTP ${response.status}: ${preview}`,
    payload: payload ?? trimmed,
  }
}

export async function apiFetch(input: RequestInfo | URL, init: ApiRequestInit = {}) {
  const resolvedInput = resolveApiRequestInput(input)
  const url = toUrlString(resolvedInput)
  const headers = new Headers(init.headers || (typeof resolvedInput !== 'string' && !(resolvedInput instanceof URL) ? resolvedInput.headers : undefined))
  attachAuthorization(headers, url)

  const body = prepareBodyAndHeaders(init, headers)
  attachCsrfHeader(headers, url, requestMethod(resolvedInput, init))
  const requestInit: RequestInit = { ...init, headers, body }
  if (requestInit.credentials === undefined && authCookieModeEnabled() && shouldAttachAuth(url)) {
    requestInit.credentials = 'include'
  }
  const response = await globalThis.fetch(resolvedInput, requestInit)
  notifySessionInvalidated(url, response.status)
  return response
}

export async function apiStreamFetch(input: RequestInfo | URL, init: ApiRequestInit = {}) {
  return apiFetch(input, init)
}

export async function readJsonResponse<T = unknown>(response: Response): Promise<T> {
  const text = await response.text()
  const trimmed = text.trim()
  if (!trimmed) return {} as T
  try {
    return JSON.parse(trimmed) as T
  } catch {
    const preview = trimmed.replace(/\s+/g, ' ').slice(0, 120)
    if (/^<!doctype html/i.test(trimmed) || /^<html[\s>]/i.test(trimmed)) {
      throw new Error(`接口返回了 HTML 页面，未命中后端 API（HTTP ${response.status}）。请检查 /api 代理或后端 18081。`)
    }
    throw new Error(`接口返回非 JSON 内容（HTTP ${response.status}）：${preview}`)
  }
}

async function throwIfNotOk(response: Response): Promise<void> {
  if (response.ok) return
  const responseText = await response.text().catch(() => '')
  const { message, payload } = buildErrorMessage(response, responseText)
  throw new ApiError(message, {
    status: response.status,
    payload,
    responseText,
    url: response.url || undefined,
  })
}

export async function apiJson<T = unknown>(input: RequestInfo | URL, init: ApiRequestInit = {}): Promise<T> {
  const response = await apiFetch(input, init)
  const text = await response.text()
  if (!response.ok) {
    const { message, payload } = buildErrorMessage(response, text)
    throw new ApiError(message, {
      status: response.status,
      payload,
      responseText: text,
      url: response.url || undefined,
    })
  }

  const trimmed = text.trim()
  if (!trimmed) return {} as T
  try {
    return JSON.parse(trimmed) as T
  } catch {
    const preview = trimmed.replace(/\s+/g, ' ').slice(0, 120)
    throw new Error(`接口返回非 JSON 内容（HTTP ${response.status}）：${preview}`)
  }
}

export async function apiText(input: RequestInfo | URL, init: ApiRequestInit = {}): Promise<string> {
  const response = await apiFetch(input, init)
  if (!response.ok) {
    await throwIfNotOk(response)
  }
  return response.text()
}

export async function apiBlob(input: RequestInfo | URL, init: ApiRequestInit = {}): Promise<Blob> {
  const response = await apiFetch(input, init)
  if (!response.ok) {
    await throwIfNotOk(response)
  }
  return response.blob()
}
