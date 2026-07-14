import { useEffect, useState } from 'react'
import { fetchWithAuth } from './fetchWithAuth'
import { buildReadingHtmlDocument } from './pdfSanitize'
import { buildUsSecReadingHtmlDocument } from './usSecHtmlSanitize'

export function useAuthenticatedBlobUrl(url: string) {
  const [blobUrl, setBlobUrl] = useState('')

  useEffect(() => {
    let cancelled = false
    let objectUrl = ''

    if (!url) {
      queueMicrotask(() => {
        if (!cancelled) setBlobUrl('')
      })
      return () => {
        cancelled = true
      }
    }

    fetchWithAuth(url)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.blob()
      })
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setBlobUrl(objectUrl)
      })
      .catch(() => {
        if (!cancelled) setBlobUrl('')
      })

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [url])

  return blobUrl
}

function useAuthenticatedHtmlUrl(url: string, buildDocument: (html: string | null) => string) {
  const [blobUrl, setBlobUrl] = useState('')

  useEffect(() => {
    let cancelled = false
    let objectUrl = ''

    if (!url) {
      queueMicrotask(() => {
        if (!cancelled) setBlobUrl('')
      })
      return () => {
        cancelled = true
      }
    }

    fetchWithAuth(url)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.text()
      })
      .then((html) => {
        if (cancelled) return
        const documentHtml = buildDocument(html)
        objectUrl = URL.createObjectURL(new Blob([documentHtml], { type: 'text/html;charset=utf-8' }))
        setBlobUrl(objectUrl)
      })
      .catch(() => {
        if (!cancelled) setBlobUrl('')
      })

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [buildDocument, url])

  return blobUrl
}

export function useAuthenticatedReadingHtmlUrl(url: string) {
  return useAuthenticatedHtmlUrl(url, buildReadingHtmlDocument)
}

export function useAuthenticatedUsSecHtmlUrl(url: string) {
  return useAuthenticatedHtmlUrl(url, buildUsSecReadingHtmlDocument)
}

export async function downloadAuthenticatedFile(url: string, filename?: string, init?: RequestInit) {
  const response = await fetchWithAuth(url, init)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  if (filename) link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
}
