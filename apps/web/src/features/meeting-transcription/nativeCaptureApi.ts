import { apiJson } from '@/shared/api/client'

import { MEETING_NATIVE_CAPTURE_SCHEMA_VERSION } from './nativeCapture'

const MEETING_API_BASE = '/api/meetings/v1'

export interface NativeCaptureCreateRequest {
  device_installation_id: string
  encoding: 'pcm_s16le'
  sample_rate: 16_000
  channels: 1
}

export interface NativeCaptureStatusResponse {
  schema_version: typeof MEETING_NATIVE_CAPTURE_SCHEMA_VERSION
  id: string
  meeting_id: string
  state: 'active' | 'sealed' | 'revoked'
  encoding: string
  sample_rate: number
  channels: number
  current_epoch: number
  total_bytes: number
  total_samples: number
  sealed_through_sample: number | null
  ingest_complete: boolean
  server_playback_state: 'not_ready' | 'pending_upload' | 'pending_packaging' | 'packaging' | 'ready' | 'failed'
  created_at: string
  updated_at: string
  sealed_at: string | null
  revoked_at: string | null
}

export interface NativeCaptureCreateResponse {
  schema_version: typeof MEETING_NATIVE_CAPTURE_SCHEMA_VERSION
  capture: NativeCaptureStatusResponse
  capture_token: string
  token_type: 'Bearer'
  token_expires_at: string
  scopes: string[]
  replayed: boolean
  limits: Record<string, number>
}

export interface NativeCaptureTokenResponse {
  schema_version: typeof MEETING_NATIVE_CAPTURE_SCHEMA_VERSION
  capture_id: string
  capture_token: string
  token_type: 'Bearer'
  token_expires_at: string
  scopes: string[]
}

export interface NativeCaptureCheckpointResponse {
  schema_version: typeof MEETING_NATIVE_CAPTURE_SCHEMA_VERSION
  capture_id: string
  meeting_id: string
  capture_checkpoint: Record<string, string | number | null>
  ingest_checkpoint: Record<string, unknown>
  realtime_checkpoint: Record<string, number>
  finalization_checkpoint: Record<string, unknown>
  epochs: Array<{
    stream_epoch: number
    state: 'active' | 'rolled_over' | 'sealed'
    highest_contiguous_sequence: number
    highest_received_sequence: number
    declared_last_sequence: number | null
    recorded_through_sample: number | null
    missing_sequence_ranges: Array<{ start: number; end: number }>
  }>
}

export interface NativeCaptureBoundaryRequest {
  expected_epoch: number
  final_sequence: number
  recorded_through_sample: number
  manifest_revision: number
  manifest_sha256: string
  manifest_entries: NativeCaptureManifestEntry[]
}

export interface NativeCaptureManifestEntry {
  sequence: number
  first_sample: number
  sample_count: number
  captured_monotonic_ns: number
  encoding: 'pcm_s16le'
  sample_rate: 16_000
  channels: 1
  sha256: string
}

export interface NativeCaptureBatchMetadata {
  stream_epoch: number
  sequence: number
  first_sample: number
  sample_count: number
  captured_monotonic_ns: number
  encoding: 'pcm_s16le'
  sample_rate: 16_000
  channels: 1
  sha256: string
  manifest_revision: number
  idempotency_key: string
}

export interface NativeCaptureBatchResponse {
  schema_version: typeof MEETING_NATIVE_CAPTURE_SCHEMA_VERSION
  capture_id: string
  stream_epoch: number
  sequence: number
  first_sample: number
  sample_count: number
  sha256: string
  byte_size: number
  replayed: boolean
  checkpoint: NativeCaptureCheckpointResponse
}

export interface NativeCaptureRolloverResponse {
  schema_version: typeof MEETING_NATIVE_CAPTURE_SCHEMA_VERSION
  capture_id: string
  previous_epoch: number
  stream_epoch: number
  stream_ticket: string
  stream_ticket_expires_at: string
  ws_url: string
  last_acked_sequence: number
  capture_offset_ms: number
  reconnect_window_seconds: number
  replayed: boolean
  checkpoint: NativeCaptureCheckpointResponse
}

export interface NativeCaptureSealResponse {
  schema_version: typeof MEETING_NATIVE_CAPTURE_SCHEMA_VERSION
  capture: NativeCaptureStatusResponse
  checkpoint: NativeCaptureCheckpointResponse
  replayed: boolean
}

export interface NativeCaptureGapRequest {
  stream_epoch: number
  from_sequence: number
  to_sequence: number
  start_sample: number
  end_sample: number
  reason: 'device_storage_lost' | 'file_corrupt' | 'system_interruption' | 'upload_unrecoverable'
  manifest_revision: number
}

