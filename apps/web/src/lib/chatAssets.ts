function isLocalOrPrivateHost(hostname: string) {
  return (
    hostname === 'localhost' ||
    hostname === '127.0.0.1' ||
    hostname.startsWith('192.168.') ||
    hostname.startsWith('10.') ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(hostname)
  )
}

function shouldUseCurrentOrigin(url: URL) {
  if (!url.pathname.startsWith('/api/')) return false
  if (typeof window === 'undefined') return false
  return isLocalOrPrivateHost(url.hostname) || url.hostname === window.location.hostname
}

export function isSafeChatAssetHref(href: string) {
  return /^(https?:\/\/|\/|#)/.test(href)
}

export function normalizeChatAssetUrl(href: string) {
  if (typeof window === 'undefined') return href
  if (href.startsWith('/api/')) return `${window.location.origin}${href}`
  try {
    const url = new URL(href)
    if (shouldUseCurrentOrigin(url)) {
      return `${window.location.origin}${url.pathname}${url.search}${url.hash}`
    }
  } catch {
    return href
  }
  return href
}
