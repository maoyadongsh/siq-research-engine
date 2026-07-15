import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import type { MeetingAudioCaptureOptions } from './audioCapture'
import {
  IosNativeCaptureAdapter,
  selectMeetingCaptureAdapter,
  WebAudioWorkletCaptureAdapter,
  type MeetingCaptureCapabilityEnvelope,
} from './captureAdapter'
import {
  MEETING_NATIVE_CAPTURE_SCHEMA_VERSION,
  type MeetingCaptureCheckpoints,
  type MeetingCapturePluginBridge,
  type MeetingCaptureStatus,
} from './nativeCapture'

const capabilities: MeetingCaptureCapabilityEnvelope = {
  audio: {
    capture_adapters: {
      web_audio_worklet: { available: true, background_recording: false },
      ios_native: { available: true, adapter: 'ios_native', requires_native_runtime: true },
    },
  },
}

test('native adapter selection requires the frontend flag, iOS runtime, plugin, and backend capability', () => {
  const iosRuntime = { native: true, platform: 'ios', pluginAvailable: true }
  assert.deepEqual(selectMeetingCaptureAdapter({
    nativeFeatureEnabled: true,
    runtime: iosRuntime,
    capabilities,
  }), { adapter: 'ios_native', reason: 'native_selected' })

  assert.equal(selectMeetingCaptureAdapter({
    nativeFeatureEnabled: false,
    runtime: iosRuntime,
    capabilities,
  }).adapter, 'web_audio_worklet')
  assert.equal(selectMeetingCaptureAdapter({
    nativeFeatureEnabled: true,
    runtime: { native: false, platform: 'web', pluginAvailable: false },
    capabilities,
  }).adapter, 'web_audio_worklet')
  assert.equal(selectMeetingCaptureAdapter({
    nativeFeatureEnabled: true,
    runtime: { native: true, platform: 'android', pluginAvailable: true },
    capabilities,
  }).adapter, 'web_audio_worklet')
  assert.equal(selectMeetingCaptureAdapter({
    nativeFeatureEnabled: true,
    runtime: { native: true, platform: 'ios', pluginAvailable: false },
    capabilities,
  }).adapter, 'web_audio_worklet')
  assert.equal(selectMeetingCaptureAdapter({
    nativeFeatureEnabled: true,
    runtime: iosRuntime,
    capabilities: { audio: { capture_adapters: { ios_native: { available: false } } } },
  }).adapter, 'web_audio_worklet')
})

test('adapter selection never uses a user agent heuristic', () => {
  const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'captureAdapter.ts'), 'utf8')
  assert.doesNotMatch(source, /userAgent|navigator\.platform|iPhone|iPad/)
})

test('web adapter preserves explicit AudioWorklet start and reports a bounded capture checkpoint', async () => {
  let captureOptions: MeetingAudioCaptureOptions | undefined
  const calls: string[] = []
  const adapter = new WebAudioWorkletCaptureAdapter(
    { onChunk: () => calls.push('chunk') },
    (options) => {
      captureOptions = options
      return {
        start: async () => { calls.push('start') },
        pause: async () => { calls.push('pause') },
        resume: async () => { calls.push('resume') },
        stop: async () => { calls.push('stop') },
      }
    },
  )
  let progressSamples = 0
  await adapter.addListener('capture.progress', (event) => { progressSamples = event.recordedThroughSample })

  const prepared = await adapter.prepare({
    meetingId: 'meeting-web-1',
    streamEpoch: 3,
    audioConfig: { encoding: 'pcm_s16le', sampleRate: 16_000, channels: 1 },
  })
  assert.equal(prepared.state, 'prepared')
  assert.deepEqual(calls, [])

  await adapter.start()
  captureOptions?.onChunk(new ArrayBuffer(640), 10)
  assert.deepEqual(calls, ['start', 'chunk'])
  assert.equal(progressSamples, 320)
  assert.equal(adapter.supportsBackgroundCapture, false)
  assert.equal((await adapter.getCheckpoints()).capture.recordedThroughSample, 320)
  assert.equal(await adapter.getLocalPlaybackAsset(), null)
  await assert.rejects(() => adapter.discardLocalCapture(false), /不能清理/)
  await adapter.stop()
  assert.deepEqual(calls, ['start', 'chunk', 'stop'])
})

function nativeStatus(state: MeetingCaptureStatus['state']): MeetingCaptureStatus {
  return {
    schemaVersion: MEETING_NATIVE_CAPTURE_SCHEMA_VERSION,
    adapter: 'ios_native',
    state,
    meetingId: 'meeting-native-1',
    captureId: 'capture-native-1',
    streamEpoch: 1,
    recordedThroughSample: 16_000,
    lastSealedSequence: 0,
    manifestRevision: 1,
    pendingUploadCount: 1,
    localPlaybackReady: state === 'stopped',
    interruptionReason: null,
    errorCode: null,
  }
}

