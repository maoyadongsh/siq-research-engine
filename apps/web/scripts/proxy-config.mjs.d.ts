export interface CreateProxyOptions {
  backendUrl?: string
  reportFinderUrl?: string
  pdfApiUrl?: string
  includeAuth?: boolean
  includeEval?: boolean
}

export interface ProxyRule {
  prefix: string
  target: string
  rewrite?: (url: string) => string
}

export function createProxyRules(options?: CreateProxyOptions): ProxyRule[]
export function createViteProxy(options?: CreateProxyOptions): Record<
  string,
  {
    target: string
    changeOrigin: true
    rewrite?: (path: string) => string
  }
>
