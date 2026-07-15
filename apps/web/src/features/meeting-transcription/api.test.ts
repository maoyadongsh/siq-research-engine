/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { after, test } from 'node:test'

const {
  buildMeetingWebSocketUrl,
  correctMeetingSegment,
  createLexiconEntry,
  createMeetingAudioTicket,
  createMeetingExport,
  createMeeting,
  getMeetingLexicon,
  getMeetingCapabilities,
  getMeetingModels,
  getMeetingTranscript,
  listMeetings,
  mergeMeetingSpeakers,
  pauseMeeting,
  regenerateMeetingArtifact,
  renameMeetingSegmentSpeaker,
  renameMeetingSpeaker,
  updateMeetingModelSelection,
} = await import('./api.ts')

interface FetchCall {
  url: string
  method: string
  headers: Headers
  body?: unknown
}

const originalFetch = globalThis.fetch

after(() => {
  globalThis.fetch = originalFetch
})

function installFetch(responseBody: unknown) {
  const calls: FetchCall[] = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({
      url: typeof input === 'string' ? input : input.toString(),
      method: init?.method || 'GET',
      headers: new Headers(init?.headers),
      body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
    })
    return new Response(JSON.stringify(responseBody), { status: 200, headers: { 'content-type': 'application/json' } })
  }) as typeof fetch
  return calls
}

test('meeting list uses isolated v1 API with encoded query values', async () => {
  const calls = installFetch({ items: [], total: 0, offset: 20, limit: 20 })
  await listMeetings({ q: ' 投委会 Alpha ', state: 'live', sort: 'started_at_desc', offset: 20, limit: 20 })
  assert.equal(calls[0].url, '/api/meetings/v1/sessions?q=%E6%8A%95%E5%A7%94%E4%BC%9A+Alpha&state=live&sort=started_at_desc&offset=20&limit=20')
  assert.equal(calls[0].method, 'GET')
})

test('model API normalizes an item envelope', async () => {
  installFetch({ items: [{ model_ref: 'local-1', label: '本地模型' }] })
  const models = await getMeetingModels()
  assert.equal(models.length, 1)
  assert.equal(models[0].model_ref, 'local-1')
})

test('meeting capabilities expose correction learning as an explicit fail-closed control', async () => {
  installFetch({ enabled: true, correction_learning: { available: false, scope: 'user_private' } })
  const capabilities = await getMeetingCapabilities()
  assert.equal(capabilities.correction_learning?.available, false)
  assert.equal(capabilities.correction_learning?.scope, 'user_private')
})

test('meeting create sends frozen selection contract and idempotency key', async () => {
  const calls = installFetch({ id: 'meeting-1' })
  const payload = {
    title: '周例会',
    language: 'zh-CN',
    audio_source: 'microphone',
    voiceprint_enabled: false,
    ai_enabled: true,
    model_selection: { mode: 'pinned' as const, model_ref: 'model-1', fallback_policy: 'disabled' },
  }
  await createMeeting(payload, 'idem-create')
  assert.equal(calls[0].url, '/api/meetings/v1/sessions')
  assert.equal(calls[0].method, 'POST')
  assert.equal(calls[0].headers.get('Idempotency-Key'), 'idem-create')
  assert.deepEqual(calls[0].body, payload)
})

test('speaker rename and correction use object-scoped optimistic locks', async () => {
  const calls = installFetch({
    segment: {
      id: 'segment/one',
      meeting_id: 'meeting/alpha',
      ordinal: 1,
      utterance_id: 'utt-1',
      start_ms: 0,
      end_ms: 1000,
      raw_text: '海光新息',
      asr_final_text: '海光新息',
      display_text: '海光信息',
      current_revision_no: 3,
      display_layer: 'manual',
      human_locked: true,
    },
  })
  await renameMeetingSpeaker('meeting/alpha', 'track/one', '张三', 4, 'idem-speaker')
  await correctMeetingSegment('meeting/alpha', 'segment/one', {
    text: '海光信息',
    expected_revision: 2,
    edit_intent: 'asr_error',
    contribute_to_accuracy: true,
    candidate_terms: [{ canonical_term: '海光信息', misrecognition: '海光新息', promote_now: false }],
  }, 'idem-correction')

  assert.equal(calls[0].url, '/api/meetings/v1/sessions/meeting%2Falpha/speakers/track%2Fone')
  assert.deepEqual(calls[0].body, { display_name: '张三', expected_version: 4 })
  assert.equal(calls[1].url, '/api/meetings/v1/sessions/meeting%2Falpha/segments/segment%2Fone')
  assert.equal((calls[1].body as Record<string, unknown>).expected_revision, 2)
  assert.equal(calls[1].headers.get('Idempotency-Key'), 'idem-correction')
})

