import { REPORT_VIEWER_THEME } from './reportViewerTheme'
import { REPORT_SOURCE_LINK_BRIDGE_SCRIPT } from './reportFrameSandbox'

export function escapeHtmlAttribute(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

export function buildReportSrcDoc(html: string, reportUrl: string) {
  if (!html) return ''
  const baseUrl = typeof window === 'undefined'
    ? reportUrl
    : new URL(reportUrl, window.location.origin).toString()
  const hasViewport = /<meta[^>]+name=["']viewport["']/i.test(html)
  const viewportInjection = hasViewport
    ? ''
    : '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
  const headStartInjection = `<base href="${escapeHtmlAttribute(baseUrl)}"><meta name="color-scheme" content="light">${viewportInjection}`
  const headEndInjection = `<style id="siq-report-light-theme">${REPORT_VIEWER_THEME}</style>${REPORT_SOURCE_LINK_BRIDGE_SCRIPT}`

  let nextHtml = html
  if (/<head(\s[^>]*)?>/i.test(nextHtml)) {
    nextHtml = nextHtml.replace(/<head(\s[^>]*)?>/i, (match) => `${match}${headStartInjection}`)
  } else {
    nextHtml = nextHtml.replace(/<html(\s[^>]*)?>/i, (match) => `${match}<head>${headStartInjection}</head>`)
  }

  if (/<\/head>/i.test(nextHtml)) {
    return nextHtml.replace(/<\/head>/i, `${headEndInjection}</head>`)
  }

  return `${headStartInjection}${headEndInjection}${nextHtml}`
}
