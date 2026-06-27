const DEFAULT_BACKEND_URL = 'http://127.0.0.1:18081'
const DEFAULT_REPORT_FINDER_URL = 'http://127.0.0.1:18000'
const DEFAULT_PDFAPI_URL = 'http://127.0.0.1:15000'

const BACKEND_PREFIXES = [
  '/api/v1',
  '/api/chat',
  '/api/wiki',
  '/api/analysis',
  '/api/factchecker',
  '/api/tracking',
  '/api/legal',
  '/api/settings',
  '/api/system',
  '/api/market-report-health',
  '/api/us-sec',
  '/api/downloads',
  '/api/workflow',
  '/api/workspace',
  '/api/documents',
  '/api/pdf',
  '/api/pdf_page',
  '/api/source',
]

function firstEnv(names) {
  for (const name of names) {
    const value = process.env[name]
    if (value && value.trim()) return value.trim()
  }
  return ''
}

function resolveUrl(value, envNames, fallback) {
  return (value || firstEnv(envNames) || fallback).replace(/\/+$/, '')
}

function pdfApiHeaders() {
  const token = firstEnv(['PDF2MD_ACCESS_TOKEN', 'SIQ_PDF2MD_ACCESS_TOKEN'])
  return token ? { 'X-PDF2MD-Token': token } : undefined
}

export function createProxyRules(options = {}) {
const backendUrl = resolveUrl(
    options.backendUrl,
    ['SIQ_BACKEND_URL', 'TRIAL_BACKEND_URL'],
    DEFAULT_BACKEND_URL,
  )
  const reportFinderUrl = resolveUrl(
    options.reportFinderUrl,
    ['SIQ_REPORT_FINDER_URL', 'TRIAL_REPORT_FINDER_URL'],
    DEFAULT_REPORT_FINDER_URL,
  )
  const pdfApiUrl = resolveUrl(
    options.pdfApiUrl,
    ['SIQ_PDFAPI_URL', 'TRIAL_PDFAPI_URL'],
    DEFAULT_PDFAPI_URL,
  )

  const backendPrefixes = [
    ...(options.includeAuth === false ? [] : ['/api/auth']),
    ...(options.includeEval === false ? [] : ['/api/eval']),
    ...BACKEND_PREFIXES,
  ]

  return [
    ...backendPrefixes.map((prefix) => ({ prefix, target: backendUrl })),
    { prefix: '/api', target: reportFinderUrl, rewrite: (url) => url.replace(/^\/api/, '') || '/' },
    {
      prefix: '/pdfapi',
      target: pdfApiUrl,
      rewrite: (url) => url.replace(/^\/pdfapi/, '/api'),
      headers: pdfApiHeaders(),
    },
  ]
}

export function createViteProxy(options = {}) {
  return Object.fromEntries(
    createProxyRules(options).map((rule) => [
      rule.prefix,
      {
        target: rule.target,
        changeOrigin: true,
        ...(rule.rewrite ? { rewrite: rule.rewrite } : {}),
        ...(rule.headers ? { headers: rule.headers } : {}),
      },
    ]),
  )
}
