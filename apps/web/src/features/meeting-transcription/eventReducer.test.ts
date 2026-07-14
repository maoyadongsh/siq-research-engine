/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { applyMeetingEvent, createMeetingRealtimeState, meetingRealtimeReducer } from './eventReducer.ts'
import type { MeetingEvent, MeetingTranscriptSegment } from './types.ts'

function event(type: string, payload: Record<string, unknown>, cursor: number | null, id = `${type}-${cursor}`): MeetingEvent {
  return {
    schema_version: 'siq.meeting.event.v1',
    event_id: id,
    meeting_id: 'meeting-1',
    type,
    cursor,
    emitted_at: '2026-07-13T08:00:00Z',
    payload,
  }
}

test('partial text is replaced in place and removed by its stable segment', () => {
  let state = createMeetingRealtimeState('live')
  state = applyMeetingEvent(state, event('transcript.partial', { utterance_id: 'utt-1', text: '海光新', start_ms: 1000 }, null, 'partial-1'))
  state = applyMeetingEvent(state, event('transcript.partial', { utterance_id: 'utt-1', text: '海光信息', start_ms: 1000 }, null, 'partial-2'))
  assert.equal(Object.keys(state.partials).length, 1)
  assert.equal(state.partials['utt-1'].text, '海光信息')

  state = applyMeetingEvent(state, event('transcript.segment.stable', {
    id: 'segment-1',
    meeting_id: 'meeting-1',
    utterance_id: 'utt-1',
    ordinal: 1,
    start_ms: 1000,
    end_ms: 2000,
    raw_text: '海光信息',
    asr_final_text: '海光信息',
    revision_no: 1,
  }, 1))

  assert.equal(Object.keys(state.partials).length, 0)
  assert.equal(state.segments.length, 1)
  assert.equal(state.segments[0].display_text, '海光信息')
})

test('durable events are cursor ordered and event-id idempotent', () => {
  const first = event('session.state.changed', { state: 'paused' }, 10, 'state-10')
  const state = applyMeetingEvent(createMeetingRealtimeState('live'), first)
  assert.equal(state.sessionState, 'paused')
  assert.equal(applyMeetingEvent(state, first), state)

  const stale = event('session.state.changed', { state: 'live' }, 9, 'state-9')
  assert.equal(applyMeetingEvent(state, stale), state)
})

test('late durable AI artifacts are accepted once without moving the cursor backwards', () => {
  const current = applyMeetingEvent(
    createMeetingRealtimeState('live'),
    event('stream.heartbeat', {}, 11, 'event-11'),
  )
  const lateArtifact = event('minutes.rolling.updated', {
    artifact: { id: 'artifact-1', artifact_type: 'rolling_minutes', version: 1, state: 'ready' },
  }, 10, 'event-10')

  const updated = applyMeetingEvent(current, lateArtifact)
  assert.equal(updated.lastCursor, 11)
  assert.equal(updated.rollingArtifacts[0]?.id, 'artifact-1')
  assert.equal(applyMeetingEvent(updated, lateArtifact), updated)
})

test('human locked text is not overwritten by a later non-human revision', () => {
  let state = createMeetingRealtimeState('stopped')
  state = applyMeetingEvent(state, event('transcript.segment.human_edited', {
    id: 'segment-1', utterance_id: 'utt-1', ordinal: 1, text: '人工确认文字', revision_no: 3, human_locked: true,
  }, 3))
  state = applyMeetingEvent(state, event('transcript.segment.corrected', {
    id: 'segment-1', utterance_id: 'utt-1', ordinal: 1, text: '后台旧文字', revision_no: 4, human_locked: false,
  }, 4))
  assert.equal(state.segments[0].display_text, '人工确认文字')
  assert.equal(state.segments[0].revision_no, 3)
})

test('ACK and gap events update bounded transport state', () => {
  let state = createMeetingRealtimeState('live')
  state = applyMeetingEvent(state, event('audio.ack', { ack_sequence: 18, buffered_frames: 2 }, 2))
  state = applyMeetingEvent(state, event('audio.gap.detected', { missing_from: 19, missing_to: 21, retryable: true }, 3))
  assert.equal(state.lastAckSequence, 18)
  assert.equal(state.bufferedFrames, 2)
  assert.deepEqual(state.audioGaps, [{ from: 19, to: 21, retryable: true }])
})

test('hydrating a transcript page merges around realtime segments instead of replacing them', () => {
  const segment = (ordinal: number): MeetingTranscriptSegment => ({
    id: `segment-${ordinal}`,
    meeting_id: 'meeting-1',
    utterance_id: `utterance-${ordinal}`,
    ordinal,
    start_ms: ordinal * 1_000,
    end_ms: ordinal * 1_000 + 800,
    raw_text: `segment ${ordinal}`,
    asr_final_text: `segment ${ordinal}`,
    display_text: `segment ${ordinal}`,
    revision_no: 1,
    text_state: 'stable',
    human_locked: false,
  })
  const liveState = { ...createMeetingRealtimeState('live'), segments: [segment(5_001)] }
  const hydrated = meetingRealtimeReducer(liveState, {
    type: 'hydrate',
    segments: [segment(4_801), segment(5_000)],
  })

  assert.deepEqual(hydrated.segments.map((item) => item.ordinal), [4_801, 5_000, 5_001])
})
