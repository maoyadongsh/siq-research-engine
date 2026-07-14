import { apiFetch, apiJson, readJsonResponse } from '@/shared/api/client'

import type {
  MeetingImportChunkResult,
  MeetingImportCreateInput,
  MeetingImportStatus,
} from './meetingImportTypes'

const IMPORT_API_BASE = '/api/meetings/v1/imports'

export function createMeetingImport(input: MeetingImportCreateInput, idempotencyKey: string) {
  return apiJson<MeetingImportStatus>(IMPORT_API_BASE, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: input,
  })
}

export function getMeetingImport(uploadId: string, signal?: AbortSignal) {
  return apiJson<MeetingImportStatus>(`${IMPORT_API_BASE}/${encodeURIComponent(uploadId)}`, { signal })
}

export async function putMeetingImportChunk(
  uploadId: string,
  ordinal: number,
  offset: number,
  chunk: Blob,
  sha256: string,
  signal?: AbortSignal,
) {
  const response = await apiFetch(
    `${IMPORT_API_BASE}/${encodeURIComponent(uploadId)}/chunks/${ordinal}`,
    {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/octet-stream',
        'X-Chunk-Offset': String(offset),
        'X-Chunk-SHA256': sha256,
      },
      body: chunk,
      signal,
    },
  )
  if (!response.ok) {
    const payload: Record<string, unknown> = await readJsonResponse<Record<string, unknown>>(response).catch(() => ({}))
    const detail = payload.detail as Record<string, unknown> | undefined
    const message = typeof detail?.message === 'string' ? detail.message : `分片上传失败（HTTP ${response.status}）`
    const error = new Error(message) as Error & { status?: number; code?: string }
    error.status = response.status
    error.code = typeof detail?.code === 'string' ? detail.code : undefined
    throw error
  }
  return readJsonResponse<MeetingImportChunkResult>(response)
}

export function completeMeetingImport(uploadId: string) {
  return apiJson<MeetingImportStatus>(`${IMPORT_API_BASE}/${encodeURIComponent(uploadId)}/complete`, {
    method: 'POST',
    body: {},
  })
}

export function retryMeetingImport(uploadId: string) {
  return apiJson<MeetingImportStatus>(`${IMPORT_API_BASE}/${encodeURIComponent(uploadId)}/retry`, {
    method: 'POST',
    body: {},
  })
}

export function cancelMeetingImport(uploadId: string) {
  return apiJson<MeetingImportStatus>(`${IMPORT_API_BASE}/${encodeURIComponent(uploadId)}`, {
    method: 'DELETE',
  })
}

export async function sha256Blob(blob: Blob) {
  const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, '0')).join('')
}
