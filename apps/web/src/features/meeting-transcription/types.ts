export type MeetingSessionState =
  | 'draft'
  | 'connecting'
  | 'live'
  | 'paused'
  | 'reconnecting'
  | 'stopping'
  | 'stopped'
  | 'archived'
  | 'interrupted'
  | 'deleted'

export type MeetingTranscriptState = 'partial' | 'stable' | 'optimized' | 'human_verified' | 'review_required'
export type MeetingModelLocality = 'local' | 'cloud' | 'hybrid' | 'unknown'
export type MeetingEditIntent = 'asr_error' | 'content_edit'
export type MeetingExportFormat = 'markdown' | 'txt' | 'srt' | 'vtt' | 'json' | 'docx' | 'pdf'
export type MeetingExportContent = 'transcript' | 'minutes'
export type MeetingExportTranscriptSource = 'display' | 'asr'

export interface MeetingSession {
  id: string
  owner_user_id: string | number
  owner_display_name?: string | null
  title: string
  language: string
  state: MeetingSessionState
  postprocess_state: string
  audio_source: string
  voiceprint_enabled: boolean
  ai_enabled: boolean
  selection_mode: 'none' | 'auto' | 'pinned'
  requested_model_ref?: string | null
  fallback_policy: string
  settings_version: number
  version: number
  stream_epoch: number
  last_audio_sequence: number
  last_segment_ordinal: number
  active_lexicon_version?: number | null
  started_at?: string | null
  stopped_at?: string | null
  created_at: string
  updated_at: string
  duration_ms?: number | null
  speaker_count?: number | null
  participant_count?: number | null
  transcript_state?: string | null
  final_minutes_state?: string | null
  model_label?: string | null
  model_locality?: MeetingModelLocality | null
}

export interface MeetingListResponse {
  items: MeetingSession[]
  total: number
  offset: number
  limit: number
}

export interface MeetingCapabilityStatus {
  available: boolean
  reason_code?: string | null
}

export interface MeetingCapabilities {
  schema_version?: string
  enabled: boolean
  configuration_errors?: string[]
  audio?: {
    codec?: string
    encoding?: string
    sample_rate?: number
    channels?: number
    frame_transport?: string
    chunk_ms?: number
    chunk_min_ms?: number
    chunk_max_ms?: number
  }
  asr?: MeetingCapabilityStatus & {
    languages?: string[]
    timestamps?: boolean
    speaker_tracks?: boolean
    speaker_diarization?: boolean
  }
  correction_learning?: MeetingCapabilityStatus & {
    scope?: string
  }
  voiceprint?: MeetingCapabilityStatus
  ai?: MeetingCapabilityStatus
  recording_import?: import('./meetingImportTypes').MeetingRecordingImportCapability
  audio_sources?: Record<string, boolean>
  supported_audio_sources?: string[]
  limits?: {
    max_duration_seconds?: number
    max_chunk_bytes?: number
    reconnect_window_seconds?: number
    max_active_per_user?: number
    max_active_total?: number
    audio_max_frames_per_second?: number
    audio_max_bytes_per_second?: number
    audio_rate_burst_seconds?: number
  }
  max_meeting_duration_seconds?: number
  reconnect_window_seconds?: number
}

export interface MeetingModel {
  model_ref: string
  label: string
  provider_label: string
  locality: MeetingModelLocality
  configured: boolean
  available: boolean
  is_default?: boolean
  capabilities: string[]
  context_window?: number | null
  data_boundary: string
  reason_code?: string | null
  checked_at?: string | null
}

export interface MeetingModelListResponse {
  items: MeetingModel[]
}

export interface MeetingModelSelection {
  mode: 'none' | 'auto' | 'pinned'
  model_ref: string | null
  fallback_policy: string
  cloud_data_boundary_confirmed?: boolean
}

export interface CreateMeetingRequest {
  title: string
  language: string
  audio_source: string
  voiceprint_enabled: boolean
  ai_enabled: boolean
  model_selection: MeetingModelSelection
}

export interface MeetingStreamTicket {
  ticket: string
  websocket_url?: string | null
  ws_url?: string | null
  expires_at?: string | null
  stream_epoch: number
  last_acked_sequence?: number
  capture_offset_ms?: number
  reconnect_window_seconds?: number
}

export interface MeetingAudioTicket {
  ticket: string
  expires_at: string
  audio_url: string
  purpose: 'meeting_audio_playback'
}

export interface MeetingSpeakerTrack {
  id: string
  meeting_id: string
  track_key?: string
  anonymous_label: string
  display_name: string | null
  label_source: 'anonymous' | 'manual' | 'voiceprint_confirmed' | 'voiceprint_auto'
  voice_profile_id?: string | null
  match_confidence?: number | null
  version: number
  sample_duration_ms?: number | null
  voiceprint_match?: MeetingVoiceprintMatch | null
  created_at?: string | null
  updated_at?: string | null
}

export interface MeetingVoiceprintMatch {
  id: string
  voice_profile_id: string
  display_name?: string | null
  confidence?: number | null
  decision: 'suggested' | 'auto_applied' | 'confirmed' | 'rejected' | 'undone'
}

export interface MeetingDiffOperation {
  op: 'equal' | 'insert' | 'delete' | 'replace'
  old_text?: string
  new_text?: string
  text?: string
}

