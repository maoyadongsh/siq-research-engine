import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createNativePlaybackState,
  deriveNativeCaptureOperationalState,
  recoverNativeCaptureAfterForeground,
  reduceNativePlaybackState,
} from './nativeCaptureState'
import {
  MEETING_NATIVE_CAPTURE_SCHEMA_VERSION,
  type MeetingCaptureCheckpoints,
  type MeetingCaptureStatus,
} from './nativeCapture'

function status(state: MeetingCaptureStatus['state'], pendingUploadCount = 0): MeetingCaptureStatus {
  return {
    schemaVersion: MEETING_NATIVE_CAPTURE_SCHEMA_VERSION,
    adapter: 'ios_native',
    state,
    meetingId: 'meeting-1',
    captureId: 'capture-1',
    streamEpoch: 3,
    recordedThroughSample: 32_000,
    lastSealedSequence: 1,
    manifestRevision: 2,
    pendingUploadCount,
    localPlaybackReady: state === 'stopped',
    interruptionReason: null,
    errorCode: null,
  }
}

function checkpoints(overrides: Partial<MeetingCaptureCheckpoints> = {}): MeetingCaptureCheckpoints {
  return {
    capture: { recordedThroughSample: 32_000, lastSealedSequence: 1, manifestRevision: 2 },
    ingest: {
      highestUploadedSequence: 1,
      highestContiguousSequence: 1,
      persistedThroughSample: 32_000,
      missingSequenceRanges: [],
    },
    realtime: { streamEpoch: 3, consumedThroughSample: 32_000, stableOrdinal: 5, eventCursor: 8 },
    finalization: {
      sealedThroughSample: 0,
      ingestComplete: false,
      localPlaybackReady: false,
      serverPlaybackState: 'not_ready',
      postprocessState: 'not_started',
    },
    ...overrides,
  }
}

test('four native checkpoints stay distinct and stopped capture never reports realtime reconnect', () => {
  const active = deriveNativeCaptureOperationalState({
    status: status('recording', 2),
    checkpoints: checkpoints({
      ingest: {
        highestUploadedSequence: 3,
        highestContiguousSequence: 1,
        persistedThroughSample: 16_000,
        missingSequenceRanges: [{ start: 2, end: 3 }],
      },
    }),
    online: false,
  })
  assert.deepEqual(
    { capture: active.capture, ingest: active.ingest, realtime: active.realtime, playback: active.playback },
    { capture: 'recording', ingest: 'pending_upload', realtime: 'waiting_for_ingest', playback: 'unavailable' },
  )
  assert.equal(active.missingSequenceCount, 2)

  const unknownRealtimeWatermark = deriveNativeCaptureOperationalState({
    status: status('recording'),
    checkpoints: checkpoints({
      realtime: {
        streamEpoch: 3,
        consumedThroughSample: null,
        stableOrdinal: 5,
        eventCursor: null,
      },
    }),
    online: true,
  })
  assert.equal(unknownRealtimeWatermark.realtime, 'active')

  const stopped = deriveNativeCaptureOperationalState({
    status: status('stopped'),
    checkpoints: checkpoints({
      finalization: {
        sealedThroughSample: 32_000,
        ingestComplete: false,
        localPlaybackReady: true,
        serverPlaybackState: 'pending_upload',
        postprocessState: 'queued',
      },
    }),
    online: true,
  })
  assert.equal(stopped.realtime, 'inactive')
  assert.equal(stopped.playback, 'local_ready')
  assert.equal(stopped.postprocess, 'queued')

  const stoppedWithRecoverableBacklog = deriveNativeCaptureOperationalState({
    status: status('stopped', 1),
    checkpoints: checkpoints({
      ingest: {
        highestUploadedSequence: 1,
        highestContiguousSequence: 0,
        persistedThroughSample: 16_000,
        missingSequenceRanges: [{ start: 1, end: 1 }],
      },
    }),
    online: false,
  })
  assert.equal(stoppedWithRecoverableBacklog.ingest, 'pending_upload')

  const irrecoverableGap = deriveNativeCaptureOperationalState({
    status: status('stopped'),
    checkpoints: checkpoints({
      ingest: {
        highestUploadedSequence: 1,
        highestContiguousSequence: 0,
        persistedThroughSample: 16_000,
        missingSequenceRanges: [{ start: 1, end: 1 }],
      },
    }),
    online: true,
  })
  assert.equal(irrecoverableGap.ingest, 'gap')
})