test('segment speaker rename sends explicit single-or-all scope and normalizes the returned segment', async () => {
  const calls = installFetch({
    operation: 'rename_segment',
    scope: 'segment',
    affected_segment_count: 1,
    event_id: 'event-1',
    event_cursor: 9,
    tracks: [{
      id: 'track/new',
      meeting_id: 'meeting/alpha',
      anonymous_label: '发言人 3',
      display_name: '王敏',
      label_source: 'manual',
      version: 1,
    }],
    segment: {
      id: 'segment/one',
      meeting_id: 'meeting/alpha',
      ordinal: 1,
      utterance_id: 'utt-1',
      start_ms: 0,
      end_ms: 1000,
      speaker_track_id: 'track/new',
      speaker_label: '王敏',
      raw_text: '原始文字',
      asr_final_text: '原始文字',
      display_text: '显示文字',
      current_revision_no: 2,
      display_layer: 'human_verified',
      human_locked: true,
    },
  })

  const result = await renameMeetingSegmentSpeaker('meeting/alpha', 'segment/one', {
    display_name: '王敏',
    scope: 'segment',
    expected_speaker_version: 4,
  }, 'idem-segment-speaker')

  assert.equal(calls[0].url, '/api/meetings/v1/sessions/meeting%2Falpha/segments/segment%2Fone/speaker')
  assert.equal(calls[0].method, 'PATCH')
  assert.equal(calls[0].headers.get('Idempotency-Key'), 'idem-segment-speaker')
  assert.deepEqual(calls[0].body, {
    display_name: '王敏',
    scope: 'segment',
    expected_speaker_version: 4,
  })
  assert.equal(result.segment.speaker_display_name, '王敏')
  assert.equal(result.segment.revision_no, 2)
  assert.equal(result.segment.text_state, 'human_verified')
})

test('speaker merge sends every source and optimistic version in one mutation', async () => {
  const calls = installFetch({
    operation: 'merge',
    meeting_id: 'meeting/alpha',
    source_track_ids: ['track/two', 'track/three'],
    target_track_ids: ['track/one'],
    segment_ids: ['segment/one', 'segment/two'],
    event_id: 'event-merge',
    event_cursor: 12,
    tracks: [],
  })

  const result = await mergeMeetingSpeakers(
    'meeting/alpha',
    'track/one',
    ['track/two', 'track/three'],
    { 'track/one': 4, 'track/two': 2, 'track/three': 7 },
    'idem-speaker-merge',
  )

  assert.equal(calls[0].url, '/api/meetings/v1/sessions/meeting%2Falpha/speakers/track%2Fone/merge')
  assert.equal(calls[0].method, 'POST')
  assert.equal(calls[0].headers.get('Idempotency-Key'), 'idem-speaker-merge')
  assert.deepEqual(calls[0].body, {
    source_track_ids: ['track/two', 'track/three'],
    expected_versions: { 'track/one': 4, 'track/two': 2, 'track/three': 7 },
  })
  assert.equal(result.segment_ids.length, 2)
})

test('websocket URL never embeds a long-lived auth token', () => {
  const url = buildMeetingWebSocketUrl('meeting/alpha', { ticket: 'one-time-ticket', stream_epoch: 1 })
  assert.equal(url, '/api/meetings/v1/sessions/meeting%2Falpha/audio?ticket=one-time-ticket')
  assert.equal(url.includes('access_token'), false)
})

test('audio playback uses a purpose-bound short ticket instead of a bearer URL', async () => {
  const calls = installFetch({
    ticket: 'playback-ticket',
    expires_at: '2026-07-13T08:15:00Z',
    audio_url: '/api/meetings/v1/sessions/meeting-1/audio?playback_ticket=playback-ticket',
    purpose: 'meeting_audio_playback',
  })
  const ticket = await createMeetingAudioTicket('meeting-1', 'idem-audio-ticket')

  assert.equal(calls[0].url, '/api/meetings/v1/sessions/meeting-1/audio-ticket')
  assert.equal(calls[0].method, 'POST')
  assert.equal(calls[0].headers.get('Idempotency-Key'), 'idem-audio-ticket')
  assert.equal(ticket.purpose, 'meeting_audio_playback')
  assert.equal(ticket.audio_url.includes('access_token'), false)
})

test('meeting export sends the selected Word format and exact source artifact version', async () => {
  const calls = installFetch({ id: 'export-1', format: 'docx', state: 'ready' })
  await createMeetingExport('meeting/alpha', {
    format: 'docx',
    content: 'minutes',
    transcript_source: 'display',
    artifact_id: 'minutes/one',
    artifact_version: 3,
  }, 'idem-docx-export')

  assert.equal(calls[0].url, '/api/meetings/v1/sessions/meeting%2Falpha/exports')
  assert.equal(calls[0].method, 'POST')
  assert.equal(calls[0].headers.get('Idempotency-Key'), 'idem-docx-export')
  assert.deepEqual(calls[0].body, {
    format: 'docx',
    content: 'minutes',
    transcript_source: 'display',
    artifact_id: 'minutes/one',
    artifact_version: 3,
  })
})

