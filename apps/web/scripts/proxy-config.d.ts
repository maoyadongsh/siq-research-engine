export interface CreateProxyOptions {
  backendUrl?: string
  meetingStreamGatewayUrl?: string
  reportFinderUrl?: string
  includeAuth?: boolean
  includeEval?: boolean
}

export interface ProxyRule {
  prefix: string
  target: string
  rewrite?: (url: string) => string
  headers?: Record<string, string>
  ws?: boolean
}

export function createProxyRules(options?: CreateProxyOptions): ProxyRule[]
export function createViteProxy(options?: CreateProxyOptions): Record<
  string,
  {
    target: string
    changeOrigin: true
    rewrite?: (path: string) => string
    headers?: Record<string, string>
    ws?: boolean
  }
>