const checkpoints: MeetingCaptureCheckpoints = {
  capture: { recordedThroughSample: 16_000, lastSealedSequence: 0, manifestRevision: 1 },
  ingest: { highestUploadedSequence: -1, highestContiguousSequence: -1, persistedThroughSample: 0, missingSequenceRanges: [] },
  realtime: { streamEpoch: 1, consumedThroughSample: 0, stableOrdinal: 0, eventCursor: 0 },
  finalization: { sealedThroughSample: 16_000, ingestComplete: false, localPlaybackReady: true, serverPlaybackState: 'pending_upload', postprocessState: 'not_started' },
}

test('iOS adapter forwards the frozen plugin contract without exposing a file path', async () => {
  const calls: string[] = []
  const plugin = {
    prepare: async () => { calls.push('prepare'); return { status: nativeStatus('prepared') } },
    start: async () => { calls.push('start'); return { status: nativeStatus('recording') } },
    pause: async () => ({ status: nativeStatus('paused') }),
    resume: async () => ({ status: nativeStatus('recording') }),
    stop: async () => ({
      status: nativeStatus('stopped'),
      playbackAsset: { handle: 'capture-asset:capture-native-1', mediaType: 'audio/wav', durationMs: 1_000 },
    }),
    getStatus: async () => ({ status: nativeStatus('recording') }),
    getCheckpoints: async () => ({ checkpoints }),
    getLocalPlaybackAsset: async () => ({ playbackAsset: null }),
    retryPendingUploads: async () => ({ status: nativeStatus('recording') }),
    recoverPendingCaptures: async () => ({ captures: [] }),
    rollover: async () => ({
      rollover: {
        captureId: 'capture-native-1',
        previousEpoch: 1,
        streamEpoch: 2,
        streamTicket: 'stream-ticket',
        streamTicketExpiresAt: '2026-07-15T01:00:00Z',
        wsUrl: 'wss://example.test/ws',
        lastAckedSequence: 0,
        captureOffsetMs: 0,
        reconnectWindowSeconds: 120,
        replayed: false,
      },
    }),
    playLocalPlayback: async () => ({ playback: { handle: null, source: 'none', positionMs: 0, durationMs: 0, playing: false, serverReady: false } }),
    pausePlayback: async () => ({ playback: { handle: null, source: 'none', positionMs: 0, durationMs: 0, playing: false, serverReady: false } }),
    resumePlayback: async () => ({ playback: { handle: null, source: 'none', positionMs: 0, durationMs: 0, playing: true, serverReady: false } }),
    seekPlayback: async () => ({ playback: { handle: null, source: 'none', positionMs: 0, durationMs: 0, playing: false, serverReady: false } }),
    getPlaybackStatus: async () => ({ playback: { handle: null, source: 'none', positionMs: 0, durationMs: 0, playing: false, serverReady: false } }),
    switchToServerPlayback: async () => ({ playback: { handle: null, source: 'server', positionMs: 0, durationMs: 0, playing: false, serverReady: true } }),
    discardLocalCapture: async () => ({
      discarded: true,
      cleanupReceipt: {
        schemaVersion: 'siq.meeting.native_capture.cleanup_receipt.v1',
        captureId: 'capture-native-1',
        meetingId: 'meeting-native-1',
        streamEpoch: 1,
        recordedThroughSample: 16_000,
        manifestRevision: 1,
        serverWavSha256: 'a'.repeat(64),
        serverWavByteSize: 32_044,
        verifiedAt: '2026-07-15T00:00:00Z',
      },
    }),
    addListener: async () => ({ remove: async () => undefined }),
  } as MeetingCapturePluginBridge
  const adapter = new IosNativeCaptureAdapter(plugin)

  await adapter.prepare({
    meetingId: 'meeting-native-1',
    captureId: 'capture-native-1',
    captureToken: 'not-logged-or-stored-in-webview',
    deviceInstallationId: 'device-installation-web-1',
    apiBaseUrl: 'https://example.test/api/meetings/v1',
    streamEpoch: 1,
    audioConfig: { encoding: 'pcm_s16le', sampleRate: 16_000, channels: 1 },
  })
  await adapter.start()
  const stopped = await adapter.stop()

  assert.equal(adapter.supportsBackgroundCapture, true)
  assert.deepEqual(calls, ['prepare', 'start'])
  assert.equal(stopped.playbackAsset?.handle, 'capture-asset:capture-native-1')
  assert.equal('filePath' in (stopped.playbackAsset || {}), false)
})
