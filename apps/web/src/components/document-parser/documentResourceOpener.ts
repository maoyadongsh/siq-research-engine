import { useCallback, useState } from 'react'
import { openDocumentResource } from '../../features/document-parser/api'

export type DocumentResourceOpenerDependencies = {
  openDocumentResource?: (url: string, filename?: string) => Promise<void>
}

export type DocumentResourceOpenerResult = {
  resourceError: string
  clearResourceError: () => void
  openResource: (url: string, filename?: string) => Promise<void>
}

export async function openDocumentResourceWithFeedback({
  url,
  filename,
  setResourceError,
  openDocumentResourceImpl = openDocumentResource,
}: {
  url: string
  filename?: string
  setResourceError: (value: string) => void
  openDocumentResourceImpl?: (url: string, filename?: string) => Promise<void>
}) {
  if (!url) return
  setResourceError('')
  try {
    await openDocumentResourceImpl(url, filename)
  } catch (err) {
    setResourceError(err instanceof Error ? err.message : '产物打开失败')
  }
}

export function useDocumentResourceOpener({
  openDocumentResource: openDocumentResourceImpl = openDocumentResource,
}: DocumentResourceOpenerDependencies = {}): DocumentResourceOpenerResult {
  const [resourceError, setResourceError] = useState('')

  const clearResourceError = useCallback(() => {
    setResourceError('')
  }, [])

  const openResource = useCallback(
    (url: string, filename?: string) => openDocumentResourceWithFeedback({
      url,
      filename,
      setResourceError,
      openDocumentResourceImpl,
    }),
    [openDocumentResourceImpl],
  )

  return {
    resourceError,
    clearResourceError,
    openResource,
  }
}
