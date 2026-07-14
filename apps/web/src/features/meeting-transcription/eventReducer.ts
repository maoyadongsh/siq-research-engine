import { segmentDisplayText } from './formatters'
import { mergeTranscriptSegments } from './transcriptPagination'
import type {
  MeetingArtifact,
  MeetingEvent,
  MeetingPartialTranscript,
  MeetingSessionState,
  MeetingSpeakerTrack,
  MeetingTranscriptSegment,
} from './types'

const LATE_CURSOR_SAFE_EVENTS = new Set([
  'minutes.rolling.updated',
  'minutes.final.ready',
  'transcript.segment.corrected',
])

export type MeetingConnectionStatus = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'offline' | 'error'

export interface MeetingRealtimeState {
  connectionStatus: MeetingConnectionStatus
  sessionState: MeetingSessionState
  lastCursor: number
  lastAckSequence: number
  asrLatencyMs: number | null
  bufferedFrames: number
  segments: MeetingTranscriptSegment[]
  partials: Record<string, MeetingPartialTranscript>
  speakers: MeetingSpeakerTrack[]
  rollingArtifacts: MeetingArtifact[]
  audioGaps: Array<{ from: number; to: number; retryable: boolean }>
  pipelineWarnings: Array<{ scope: string; message: string; recovered: boolean }>
  seenEventIds: Record<string, true>
}

export type MeetingRealtimeAction =
  | { type: 'event'; event: MeetingEvent }
  | { type: 'connection'; status: MeetingConnectionStatus }
  | { type: 'hydrate'; segments?: MeetingTranscriptSegment[]; speakers?: MeetingSpeakerTrack[]; artifacts?: MeetingArtifact[]; sessionState?: MeetingSessionState }
  | { type: 'reset' }

export function createMeetingRealtimeState(sessionState: MeetingSessionState = 'draft'): MeetingRealtimeState {
  return {
    connectionStatus: 'idle',
    sessionState,
    lastCursor: 0,
    lastAckSequence: -1,
    asrLatencyMs: null,
    bufferedFrames: 0,
    segments: [],
    partials: {},
    speakers: [],
    rollingArtifacts: [],
    audioGaps: [],
    pipelineWarnings: [],
    seenEventIds: {},
  }
}

