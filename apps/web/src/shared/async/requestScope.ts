export interface RequestScopeToken {
  requestId: number
  ownerId: string
  signal: AbortSignal
}

export interface RequestScope {
  begin: (ownerId?: string | null) => RequestScopeToken
  invalidate: (token?: RequestScopeToken | null) => boolean
  isCurrent: (token: RequestScopeToken | null | undefined, currentOwnerId?: string | null) => boolean
}

export function createRequestScope(): RequestScope {
  let latestRequestId = 0
  let activeToken: RequestScopeToken | null = null
  let activeController: AbortController | null = null

  return {
    begin(ownerId) {
      activeController?.abort()
      const controller = new AbortController()
      latestRequestId += 1
      activeController = controller
      activeToken = {
        requestId: latestRequestId,
        ownerId: String(ownerId || '').trim(),
        signal: controller.signal,
      }
      return activeToken
    },
    invalidate(token) {
      if (token && token !== activeToken) return false
      activeController?.abort()
      latestRequestId += 1
      activeController = null
      activeToken = null
      return true
    },
    isCurrent(token, currentOwnerId) {
      if (!token || token !== activeToken || token.requestId !== latestRequestId || token.signal.aborted) {
        return false
      }
      if (currentOwnerId === undefined) return true
      return token.ownerId === String(currentOwnerId || '').trim()
    },
  }
}