function nativeCapturePath(meetingId: string, captureId?: string, suffix = '') {
  const base = `${MEETING_API_BASE}/sessions/${encodeURIComponent(meetingId)}/native-captures`
  return captureId ? `${base}/${encodeURIComponent(captureId)}${suffix}` : base
}

function mutationKey(value?: string) {
  return value || globalThis.crypto?.randomUUID?.() || `native-capture-${Date.now()}-${Math.random()}`
}

function captureHeaders(captureToken: string, deviceInstallationId: string, headers: HeadersInit = {}) {
  const token = captureToken.trim()
  const installationId = deviceInstallationId.trim()
  if (!token) throw new Error('原生采集 token 不能为空。')
  if (installationId.length < 16) throw new Error('设备安装标识无效。')
  return new Headers({
    ...Object.fromEntries(new Headers(headers)),
    Authorization: `Bearer ${token}`,
    'X-SIQ-Device-Installation-Id': installationId,
  })
}

export function createNativeCapture(
  meetingId: string,
  payload: NativeCaptureCreateRequest,
  idempotencyKey?: string,
) {
  return apiJson<NativeCaptureCreateResponse>(nativeCapturePath(meetingId), {
    method: 'POST',
    headers: { 'Idempotency-Key': mutationKey(idempotencyKey) },
    body: payload,
  })
}

export function renewNativeCaptureToken(meetingId: string, captureId: string) {
  return apiJson<NativeCaptureTokenResponse>(nativeCapturePath(meetingId, captureId, '/token'), {
    method: 'POST',
    body: {},
  })
}

export function revokeNativeCaptureToken(meetingId: string, captureId: string) {
  return apiJson<{ schema_version: string; capture_id: string; revoked: number }>(
    nativeCapturePath(meetingId, captureId, '/token/revoke'),
    { method: 'POST', body: {} },
  )
}

export function getNativeCaptureCheckpoint(
  meetingId: string,
  captureId: string,
  captureToken: string,
  deviceInstallationId: string,
) {
  return apiJson<NativeCaptureCheckpointResponse>(nativeCapturePath(meetingId, captureId, '/checkpoint'), {
    headers: captureHeaders(captureToken, deviceInstallationId),
  })
}

export function putNativeCaptureBatch(
  meetingId: string,
  captureId: string,
  captureToken: string,
  deviceInstallationId: string,
  metadata: NativeCaptureBatchMetadata,
  audio: ArrayBuffer | ArrayBufferView | Blob,
) {
  const headers = captureHeaders(captureToken, deviceInstallationId, {
    'Content-Type': 'application/octet-stream',
    'Idempotency-Key': mutationKey(metadata.idempotency_key),
    'X-SIQ-First-Sample': String(metadata.first_sample),
    'X-SIQ-Sample-Count': String(metadata.sample_count),
    'X-SIQ-Captured-Monotonic-Ns': String(metadata.captured_monotonic_ns),
    'X-SIQ-Audio-Encoding': metadata.encoding,
    'X-SIQ-Sample-Rate': String(metadata.sample_rate),
    'X-SIQ-Channels': String(metadata.channels),
    'X-SIQ-SHA256': metadata.sha256.toLowerCase(),
    'X-SIQ-Manifest-Revision': String(metadata.manifest_revision),
  })
  return apiJson<NativeCaptureBatchResponse>(
    nativeCapturePath(
      meetingId,
      captureId,
      `/batches/${encodeURIComponent(String(metadata.stream_epoch))}/${encodeURIComponent(String(metadata.sequence))}`,
    ),
    { method: 'PUT', headers, body: audio },
  )
}

export function sealNativeCapture(
  meetingId: string,
  captureId: string,
  captureToken: string,
  deviceInstallationId: string,
  payload: NativeCaptureBoundaryRequest,
) {
  return apiJson<NativeCaptureSealResponse>(nativeCapturePath(meetingId, captureId, '/seal'), {
    method: 'POST',
    headers: captureHeaders(captureToken, deviceInstallationId),
    body: payload,
  })
}

export function rolloverNativeCapture(
  meetingId: string,
  captureId: string,
  payload: NativeCaptureBoundaryRequest,
  idempotencyKey?: string,
) {
  return apiJson<NativeCaptureRolloverResponse>(nativeCapturePath(meetingId, captureId, '/rollover'), {
    method: 'POST',
    headers: { 'Idempotency-Key': mutationKey(idempotencyKey) },
    body: payload,
  })
}

export function recordNativeCaptureGap(
  meetingId: string,
  captureId: string,
  payload: NativeCaptureGapRequest,
  idempotencyKey?: string,
) {
  return apiJson<{ checkpoint: NativeCaptureCheckpointResponse; gap_id: string; replayed: boolean }>(
    nativeCapturePath(meetingId, captureId, '/gaps'),
    {
      method: 'POST',
      headers: { 'Idempotency-Key': mutationKey(idempotencyKey) },
      body: payload,
    },
  )
}
