import assert from 'node:assert/strict'
import test from 'node:test'

import {
  MEETING_NATIVE_CAPTURE_SCHEMA_VERSION,
  type MeetingCaptureStatus,
  type MeetingNativeCaptureCleanupReceipt,
  type MeetingNativeCapturePrepareOptions,
} from './nativeCapture'
import type { NativeCaptureCreateResponse } from './nativeCaptureApi'
import {
  getOrCreateNativeCaptureIdentity,
  nativeCleanupReady,
  startNativeCaptureSession,
  validateNativeCleanupReceipt,
} from './nativeCaptureSession'

function status(state: MeetingCaptureStatus['state']): MeetingCaptureStatus {
  return {
    schemaVersion: MEETING_NATIVE_CAPTURE_SCHEMA_VERSION,
    adapter: 'ios_native',
    state,
    meetingId: 'meeting-1',
    captureId: '00000000-0000-4000-8000-000000000001',
    streamEpoch: 2,
    recordedThroughSample: 32_000,
    lastSealedSequence: 1,
    manifestRevision: 4,
    pendingUploadCount: 0,
    localPlaybackReady: state === 'stopped',
    interruptionReason: null,
    errorCode: null,
  }
}

function createResponse(): NativeCaptureCreateResponse {
  return {
    schema_version: MEETING_NATIVE_CAPTURE_SCHEMA_VERSION,
    capture: {
      schema_version: MEETING_NATIVE_CAPTURE_SCHEMA_VERSION,
      id: '00000000-0000-4000-8000-000000000001',
      meeting_id: 'meeting-1',
      state: 'active',
      encoding: 'pcm_s16le',
      sample_rate: 16_000,
      channels: 1,
      current_epoch: 2,
      total_bytes: 0,
      total_samples: 0,
      sealed_through_sample: null,
      ingest_complete: false,
      server_playback_state: 'not_ready',
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:00:00Z',
      sealed_at: null,
      revoked_at: null,
    },
    capture_token: 'capture-secret-that-must-stay-in-memory',
    token_type: 'Bearer',
    token_expires_at: '2026-07-15T01:00:00Z',
    scopes: ['batch:write'],
    replayed: false,
    limits: {
      max_batch_bytes: 1_048_576,
      max_total_bytes: 1_500_000_000,
      max_duration_seconds: 14_400,
    },
  }
}

test('native capture orchestration creates after meeting start and keeps capture credentials in the prepare call only', async () => {
  const calls: string[] = []
  const prepared: MeetingNativeCapturePrepareOptions[] = []
  const adapter = {
    prepare: async (options: MeetingNativeCapturePrepareOptions) => {
      calls.push('prepare')
      prepared.push(options)
      return status('prepared')
    },
    start: async () => { calls.push('start'); return status('recording') },
    resume: async () => { calls.push('resume'); return status('recording') },
  }
  const result = await startNativeCaptureSession({
    adapter,
    meetingId: 'meeting-1',
    streamEpoch: 2,
    apiBaseUrl: 'https://meeting.example.test/api/meetings/v1',
    identity: {
      deviceInstallationId: 'ios-device-installation-1',
      createIdempotencyKey: 'ios-create-idempotency-1',
    },
    userBearerToken: 'foreground-user-bearer',
    createCapture: async (meetingId, payload, idempotencyKey) => {
      calls.push('create')
      assert.equal(meetingId, 'meeting-1')
      assert.equal(payload.device_installation_id, 'ios-device-installation-1')
      assert.equal(idempotencyKey, 'ios-create-idempotency-1')
      return createResponse()
    },
  })

  assert.deepEqual(calls, ['create', 'prepare', 'start'])
  assert.equal(prepared[0]?.captureToken, 'capture-secret-that-must-stay-in-memory')
  assert.equal(prepared[0]?.userBearerToken, 'foreground-user-bearer')
  assert.equal(prepared[0]?.maxDurationSeconds, 14_400)
  assert.deepEqual(Object.keys(result).sort(), ['captureId', 'replayed', 'status'])
  assert.equal(JSON.stringify(result).includes('capture-secret'), false)
})

