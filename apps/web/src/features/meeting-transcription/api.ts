import { apiJson } from '@/shared/api/client'

import type {
  CreateMeetingRequest,
  MeetingArtifact,
  MeetingAudioTicket,
  MeetingCapabilities,
  MeetingEvent,
  MeetingExport,
  MeetingExportContent,
  MeetingExportFormat,
  MeetingExportTranscriptSource,
  MeetingJob,
  MeetingLexicon,
  MeetingLexiconEntry,
  MeetingLexiconVersion,
  MeetingListResponse,
  MeetingModel,
  MeetingModelListResponse,
  MeetingModelSetting,
  MeetingSession,
  MeetingSpeakerTrack,
  MeetingStreamTicket,
  MeetingTermCandidate,
  MeetingTranscriptResponse,
  MeetingVoiceProfile,
  MeetingVoiceprintEnrollmentRequest,
  SegmentCorrectionRequest,
} from './types'

export const MEETING_API_BASE = '/api/meetings/v1'

function meetingPath(meetingId: string, suffix = '') {
  return `${MEETING_API_BASE}/sessions/${encodeURIComponent(meetingId)}${suffix}`
}

function mutationHeaders(idempotencyKey?: string) {
  const key = idempotencyKey || globalThis.crypto?.randomUUID?.() || `meeting-${Date.now()}-${Math.random()}`
  return { 'Idempotency-Key': key }
}

function queryString(values: Record<string, string | number | boolean | null | undefined>) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const serialized = params.toString()
  return serialized ? `?${serialized}` : ''
}

function normalizeItems<T>(payload: T[] | { items?: T[] }) {
  return Array.isArray(payload) ? payload : (payload.items || [])
}

export function getMeetingCapabilities(signal?: AbortSignal) {
  return apiJson<MeetingCapabilities>(`${MEETING_API_BASE}/capabilities`, { signal }).then((payload) => ({
    ...payload,
    audio: payload.audio ? { ...payload.audio, encoding: payload.audio.encoding || payload.audio.codec } : payload.audio,
    audio_sources: payload.audio_sources || Object.fromEntries((payload.supported_audio_sources || []).map((source) => [source, true])),
    max_meeting_duration_seconds: payload.max_meeting_duration_seconds || payload.limits?.max_duration_seconds,
    reconnect_window_seconds: payload.reconnect_window_seconds || payload.limits?.reconnect_window_seconds,
  }))
}

export async function getMeetingModels(signal?: AbortSignal) {
  const payload = await apiJson<MeetingModel[] | MeetingModelListResponse>(
    `${MEETING_API_BASE}/models?purpose=meeting_postprocess`,
    { signal },
  )
  return normalizeItems<MeetingModel>(payload)
}

export function listMeetings(
  options: { q?: string; state?: string; sort?: string; offset?: number; limit?: number } = {},
  signal?: AbortSignal,
) {
  return apiJson<MeetingListResponse>(
    `${MEETING_API_BASE}/sessions${queryString({
      q: options.q?.trim(),
      state: options.state,
      sort: options.sort,
      offset: options.offset ?? 0,
      limit: options.limit ?? 20,
    })}`,
    { signal },
  )
}

export function createMeeting(payload: CreateMeetingRequest, idempotencyKey?: string) {
  return apiJson<MeetingSession>(`${MEETING_API_BASE}/sessions`, {
    method: 'POST',
    headers: mutationHeaders(idempotencyKey),
    body: payload,
  })
}

export function getMeeting(meetingId: string, signal?: AbortSignal) {
  return apiJson<MeetingSession>(meetingPath(meetingId), { signal })
}

export function updateMeeting(
  meetingId: string,
  payload: { title?: string; expected_version: number },
  idempotencyKey?: string,
) {
  return apiJson<MeetingSession>(meetingPath(meetingId), {
    method: 'PATCH',
    headers: mutationHeaders(idempotencyKey),
    body: payload,
  })
}

async function sessionAction(meetingId: string, action: string, idempotencyKey?: string) {
  const payload = await apiJson<{ session: MeetingSession }>(meetingPath(meetingId, `/${action}`), {
    method: 'POST',
    headers: mutationHeaders(idempotencyKey),
    body: {},
  })
  return payload.session
}

export function startMeeting(meetingId: string, idempotencyKey?: string) {
  return sessionAction(meetingId, 'start', idempotencyKey)
}

export function pauseMeeting(meetingId: string, _expectedVersion?: number, idempotencyKey?: string) {
  return sessionAction(meetingId, 'pause', idempotencyKey)
}