function numberValue(value: unknown, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function stringValue(value: unknown, fallback = '') {
  return typeof value === 'string' ? value : fallback
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function normalizeSegment(payload: Record<string, unknown>): MeetingTranscriptSegment | null {
  const nested = recordValue(payload.segment)
  const value = Object.keys(nested).length ? nested : payload
  const id = stringValue(value.id || value.segment_id)
  const utteranceId = stringValue(value.utterance_id, id)
  if (!id || !utteranceId) return null
  const rawText = stringValue(value.raw_text || value.asr_final_text || value.text)
  const displayText = stringValue(value.display_text || value.text || value.normalized_text || value.asr_final_text, rawText)
  const state = stringValue(value.text_state || value.state, 'stable') as MeetingTranscriptSegment['text_state']
  return {
    id,
    meeting_id: stringValue(value.meeting_id),
    ordinal: numberValue(value.ordinal),
    utterance_id: utteranceId,
    start_ms: numberValue(value.start_ms),
    end_ms: numberValue(value.end_ms, numberValue(value.start_ms)),
    speaker_track_id: stringValue(value.speaker_track_id) || null,
    speaker_display_name: stringValue(value.speaker_display_name || value.speaker_name) || null,
    raw_text: rawText,
    asr_final_text: stringValue(value.asr_final_text, rawText),
    normalized_text: stringValue(value.normalized_text) || null,
    display_text: displayText,
    text: displayText,
    revision_no: numberValue(value.revision_no || value.current_revision, 1),
    text_state: state,
    human_locked: Boolean(value.human_locked || state === 'human_verified'),
    asr_confidence: value.asr_confidence == null ? null : numberValue(value.asr_confidence),
    overlap: Boolean(value.overlap),
    diff_ops: Array.isArray(value.diff_ops) ? value.diff_ops as MeetingTranscriptSegment['diff_ops'] : null,
    updated_at: stringValue(value.updated_at) || null,
  }
}

function normalizeSpeaker(payload: Record<string, unknown>): MeetingSpeakerTrack | null {
  const nested = recordValue(payload.speaker || payload.track)
  const value = Object.keys(nested).length ? nested : payload
  const id = stringValue(value.id || value.track_id || value.speaker_track_id)
  if (!id) return null
  const anonymousLabel = stringValue(value.anonymous_label, '发言人')
  return {
    id,
    meeting_id: stringValue(value.meeting_id),
    track_key: stringValue(value.track_key),
    anonymous_label: anonymousLabel,
    display_name: stringValue(value.display_name, anonymousLabel),
    label_source: stringValue(value.label_source, 'anonymous') as MeetingSpeakerTrack['label_source'],
    voice_profile_id: stringValue(value.voice_profile_id) || null,
    match_confidence: value.match_confidence == null ? null : numberValue(value.match_confidence),
    version: numberValue(value.version, 1),
    sample_duration_ms: value.sample_duration_ms == null ? null : numberValue(value.sample_duration_ms),
  }
}

function upsertSegment(items: MeetingTranscriptSegment[], incoming: MeetingTranscriptSegment) {
  const existingIndex = items.findIndex((item) => item.id === incoming.id || item.ordinal === incoming.ordinal)
  if (existingIndex < 0) return [...items, incoming].sort((a, b) => a.ordinal - b.ordinal)
  const existing = items[existingIndex]
  if (existing.human_locked && !incoming.human_locked) return items
  if (incoming.revision_no < existing.revision_no) return items
  const next = [...items]
  next[existingIndex] = { ...existing, ...incoming }
  return next
}

function upsertSpeaker(items: MeetingSpeakerTrack[], incoming: MeetingSpeakerTrack) {
  const index = items.findIndex((item) => item.id === incoming.id)
  if (index < 0) return [...items, incoming]
  if (incoming.version < items[index].version) return items
  const next = [...items]
  next[index] = { ...items[index], ...incoming }
  return next
}

function rememberEvent(seen: Record<string, true>, eventId: string) {
  const keys = Object.keys(seen)
  if (keys.length < 1000) return { ...seen, [eventId]: true as const }
  const trimmed: Record<string, true> = {}
  for (const key of keys.slice(-750)) trimmed[key] = true
  trimmed[eventId] = true
  return trimmed
}

export function applyMeetingEvent(state: MeetingRealtimeState, event: MeetingEvent): MeetingRealtimeState {
  if (state.seenEventIds[event.event_id]) return state
  const acceptsLateCursor = LATE_CURSOR_SAFE_EVENTS.has(event.type)
  if (event.cursor != null && event.cursor <= state.lastCursor && !acceptsLateCursor) return state

  const payload = recordValue(event.payload)
  let next: MeetingRealtimeState = {
    ...state,
    lastCursor: event.cursor == null ? state.lastCursor : Math.max(state.lastCursor, event.cursor),
    seenEventIds: rememberEvent(state.seenEventIds, event.event_id),
  }

  switch (event.type) {
    case 'session.state.changed': {
      const sessionState = stringValue(payload.state || payload.session_state) as MeetingSessionState
      if (sessionState) next = { ...next, sessionState }
      break
    }
    case 'stream.ready':
      next = { ...next, connectionStatus: 'connected' }
      break
    case 'stream.heartbeat':
      next = {
        ...next,
        connectionStatus: 'connected',
        asrLatencyMs: payload.asr_latency_ms == null ? next.asrLatencyMs : numberValue(payload.asr_latency_ms),
      }
      break
    case 'audio.ack':
      next = {
        ...next,
        lastAckSequence: Math.max(next.lastAckSequence, numberValue(payload.ack_sequence, -1)),
        bufferedFrames: numberValue(payload.buffered_frames),
      }
      break
    case 'audio.gap.detected':
      next = {
        ...next,
        audioGaps: [...next.audioGaps, {
          from: numberValue(payload.missing_from),
          to: numberValue(payload.missing_to),
          retryable: payload.retryable !== false,
        }].slice(-50),
      }
      break
    case 'transcript.partial': {
      const utteranceId = stringValue(payload.utterance_id)
      if (utteranceId) {
        next = {
          ...next,
          partials: {
            ...next.partials,
            [utteranceId]: {
              utterance_id: utteranceId,
              text: stringValue(payload.text),
              start_ms: numberValue(payload.start_ms),
              speaker_track_id: stringValue(payload.speaker_track_id) || null,
            },
          },
        }
      }
      break
    }
    case 'transcript.segment.stable':
    case 'transcript.segment.corrected':
    case 'transcript.segment.human_edited': {
      const segment = normalizeSegment(payload)
      if (segment) {
        const partials = { ...next.partials }
        delete partials[segment.utterance_id]
        next = { ...next, segments: upsertSegment(next.segments, segment), partials }
      }
      break
    }
    case 'speaker.track.created':
    case 'speaker.label.changed': {
      const speaker = normalizeSpeaker(payload)
      if (speaker) next = { ...next, speakers: upsertSpeaker(next.speakers, speaker) }
      break
    }
    case 'minutes.rolling.updated':
    case 'minutes.final.ready': {
      const artifactPayload = recordValue(payload.artifact)
      const artifact = (Object.keys(artifactPayload).length ? artifactPayload : payload) as unknown as MeetingArtifact
      if (artifact.id) {
        next = {
          ...next,
          rollingArtifacts: [artifact, ...next.rollingArtifacts.filter((item) => item.id !== artifact.id)]
            .sort((a, b) => b.version - a.version),
        }
      }
      break
    }
    case 'pipeline.degraded':
    case 'error':
      next = {
        ...next,
        pipelineWarnings: [...next.pipelineWarnings, {
          scope: stringValue(payload.scope, 'meeting'),
          message: stringValue(payload.message || payload.detail, '会议处理能力暂时降级'),
          recovered: false,
        }].slice(-20),
      }
      break
    case 'pipeline.recovered': {
      const scope = stringValue(payload.scope, 'meeting')
      next = {
        ...next,
        pipelineWarnings: next.pipelineWarnings.map((warning) => warning.scope === scope ? { ...warning, recovered: true } : warning),
      }
      break
    }
  }
  return next
}

export function meetingRealtimeReducer(state: MeetingRealtimeState, action: MeetingRealtimeAction): MeetingRealtimeState {
  if (action.type === 'reset') return createMeetingRealtimeState()
  if (action.type === 'connection') return { ...state, connectionStatus: action.status }
  if (action.type === 'event') return applyMeetingEvent(state, action.event)
  return {
    ...state,
    sessionState: action.sessionState || state.sessionState,
    segments: action.segments ? mergeTranscriptSegments(state.segments, action.segments) : state.segments,
    speakers: action.speakers || state.speakers,
    rollingArtifacts: action.artifacts || state.rollingArtifacts,
  }
}

export function latestStableAnnouncement(segments: MeetingTranscriptSegment[]) {
  const latest = segments[segments.length - 1]
  return latest ? `${latest.speaker_display_name || '发言人'}：${segmentDisplayText(latest)}` : ''
}
