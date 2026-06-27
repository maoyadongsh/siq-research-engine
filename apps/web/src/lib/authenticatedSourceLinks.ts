import { fetchWithAuth } from './fetchWithAuth'
import { normalizeChatAssetUrl } from './chatAssets'

const SOURCE_LINK_RE = /\/api\/(?:(?:source|pdf_page)\/|downloads\/report-file|documents\/(?:source|artifact|figures|download)\/|pdf\/(?:source|pdf_page|artifact|download|download_complete|download_corrected|financial|quality|result)\/)/
const SOURCE_ACCESS_RE = /\/api\/source_access\//
const PDF_PAGE_RE = /^\/api\/pdf_page\/([^/]+)\/(\d+)\/?$/
const SOURCE_PAGE_RE = /^\/api\/source\/([^/]+)\/page\/(\d+)\/?$/
const SOURCE_TABLE_RE = /^\/api\/source\/([^/]+)\/table\/(\d+)\/?$/

type ClickLike = {
  button: number
  metaKey: boolean
  ctrlKey: boolean
  shiftKey: boolean
  altKey: boolean
  preventDefault: () => void
}

export function isAuthenticatedSourceLink(href: string) {
  if (!href || href.startsWith('#') || href.startsWith('blob:') || href.startsWith('data:')) return false
  try {
    const normalized = normalizeChatAssetUrl(href)
    const url = new URL(normalized, window.location.origin)
    return SOURCE_LINK_RE.test(url.pathname) || SOURCE_ACCESS_RE.test(url.pathname)
  } catch {
    return SOURCE_LINK_RE.test(href) || SOURCE_ACCESS_RE.test(href)
  }
}

function sourceAccessPath(href: string) {
  const normalized = normalizeChatAssetUrl(href)
  const url = new URL(normalized, window.location.origin)
  const matchers: Array<[RegExp, string]> = [
    [PDF_PAGE_RE, 'pdf_page'],
    [SOURCE_PAGE_RE, 'source_page'],
    [SOURCE_TABLE_RE, 'source_table'],
  ]
  for (const [pattern, kind] of matchers) {
    const match = url.pathname.match(pattern)
    if (match) {
      return `/api/source_access/${kind}/${encodeURIComponent(match[1])}/${match[2]}`
    }
  }
  return null
}

async function resolveAuthenticatedSourceUrl(href: string) {
  const accessPath = sourceAccessPath(href)
  if (!accessPath) return null
  const response = await fetchWithAuth(accessPath)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const data = await response.json().catch(() => null) as { url?: unknown } | null
  return typeof data?.url === 'string' && data.url ? data.url : null
}

export async function openAuthenticatedSourceLink(href: string) {
  const popup = window.open('about:blank', '_blank')
  if (popup) popup.opener = null

  let signedUrl: string | null = null
  try {
    signedUrl = await resolveAuthenticatedSourceUrl(href)
  } catch (error) {
    console.warn('Failed to resolve authenticated source URL; falling back to blob open', error)
  }
  if (signedUrl) {
    if (popup) {
      popup.location.href = signedUrl
    } else {
      window.open(signedUrl, '_blank', 'noopener,noreferrer')
    }
    return
  }

  const normalized = normalizeChatAssetUrl(href)
  const response = await fetchWithAuth(normalized)
  if (!response.ok) {
    popup?.close()
    throw new Error(`HTTP ${response.status}`)
  }

  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  if (popup) {
    popup.location.href = objectUrl
  } else {
    window.open(objectUrl, '_blank', 'noopener,noreferrer')
  }
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
}

export async function handleAuthenticatedSourceClick(
  event: ClickLike,
  href: string,
) {
  if (!isAuthenticatedSourceLink(href)) return false
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false
  event.preventDefault()
  await openAuthenticatedSourceLink(href)
  return true
}