export function resumeMeeting(meetingId: string, _expectedVersion?: number, idempotencyKey?: string) {
  return sessionAction(meetingId, 'resume', idempotencyKey)
}

export function stopMeeting(meetingId: string, _expectedVersion?: number, idempotencyKey?: string) {
  return sessionAction(meetingId, 'stop', idempotencyKey)
}

export async function finalizeMeeting(meetingId: string, idempotencyKey?: string) {
  const payload = await apiJson<{ session: MeetingSession }>(meetingPath(meetingId, '/finalize'), {
    method: 'POST',
    headers: mutationHeaders(idempotencyKey),
    body: {},
  })
  return payload.session
}

export function deleteMeeting(meetingId: string, idempotencyKey?: string) {
  return apiJson<Record<string, unknown>>(meetingPath(meetingId), {
    method: 'DELETE',
    headers: mutationHeaders(idempotencyKey),
  })
}

export function createMeetingStreamTicket(meetingId: string, idempotencyKey?: string) {
  return apiJson<MeetingStreamTicket>(meetingPath(meetingId, '/stream-ticket'), {
    method: 'POST',
    headers: mutationHeaders(idempotencyKey),
    body: {},
  })
}

export function createMeetingAudioTicket(meetingId: string, idempotencyKey?: string) {
  return apiJson<MeetingAudioTicket>(meetingPath(meetingId, '/audio-ticket'), {
    method: 'POST',
    headers: mutationHeaders(idempotencyKey),
    body: {},
  })
}

export async function listMeetingEvents(meetingId: string, afterCursor = 0, signal?: AbortSignal) {
  const payload = await apiJson<{ items: Array<MeetingEvent & { event_type?: string; created_at?: string }>; next_cursor?: number | null }>(
    meetingPath(meetingId, `/events${queryString({ after_cursor: afterCursor })}`),
    { signal },
  )
  return {
    ...payload,
    items: payload.items.map((event) => ({
      ...event,
      type: event.type || event.event_type || '',
      emitted_at: event.emitted_at || event.created_at || '',
    })),
  }
}

function normalizeTranscriptSegment(value: Record<string, unknown>): import('./types').MeetingTranscriptSegment {
  const displayLayer = String(value.display_layer || '')
  return {
    ...(value as unknown as import('./types').MeetingTranscriptSegment),
    speaker_display_name: typeof value.speaker_label === 'string' ? value.speaker_label : null,
    revision_no: Number(value.current_revision_no ?? value.revision_no ?? 0),
    text_state: value.human_locked ? 'human_verified' : ['llm_correction', 'llm_corrected'].includes(displayLayer) ? 'optimized' : 'stable',
    text: typeof value.display_text === 'string' ? value.display_text : undefined,
  }
}

export async function getMeetingTranscript(
  meetingId: string,
  options: { afterOrdinal?: number; limit?: number } = {},
  signal?: AbortSignal,
) {
  const payload = await apiJson<MeetingTranscriptResponse & { items: Array<Record<string, unknown>> }>(
    meetingPath(meetingId, `/transcript${queryString({ after_ordinal: options.afterOrdinal, limit: options.limit ?? 500 })}`),
    { signal },
  )
  return { ...payload, items: payload.items.map(normalizeTranscriptSegment) }
}

export async function getMeetingSpeakers(meetingId: string, signal?: AbortSignal) {
  const payload = await apiJson<Array<MeetingSpeakerTrack & { resolved_label?: string }> | { items?: Array<MeetingSpeakerTrack & { resolved_label?: string }> }>(
    meetingPath(meetingId, '/speakers'),
    { signal },
  )
  return normalizeItems(payload).map((speaker) => ({
    ...speaker,
    display_name: speaker.display_name || speaker.resolved_label || speaker.anonymous_label,
  }))
}

export function renameMeetingSpeaker(
  meetingId: string,
  trackId: string,
  displayName: string,
  expectedVersion: number,
  idempotencyKey?: string,
) {
  return apiJson<MeetingSpeakerTrack>(meetingPath(meetingId, `/speakers/${encodeURIComponent(trackId)}`), {
    method: 'PATCH',
    headers: mutationHeaders(idempotencyKey),
    body: { display_name: displayName, expected_version: expectedVersion },
  })
}