test('a recovered interrupted capture resumes instead of opening a second recorder', async () => {
  const calls: string[] = []
  const result = await startNativeCaptureSession({
    adapter: {
      prepare: async () => { calls.push('prepare'); return status('interrupted') },
      start: async () => { calls.push('start'); return status('recording') },
      resume: async () => { calls.push('resume'); return status('recording') },
    },
    meetingId: 'meeting-1',
    streamEpoch: 2,
    apiBaseUrl: 'https://meeting.example.test/api/meetings/v1',
    identity: {
      deviceInstallationId: 'ios-device-installation-1',
      createIdempotencyKey: 'ios-create-idempotency-1',
    },
    createCapture: async () => createResponse(),
  })
  assert.deepEqual(calls, ['prepare', 'resume'])
  assert.equal(result.status.state, 'recording')
})

test('durable non-secret identity survives a cold-start wrapper without storing credentials', () => {
  const values = new Map<string, string>()
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) },
  }
  const first = getOrCreateNativeCaptureIdentity('meeting/1', storage)
  const coldStartStorage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) },
  }
  const second = getOrCreateNativeCaptureIdentity('meeting/1', coldStartStorage)
  assert.deepEqual(second, first)
  assert.equal([...values.values()].some((value) => /token|secret|bearer/i.test(value)), false)
  assert.equal([...values.keys()].some((key) => /token|secret|bearer/i.test(key)), false)
})

test('cold recovery refuses to bind a newly created capture over a recovered manifest', async () => {
  let prepareCalled = false
  await assert.rejects(() => startNativeCaptureSession({
    adapter: {
      prepare: async () => { prepareCalled = true; return status('prepared') },
      start: async () => status('recording'),
      resume: async () => status('recording'),
    },
    meetingId: 'meeting-1',
    streamEpoch: 2,
    apiBaseUrl: 'https://meeting.example.test/api/meetings/v1',
    identity: {
      deviceInstallationId: 'ios-device-installation-1',
      createIdempotencyKey: 'ios-create-idempotency-1',
    },
    expectedCaptureId: 'recovered-capture-id',
    createCapture: async () => createResponse(),
  }), /恢复身份不一致/)
  assert.equal(prepareCalled, false)
})

test('cleanup is exposed only after complete server playback and requires an exact receipt', () => {
  const stopped = status('stopped')
  const checkpoints = {
    capture: { recordedThroughSample: 32_000, lastSealedSequence: 1, manifestRevision: 4 },
    ingest: { highestUploadedSequence: 1, highestContiguousSequence: 1, persistedThroughSample: 32_000, missingSequenceRanges: [] },
    realtime: { streamEpoch: 2, consumedThroughSample: 32_000, stableOrdinal: 1, eventCursor: 1 },
    finalization: { sealedThroughSample: 32_000, ingestComplete: true, localPlaybackReady: true, serverPlaybackState: 'ready' as const, postprocessState: 'queued' },
  }
  assert.equal(nativeCleanupReady(stopped, checkpoints), true)
  assert.equal(nativeCleanupReady(status('recording'), checkpoints), false)
  assert.equal(nativeCleanupReady(stopped, {
    ...checkpoints,
    ingest: { ...checkpoints.ingest, missingSequenceRanges: [{ start: 1, end: 1 }] },
  }), false)
  assert.equal(nativeCleanupReady(stopped, {
    ...checkpoints,
    finalization: { ...checkpoints.finalization, sealedThroughSample: 31_999 },
  }), false)
  assert.equal(nativeCleanupReady(stopped, {
    ...checkpoints,
    capture: { ...checkpoints.capture, manifestRevision: 5 },
  }), false)
  assert.equal(nativeCleanupReady(stopped, {
    ...checkpoints,
    capture: { ...checkpoints.capture, recordedThroughSample: 31_999 },
  }), false)
  assert.equal(nativeCleanupReady(stopped, {
    ...checkpoints,
    ingest: { ...checkpoints.ingest, persistedThroughSample: 31_999 },
  }), false)

  const receipt: MeetingNativeCaptureCleanupReceipt = {
    schemaVersion: 'siq.meeting.native_capture.cleanup_receipt.v1',
    captureId: stopped.captureId || '',
    meetingId: stopped.meetingId || '',
    streamEpoch: 2,
    recordedThroughSample: 32_000,
    manifestRevision: 4,
    serverWavSha256: 'a'.repeat(64),
    serverWavByteSize: 64_044,
    verifiedAt: '2026-07-15T00:10:00Z',
  }
  assert.equal(validateNativeCleanupReceipt(receipt, stopped), receipt)
  assert.throws(
    () => validateNativeCleanupReceipt({ ...receipt, recordedThroughSample: 31_999 }, stopped),
    /回执校验失败/,
  )
})