export interface MeetingTranscriptSegment {
  id: string
  meeting_id: string
  ordinal: number
  utterance_id: string
  start_ms: number
  end_ms: number
  speaker_track_id?: string | null
  speaker_display_name?: string | null
  raw_text: string
  asr_final_text: string
  normalized_text?: string | null
  display_text?: string | null
  text?: string | null
  revision_no: number
  text_state: MeetingTranscriptState
  human_locked: boolean
  asr_confidence?: number | null
  overlap?: boolean
  diff_ops?: MeetingDiffOperation[] | null
  updated_at?: string | null
}

export interface MeetingTranscriptResponse {
  items: MeetingTranscriptSegment[]
  next_ordinal?: number | null
}

export type MeetingSpeakerRenameScope = 'segment' | 'speaker'

export interface SegmentSpeakerRenameResponse {
  operation: 'rename_segment' | 'rename_speaker'
  scope: MeetingSpeakerRenameScope
  segment: MeetingTranscriptSegment
  tracks: MeetingSpeakerTrack[]
  affected_segment_count: number
  event_id: string
  event_cursor: number
}

export interface MeetingSpeakerMappingResponse {
  operation: 'merge' | 'split'
  meeting_id: string
  source_track_ids: string[]
  target_track_ids: string[]
  segment_ids: string[]
  event_id: string
  event_cursor: number
  tracks: MeetingSpeakerTrack[]
}

export interface MeetingPartialTranscript {
  utterance_id: string
  text: string
  start_ms?: number
  speaker_track_id?: string | null
}

export interface MeetingEvent<TPayload = Record<string, unknown>> {
  schema_version: 'siq.meeting.event.v1' | string
  event_id: string
  meeting_id: string
  type: string
  cursor: number | null
  emitted_at: string
  trace_id?: string | null
  payload: TPayload
}

export interface MeetingEventsResponse {
  items: MeetingEvent[]
  next_cursor?: number | null
}

export interface MeetingArtifact {
  id: string
  meeting_id: string
  artifact_type: 'rolling_minutes' | 'final_minutes' | 'viewpoints' | 'decisions' | 'action_items' | 'chapters' | string
  version: number
  state: 'generating' | 'ready' | 'failed' | 'stale' | string
  content_text?: string | null
  content_json?: Record<string, unknown> | unknown[] | null
  transcript_revision?: number | null
  requested_model_label?: string | null
  actual_model_label?: string | null
  generated_at?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface MeetingJob {
  id: string
  job_type: string
  state: 'queued' | 'running' | 'succeeded' | 'failed' | string
  progress?: number | null
  error_code?: string | null
  retryable?: boolean
  updated_at?: string | null
}

export interface MeetingModelSetting {
  meeting_id: string
  settings_version: number
  selection_mode: 'none' | 'auto' | 'pinned'
  requested_model_ref?: string | null
  fallback_policy: string
  effective_after_segment_ordinal: number
  cloud_data_boundary_confirmed_at?: string | null
  created_at?: string | null
}

export interface SegmentCorrectionRequest {
  text: string
  expected_revision: number
  edit_intent: MeetingEditIntent
  contribute_to_accuracy: boolean
  candidate_terms: Array<{
    canonical_term: string
    misrecognition?: string | null
    promote_now: boolean
  }>
}

export interface MeetingTermCandidate {
  id: string
  canonical_term: string
  misrecognition?: string | null
  language: string
  candidate_type: string
  source_count: number
  distinct_meeting_count: number
  confidence?: number | null
  status: 'pending' | 'confirmed' | 'rejected' | 'promoted' | 'deprecated'
  created_at?: string | null
  updated_at?: string | null
}

export interface MeetingLexiconEntry {
  id: string
  canonical_term: string
  aliases?: string[]
  misrecognitions?: string[]
  language: string
  entry_type: string
  weight: number
  scope: 'current_meeting' | 'user_future_meetings'
  meeting_id?: string | null
  status: 'active' | 'paused' | 'deprecated' | 'deleted'
  source_candidate_id?: string | null
  hit_count?: number
  false_positive_count?: number
  created_at?: string | null
  updated_at?: string | null
}

export interface MeetingLexicon {
  entries: MeetingLexiconEntry[]
  version: number
  language: string
  activated_at?: string | null
}

export interface MeetingLexiconVersion {
  version: number
  meeting_id?: string | null
  language: string
  entry_count: number
  change_reason?: string | null
  created_at: string
  active?: boolean
}

export interface MeetingVoiceProfile {
  id: string
  display_name: string
  scope: 'user_private' | string
  status: 'collecting' | 'active' | 'paused' | 'revoked' | 'deleted'
  encoder_name?: string | null
  encoder_version?: string | null
  sample_count: number
  effective_duration_ms: number
  quality_summary?: string | Record<string, unknown> | null
  consent?: {
    purpose?: string
    policy_version?: string
    granted_at?: string | null
    revoked_at?: string | null
  } | null
  consent_active?: boolean
  last_matched_at?: string | null
  created_at: string
  updated_at?: string | null
}

export interface MeetingVoiceprintEnrollmentRequest {
  consent_accepted: true
  policy_version: string
  voice_profile_id: string
  source_track_id: string
}

export interface MeetingExport {
  id: string
  meeting_id?: string
  format: MeetingExportFormat
  content?: MeetingExportContent
  transcript_source?: MeetingExportTranscriptSource
  state: 'queued' | 'running' | 'ready' | 'failed' | string
  job_id?: string
  artifact_id?: string | null
  artifact_version?: number | null
  filename?: string | null
  media_type?: string | null
  byte_size?: number | null
  sha256?: string | null
  download_url?: string | null
  download_expires_at?: string | null
  error_code?: string | null
  created_at?: string | null
  updated_at?: string | null
}