export function enrollMeetingVoiceprint(
  meetingId: string,
  trackId: string,
  payload: MeetingVoiceprintEnrollmentRequest,
  idempotencyKey?: string,
) {
  return apiJson<{ voice_profile: MeetingVoiceProfile }>(
    meetingPath(meetingId, `/speakers/${encodeURIComponent(trackId)}/voiceprint-enrollment`),
    {
      method: 'POST',
      headers: mutationHeaders(idempotencyKey),
      body: payload,
    },
  ).then((response) => response.voice_profile)
}

export function decideVoiceprintMatch(
  meetingId: string,
  matchId: string,
  decision: 'confirm' | 'reject' | 'undo',
  idempotencyKey?: string,
) {
  return apiJson<Record<string, unknown>>(
    meetingPath(meetingId, `/voiceprint-matches/${encodeURIComponent(matchId)}/decision`),
    {
      method: 'POST',
      headers: mutationHeaders(idempotencyKey),
      body: { decision: { confirm: 'confirmed', reject: 'rejected', undo: 'undone' }[decision] },
    },
  )
}

export async function correctMeetingSegment(
  meetingId: string,
  segmentId: string,
  payload: SegmentCorrectionRequest,
  idempotencyKey?: string,
) {
  const response = await apiJson<{ segment: Record<string, unknown> }>(
    meetingPath(meetingId, `/segments/${encodeURIComponent(segmentId)}`),
    {
      method: 'PATCH',
      headers: mutationHeaders(idempotencyKey),
      body: payload,
    },
  )
  return normalizeTranscriptSegment(response.segment)
}

export function revertMeetingSegment(
  meetingId: string,
  segmentId: string,
  revisionNo: number,
  expectedRevision: number,
  idempotencyKey?: string,
) {
  return apiJson<Record<string, unknown>>(
    meetingPath(meetingId, `/segments/${encodeURIComponent(segmentId)}/revert`),
    {
      method: 'POST',
      headers: mutationHeaders(idempotencyKey),
      body: { revision_no: revisionNo, expected_revision: expectedRevision },
    },
  ).then(normalizeTranscriptSegment)
}

export async function getMeetingArtifacts(meetingId: string, signal?: AbortSignal) {
  const payload = await apiJson<MeetingArtifact[] | { items?: MeetingArtifact[] }>(meetingPath(meetingId, '/artifacts'), { signal })
  return normalizeItems(payload)
}

export async function regenerateMeetingArtifact(
  meetingId: string,
  artifactId: string,
  expectedSettingsVersion: number,
) {
  const payload = await apiJson<{
    artifact: MeetingArtifact
    job: MeetingJob & { job_kind?: string; public_error_code?: string | null; attempt?: number; max_attempts?: number }
  }>(meetingPath(meetingId, `/artifacts/${encodeURIComponent(artifactId)}/regenerate`), {
    method: 'POST',
    body: { expected_settings_version: expectedSettingsVersion },
  })
  return {
    artifact: payload.artifact,
    job: {
      ...payload.job,
      job_type: payload.job.job_type || payload.job.job_kind || 'meeting_job',
      error_code: payload.job.error_code || payload.job.public_error_code || null,
      retryable: payload.job.state === 'failed' && (payload.job.attempt || 0) < (payload.job.max_attempts || 1),
    },
  }
}

export async function getMeetingJobs(meetingId: string, signal?: AbortSignal) {
  const payload = await apiJson<Array<MeetingJob & { job_kind?: string; public_error_code?: string | null; attempt?: number; max_attempts?: number }> | { items?: Array<MeetingJob & { job_kind?: string; public_error_code?: string | null; attempt?: number; max_attempts?: number }> }>(meetingPath(meetingId, '/jobs'), { signal })
  return normalizeItems(payload).map((job) => ({
    ...job,
    job_type: job.job_type || job.job_kind || 'meeting_job',
    error_code: job.error_code || job.public_error_code || null,
    retryable: job.state === 'failed' && (job.attempt || 0) < (job.max_attempts || 1),
  }))
}

export function retryMeetingJob(meetingId: string, jobId: string, idempotencyKey?: string) {
  return apiJson<MeetingJob & { job_kind?: string; public_error_code?: string | null }>(meetingPath(meetingId, `/jobs/${encodeURIComponent(jobId)}/retry`), {
    method: 'POST',
    headers: mutationHeaders(idempotencyKey),
    body: {},
  }).then((job) => ({ ...job, job_type: job.job_type || job.job_kind || 'meeting_job', error_code: job.error_code || job.public_error_code || null }))
}