test('meeting minutes regeneration creates a new artifact job without overwriting the selected version', async () => {
  const calls = installFetch({
    artifact: {
      id: 'minutes-4',
      meeting_id: 'meeting/alpha',
      artifact_type: 'final_minutes',
      version: 4,
      state: 'generating',
      supersedes_id: 'minutes-3',
    },
    job: {
      id: 'job-4',
      job_kind: 'final_minutes',
      state: 'queued',
      attempt: 0,
      max_attempts: 3,
    },
  })

  const result = await regenerateMeetingArtifact('meeting/alpha', 'minutes/3', 7)

  assert.equal(calls[0].url, '/api/meetings/v1/sessions/meeting%2Falpha/artifacts/minutes%2F3/regenerate')
  assert.equal(calls[0].method, 'POST')
  assert.deepEqual(calls[0].body, { expected_settings_version: 7 })
  assert.equal(result.artifact.version, 4)
  assert.equal(result.job.job_type, 'final_minutes')
})

test('lifecycle action unwraps the backend session envelope', async () => {
  installFetch({ session: { id: 'meeting-1', state: 'paused' }, idempotent: false, event_cursor: 8 })
  const session = await pauseMeeting('meeting-1', 3, 'idem-pause')
  assert.equal(session.id, 'meeting-1')
  assert.equal(session.state, 'paused')
})

test('transcript response maps backend revision and speaker fields', async () => {
  const calls = installFetch({
    items: [{
      id: 'segment-1', meeting_id: 'meeting-1', ordinal: 1, utterance_id: 'utt-1', start_ms: 0, end_ms: 500,
      speaker_label: '张三', raw_text: '海光新息', asr_final_text: '海光新息', display_text: '海光信息',
      current_revision_no: 4, display_layer: 'manual', human_locked: true,
    }],
    next_ordinal: null,
  })
  const page = await getMeetingTranscript('meeting-1', { afterOrdinal: 4_800, limit: 200 })
  assert.equal(calls[0].url, '/api/meetings/v1/sessions/meeting-1/transcript?after_ordinal=4800&limit=200')
  assert.equal(page.items[0].speaker_display_name, '张三')
  assert.equal(page.items[0].revision_no, 4)
  assert.equal(page.items[0].text_state, 'human_verified')
})

test('transcript playback lookup requests a bounded window around audio time', async () => {
  const calls = installFetch({ items: [], next_ordinal: null })
  await getMeetingTranscript('meeting-1', { atMs: 123_456, limit: 200 })
  assert.equal(calls[0].url, '/api/meetings/v1/sessions/meeting-1/transcript?at_ms=123456&limit=200')
})

test('transcript response recognizes the backend llm_corrected display layer', async () => {
  installFetch({
    items: [{
      id: 'segment-llm', meeting_id: 'meeting-1', ordinal: 2, utterance_id: 'utt-2', start_ms: 500, end_ms: 900,
      raw_text: '耐莫创', asr_final_text: '耐莫创', display_text: 'Nemotron',
      current_revision_no: 1, display_layer: 'llm_corrected', human_locked: false,
    }],
    next_ordinal: null,
  })
  const page = await getMeetingTranscript('meeting-1')
  assert.equal(page.items[0].text_state, 'optimized')
})

test('lexicon and model selection adapt backend envelopes', async () => {
  installFetch({
    schema_version: 'meeting.lexicon.v1',
    language: 'zh-CN',
    active_version: { version: 6, created_at: '2026-07-13T08:00:00Z' },
    entries: [],
  })
  const lexicon = await getMeetingLexicon()
  assert.equal(lexicon.version, 6)

  const calls = installFetch({
    meeting_id: 'meeting-1', settings_version: 3, selection_mode: 'pinned', requested_model_ref: 'cloud-1',
    fallback_policy: 'disabled', effective_after_segment_ordinal: 42,
  })
  await updateMeetingModelSelection('meeting-1', {
    mode: 'pinned',
    model_ref: 'cloud-1',
    fallback_policy: 'disabled',
    expected_settings_version: 2,
    cloud_data_boundary_confirmed: true,
  }, 'idem-model')
  assert.deepEqual(calls[0].body, {
    expected_settings_version: 2,
    selection: {
      mode: 'pinned',
      model_ref: 'cloud-1',
      fallback_policy: 'disabled',
      cloud_data_boundary_confirmed: true,
    },
  })
})

test('meeting-scoped lexicon requests preserve the meeting boundary', async () => {
  const calls = installFetch({
    schema_version: 'meeting.lexicon.v1',
    language: 'zh-CN',
    active_version: { version: 9, meeting_id: 'meeting/alpha', created_at: '2026-07-13T08:00:00Z' },
    entries: [],
  })
  await getMeetingLexicon('meeting/alpha')
  await createLexiconEntry({
    canonical_term: '海光信息',
    language: 'zh-CN',
    weight: 6,
    scope: 'current_meeting',
    meeting_id: 'meeting/alpha',
  }, 'meeting-lexicon-entry')

  assert.equal(calls[0].url, '/api/meetings/v1/lexicon?meeting_id=meeting%2Falpha')
  assert.deepEqual(calls[1].body, {
    canonical_term: '海光信息',
    language: 'zh-CN',
    weight: 6,
    scope: 'current_meeting',
    meeting_id: 'meeting/alpha',
  })
})