test('foreground recovery orders native snapshot, server reconciliation, upload retry, then rollover', async () => {
  const calls: string[] = []
  let currentStatus = status('recording', 1)
  let currentCheckpoint = checkpoints({
    ingest: {
      highestUploadedSequence: 1,
      highestContiguousSequence: 0,
      persistedThroughSample: 16_000,
      missingSequenceRanges: [{ start: 1, end: 1 }],
    },
  })
  const result = await recoverNativeCaptureAfterForeground({
    getStatus: async () => { calls.push('status'); return currentStatus },
    getCheckpoints: async () => { calls.push('checkpoint'); return currentCheckpoint },
    retryPendingUploads: async () => {
      calls.push('retry')
      currentStatus = status('recording', 0)
      currentCheckpoint = checkpoints()
      return currentStatus
    },
    rollover: async ({ checkpoints: reconciled }) => {
      calls.push('rollover')
      assert.equal(reconciled.ingest.persistedThroughSample, 32_000)
      return { streamEpoch: 4 }
    },
  })
  assert.deepEqual(calls, ['status', 'checkpoint', 'retry', 'checkpoint', 'rollover'])
  assert.deepEqual(result.steps, ['status', 'checkpoint', 'retry_uploads', 'checkpoint_after_retry', 'rollover'])
  assert.equal(result.outcome, 'rolled_over')
  assert.deepEqual(result.rollover, { streamEpoch: 4 })
})

test('foreground recovery leaves unresolved gaps pending and never enters an infinite rollover loop', async () => {
  let rolloverCalls = 0
  const unresolved = checkpoints({
    ingest: {
      highestUploadedSequence: 3,
      highestContiguousSequence: 0,
      persistedThroughSample: 16_000,
      missingSequenceRanges: [{ start: 1, end: 3 }],
    },
  })
  const result = await recoverNativeCaptureAfterForeground({
    getStatus: async () => status('interrupted', 3),
    getCheckpoints: async () => unresolved,
    retryPendingUploads: async () => status('interrupted', 3),
    rollover: async () => { rolloverCalls += 1; return { streamEpoch: 4 } },
  })
  assert.equal(result.outcome, 'waiting_for_upload')
  assert.equal(rolloverCalls, 0)

  let stoppedRetryCalls = 0
  const stopped = await recoverNativeCaptureAfterForeground({
    getStatus: async () => status('stopped', 1),
    getCheckpoints: async () => unresolved,
    retryPendingUploads: async () => {
      stoppedRetryCalls += 1
      return status('stopped', 1)
    },
    rollover: async () => { throw new Error('must not roll over a stopped capture') },
  })
  assert.equal(stopped.outcome, 'stopped')
  assert.equal(stoppedRetryCalls, 1)
  assert.deepEqual(
    stopped.steps,
    ['status', 'checkpoint', 'retry_uploads', 'checkpoint_after_retry'],
  )
})

test('foreground recovery cannot roll over after the capture stops during upload reconciliation', async () => {
  let rolloverCalls = 0
  const result = await recoverNativeCaptureAfterForeground({
    getStatus: async () => status('recording', 1),
    getCheckpoints: async () => checkpoints({
      ingest: {
        highestUploadedSequence: 0,
        highestContiguousSequence: 0,
        persistedThroughSample: 16_000,
        missingSequenceRanges: [],
      },
    }),
    retryPendingUploads: async () => status('stopped'),
    rollover: async () => { rolloverCalls += 1; return { streamEpoch: 4 } },
  })
  assert.equal(result.outcome, 'stopped')
  assert.equal(rolloverCalls, 0)
})