export function updateMeetingModelSelection(
  meetingId: string,
  payload: {
    mode: 'none' | 'auto' | 'pinned'
    model_ref: string | null
    fallback_policy: string
    expected_settings_version: number
    cloud_data_boundary_confirmed?: boolean
  },
  idempotencyKey?: string,
) {
  return apiJson<MeetingModelSetting>(meetingPath(meetingId, '/model-selection'), {
    method: 'PUT',
    headers: mutationHeaders(idempotencyKey),
    body: {
      expected_settings_version: payload.expected_settings_version,
      selection: {
        mode: payload.mode,
        model_ref: payload.model_ref,
        fallback_policy: payload.fallback_policy,
        cloud_data_boundary_confirmed: payload.cloud_data_boundary_confirmed || false,
      },
    },
  })
}

export async function listTermCandidates(status = '', signal?: AbortSignal) {
  const payload = await apiJson<MeetingTermCandidate[] | { items?: MeetingTermCandidate[] }>(
    `${MEETING_API_BASE}/term-candidates${queryString({ status })}`,
    { signal },
  )
  return normalizeItems(payload)
}

function termCandidateAction(candidateId: string, action: 'confirm' | 'reject', idempotencyKey?: string) {
  return apiJson<MeetingTermCandidate>(
    `${MEETING_API_BASE}/term-candidates/${encodeURIComponent(candidateId)}/${action}`,
    { method: 'POST', headers: mutationHeaders(idempotencyKey), body: {} },
  )
}

export async function confirmTermCandidate(candidateId: string, idempotencyKey?: string) {
  const payload = await apiJson<{ candidate: MeetingTermCandidate }>(
    `${MEETING_API_BASE}/term-candidates/${encodeURIComponent(candidateId)}/confirm`,
    { method: 'POST', headers: mutationHeaders(idempotencyKey), body: {} },
  )
  return payload.candidate
}

export function rejectTermCandidate(candidateId: string, idempotencyKey?: string) {
  return termCandidateAction(candidateId, 'reject', idempotencyKey)
}

export function getMeetingLexicon(meetingId?: string, signal?: AbortSignal) {
  return apiJson<MeetingLexicon & { active_version?: MeetingLexiconVersion | null }>(
    `${MEETING_API_BASE}/lexicon${queryString({ meeting_id: meetingId })}`,
    { signal },
  ).then((payload) => ({
    ...payload,
    version: payload.active_version?.version || payload.version || 0,
    activated_at: payload.active_version?.created_at || payload.activated_at || null,
  }))
}

export function createLexiconEntry(
  payload: Pick<MeetingLexiconEntry, 'canonical_term' | 'language' | 'weight' | 'scope'> & { meeting_id?: string; misrecognitions?: string[] },
  idempotencyKey?: string,
) {
  return apiJson<{ entry: MeetingLexiconEntry }>(`${MEETING_API_BASE}/lexicon`, {
    method: 'POST',
    headers: mutationHeaders(idempotencyKey),
    body: payload,
  }).then((response) => response.entry)
}

export function updateLexiconEntry(
  entryId: string,
  payload: Partial<Pick<MeetingLexiconEntry, 'canonical_term' | 'weight' | 'scope' | 'status' | 'misrecognitions'>>,
  idempotencyKey?: string,
) {
  const supported = {
    weight: payload.weight,
    scope: payload.scope,
    status: payload.status,
  }
  return apiJson<{ entry: MeetingLexiconEntry }>(`${MEETING_API_BASE}/lexicon/${encodeURIComponent(entryId)}`, {
    method: 'PATCH',
    headers: mutationHeaders(idempotencyKey),
    body: supported,
  }).then((response) => response.entry)
}

export function deleteLexiconEntry(entryId: string, idempotencyKey?: string) {
  return apiJson<Record<string, unknown>>(`${MEETING_API_BASE}/lexicon/${encodeURIComponent(entryId)}`, {
    method: 'DELETE',
    headers: mutationHeaders(idempotencyKey),
  })
}

export async function listLexiconVersions(meetingId?: string, signal?: AbortSignal) {
  const payload = await apiJson<Array<MeetingLexiconVersion & { is_active?: boolean }> | { items?: Array<MeetingLexiconVersion & { is_active?: boolean }> }>(
    `${MEETING_API_BASE}/lexicon/versions${queryString({ meeting_id: meetingId })}`,
    { signal },
  )
  return normalizeItems(payload).map((version) => ({ ...version, active: version.active ?? version.is_active ?? false }))
}

