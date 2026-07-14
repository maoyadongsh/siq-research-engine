import type { MeetingModelSelection } from './types'

export type MeetingImportState =
  | 'uploading'
  | 'queued'
  | 'processing'
  | 'postprocess_queued'
  | 'ready'
  | 'retry_wait'
  | 'failed'
  | 'cancelled'

export type MeetingImportStep =
  | 'uploading'
  | 'verifying'
  | 'probing'
  | 'transcoding'
  | 'persisting'
  | 'finalizing'
  | 'reclustering'
  | 'minutes'
  | 'ready'
  | 'failed'
  | 'cancelled'

export interface MeetingImportCreateInput {
  filename: string
  media_type: string | null
  file_size: number
  chunk_size: number
  title: string
  language: string
  voiceprint_enabled: boolean
  ai_enabled: boolean
  model_selection: MeetingModelSelection
}
export interface MeetingImportStatus {
  schema_version: string
  id: string
  meeting_id: string | null
  filename: string
  media_type: string | null
  expected_size: number
  received_size: number
  chunk_size: number
  total_chunks: number
  received_chunks: number
  next_ordinal: number
  upload_progress: number
  state: MeetingImportState
  ingest_state: MeetingImportState
  step: MeetingImportStep
  detected_duration_ms: number | null
  public_error_code: string | null
  retryable: boolean
  can_resume: boolean
  can_cancel: boolean
  created_at: string
  updated_at: string
}

export interface MeetingImportChunkResult {
  upload_id: string
  ordinal: number
  byte_offset: number
  byte_size: number
  sha256: string
  received_size: number
  received_chunks: number
  next_ordinal: number
  replayed: boolean
}

export interface MeetingRecordingImportCapability {
  available: boolean
  configuration_errors?: string[]
  formats: string[]
  resumable: boolean
  max_file_bytes: number
  max_duration_seconds: number
  min_chunk_bytes: number
  max_chunk_bytes: number
}