test('rollover rejects epoch rollback and does not retain returned credentials', async () => {
  const dependencies = {
    getStatus: async () => status('recording'),
    getCheckpoints: async () => checkpoints(),
    retryPendingUploads: async () => status('recording'),
  }
  await assert.rejects(
    recoverNativeCaptureAfterForeground({
      ...dependencies,
      rollover: async () => ({ streamEpoch: 3 }),
    }),
    /did not advance/,
  )

  const result = await recoverNativeCaptureAfterForeground({
    ...dependencies,
    rollover: async () => ({
      streamEpoch: 4,
      streamTicket: 'must-not-enter-state',
      wsUrl: 'wss://example.test?ticket=must-not-enter-state',
    }),
  })
  assert.deepEqual(result.rollover, { streamEpoch: 4 })
  assert.equal(JSON.stringify(result).includes('must-not-enter-state'), false)
})

test('malformed and enormous missing ranges saturate without numeric overflow', () => {
  const operational = deriveNativeCaptureOperationalState({
    status: status('recording'),
    checkpoints: checkpoints({
      ingest: {
        highestUploadedSequence: 0,
        highestContiguousSequence: 0,
        persistedThroughSample: 0,
        missingSequenceRanges: [{ start: 0, end: Number.MAX_SAFE_INTEGER }],
      },
    }),
    online: true,
  })
  assert.equal(operational.missingSequenceCount, Number.MAX_SAFE_INTEGER)
  assert.equal(Number.isSafeInteger(operational.missingSequenceCount), true)
  assert.equal(operational.ingest, 'syncing')
})

test('server playback switch preserves position and falls back to the opaque local handle', () => {
  const local = {
    handle: 'capture-asset:capture-1',
    webUrl: 'file:///private/recordings/capture-1.wav',
    mediaType: 'audio/wav',
    durationMs: 10_000,
  }
  let state = reduceNativePlaybackState(createNativePlaybackState(), { type: 'local_ready', asset: local })
  state = reduceNativePlaybackState(state, { type: 'position', currentTimeSeconds: 7.25, playing: true })
  state = reduceNativePlaybackState(state, {
    type: 'server_ready',
    url: 'https://api.example.test/audio?playback_ticket=must-not-enter-state',
  })
  assert.equal(state.source, 'local')
  assert.equal(state.phase, 'switching_to_server')
  assert.equal(state.currentTimeSeconds, 7.25)
  assert.equal(state.resumeAfterSwitch, true)
  assert.equal(state.localAsset?.handle, 'capture-asset:capture-1')
  assert.equal(JSON.stringify(state).includes('file://'), false)
  assert.equal(JSON.stringify(state).includes('must-not-enter-state'), false)

  const failed = reduceNativePlaybackState(state, {
    type: 'server_switch_failed',
    message: 'range unavailable: playback_ticket=must-not-enter-state',
  })
  assert.equal(failed.source, 'local')
  assert.equal(failed.phase, 'local_ready')
  assert.equal(failed.currentTimeSeconds, 7.25)
  assert.equal(failed.switchError, 'server_playback_switch_failed')
  assert.equal(JSON.stringify(failed).includes('must-not-enter-state'), false)

  const switching = reduceNativePlaybackState(failed, { type: 'server_ready', url: '/audio?ticket=second' })
  const ready = reduceNativePlaybackState(switching, { type: 'server_switch_succeeded' })
  assert.equal(ready.source, 'server')
  assert.equal(ready.currentTimeSeconds, 7.25)

  const staleFailure = reduceNativePlaybackState(ready, {
    type: 'server_switch_failed',
    message: 'stale callback',
  })
  assert.equal(staleFailure, ready)

  const invalidPosition = reduceNativePlaybackState(ready, {
    type: 'position',
    currentTimeSeconds: Number.NaN,
    playing: false,
  })
  assert.equal(invalidPosition, ready)
})
