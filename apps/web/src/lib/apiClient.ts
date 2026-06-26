type JsonInit = Omit<RequestInit, 'body'> & {
  body?: BodyInit | object | null
}

function isPlainJsonBody(body: JsonInit['body']): body is object {
  return Boolean(
    body &&
    typeof body === 'object' &&
    !(body instanceof FormData) &&
    !(body instanceof Blob) &&
    !(body instanceof URLSearchParams),
  )
}

export function accessToken() {
  return localStorage.getItem('access_token') || ''
}

export async function apiJson<T>(url: string, init: JsonInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const body: BodyInit | null | undefined = isPlainJsonBody(init.body)
    ? JSON.stringify(init.body)
    : init.body

  if (isPlainJsonBody(init.body) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const token = accessToken()
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const response = await fetch(url, { ...init, headers, body })
  if (!response.ok) {
    let message = `HTTP ${response.status}`
    try {
      const payload = await response.json()
      const detail = payload.detail
      if (typeof detail === 'string') {
        message = detail
      } else if (detail && typeof detail === 'object') {
        message = detail.message || detail.error || payload.error || message
      } else {
        message = payload.error || payload.message || message
      }
    } catch {
      const text = await response.text().catch(() => '')
      if (text) message = text
    }
    throw new Error(message)
  }

  return response.json() as Promise<T>
}