export function activateLexiconVersion(version: number, idempotencyKey?: string, meetingId?: string) {
  return apiJson<MeetingLexiconVersion>(`${MEETING_API_BASE}/lexicon/versions/${version}/activate${queryString({ meeting_id: meetingId })}`, {
    method: 'POST',
    headers: mutationHeaders(idempotencyKey),
    body: {},
  })
}

export async function listVoiceprints(signal?: AbortSignal) {
  const payload = await apiJson<MeetingVoiceProfile[] | { items?: MeetingVoiceProfile[] }>(
    `${MEETING_API_BASE}/voiceprints`,
    { signal },
  )
  return normalizeItems(payload)
}

export function createVoiceprint(displayName: string, idempotencyKey?: string) {
  return apiJson<MeetingVoiceProfile>(`${MEETING_API_BASE}/voiceprints`, {
    method: 'POST',
    headers: mutationHeaders(idempotencyKey),
    body: { display_name: displayName },
  })
}

function voiceprintAction(profileId: string, action: string, body: object = {}, idempotencyKey?: string) {
  return apiJson<MeetingVoiceProfile>(
    `${MEETING_API_BASE}/voiceprints/${encodeURIComponent(profileId)}/${action}`,
    { method: 'POST', headers: mutationHeaders(idempotencyKey), body },
  )
}

export function pauseVoiceprint(profileId: string, idempotencyKey?: string) {
  return voiceprintAction(profileId, 'pause', {}, idempotencyKey)
}

export function resumeVoiceprint(profileId: string, idempotencyKey?: string) {
  return voiceprintAction(profileId, 'resume', {}, idempotencyKey)
}

export function reEnrollVoiceprint(profileId: string, idempotencyKey?: string) {
  return voiceprintAction(profileId, 're-enroll', {}, idempotencyKey)
}

export function revokeVoiceprintConsent(profileId: string, idempotencyKey?: string) {
  return voiceprintAction(profileId, 'consent/revoke', {}, idempotencyKey)
}

export function deleteVoiceprint(profileId: string, idempotencyKey?: string) {
  return apiJson<Record<string, unknown>>(`${MEETING_API_BASE}/voiceprints/${encodeURIComponent(profileId)}`, {
    method: 'DELETE',
    headers: mutationHeaders(idempotencyKey),
  })
}

export function createMeetingExport(
  meetingId: string,
  payload: {
    format: MeetingExportFormat
    content?: MeetingExportContent
    transcript_source: MeetingExportTranscriptSource
    artifact_id?: string | null
    artifact_version?: number | null
  },
  idempotencyKey?: string,
) {
  return apiJson<MeetingExport>(meetingPath(meetingId, '/exports'), {
    method: 'POST',
    headers: mutationHeaders(idempotencyKey),
    body: payload,
  })
}

export async function listMeetingExports(meetingId: string, signal?: AbortSignal) {
  const payload = await apiJson<MeetingExport[] | { items?: MeetingExport[] }>(
    meetingPath(meetingId, '/exports'),
    { signal },
  )
  return normalizeItems(payload)
}

export function getMeetingExport(meetingId: string, exportId: string, signal?: AbortSignal) {
  return apiJson<MeetingExport>(meetingPath(meetingId, `/exports/${encodeURIComponent(exportId)}`), { signal })
}

export function createMeetingExportTicket(meetingId: string, exportId: string) {
  return apiJson<{ download_url: string; expires_at: string }>(
    meetingPath(meetingId, `/exports/${encodeURIComponent(exportId)}/ticket`),
    { method: 'POST', body: {} },
  )
}

export function meetingAudioUrl(meetingId: string) {
  return meetingPath(meetingId, '/audio')
}

export function buildMeetingWebSocketUrl(meetingId: string, ticket: MeetingStreamTicket) {
  const supplied = ticket.websocket_url || ticket.ws_url
  const path = supplied || `${meetingPath(meetingId, '/audio')}?ticket=${encodeURIComponent(ticket.ticket)}`
  if (/^wss?:\/\//i.test(path)) {
    const url = new URL(path)
    if (!url.searchParams.has('ticket')) url.searchParams.set('ticket', ticket.ticket)
    return url.toString()
  }
  if (typeof window === 'undefined') {
    if (/[?&]ticket=/.test(path)) return path
    return `${path}${path.includes('?') ? '&' : '?'}ticket=${encodeURIComponent(ticket.ticket)}`
  }
  const url = new URL(path, window.location.origin)
  url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  if (!url.searchParams.has('ticket')) url.searchParams.set('ticket', ticket.ticket)
  return url.toString()
}
