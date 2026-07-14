import type { PluginListenerHandle } from '@capacitor/core'

export const MEETING_NATIVE_CAPTURE_SCHEMA_VERSION = 'siq.meeting.native_capture.v1' as const

export type CaptureState =
  | 'idle'
  | 'prepared'
  | 'recording'
  | 'paused'
  | 'interrupted'
  | 'stopping'
  | 'stopped'
  | 'error'

export interface CaptureStatus {
  schemaVersion: typeof MEETING_NATIVE_CAPTURE_SCHEMA_VERSION
  adapter: 'ios_native'
  state: CaptureState
  meetingId: string | null
  captureId: string | null
  streamEpoch: number
  recordedThroughSample: number
  lastSealedSequence: number
  manifestRevision: number
  pendingUploadCount: number
  localPlaybackReady: boolean
  interruptionReason: string | null
  errorCode: string | null
}

export interface PrepareOptions {
  meetingId: string
  captureId: string
  captureToken: string
  deviceInstallationId: string
  apiBaseUrl: string
  streamEpoch: number
  audioConfig: {
    encoding: 'pcm_s16le'
    sampleRate: 16000
    channels: 1
    batchDurationMs?: number
  }
  maxBatchBytes?: number
  maxTotalBytes?: number
  maxDurationSeconds?: number
}

export interface CaptureCheckpoints {
  capture: {
    recordedThroughSample: number
    lastSealedSequence: number
    manifestRevision: number
  }
  ingest: {
    highestUploadedSequence: number
    highestContiguousSequence: number
    persistedThroughSample: number
    missingSequenceRanges: Array<{ start: number; end: number }>
  }
  realtime: {
    streamEpoch: number
    lastAckedSequence: number
    consumedThroughSample: number | null
    stableOrdinal: number
    eventCursor: number | null
  }
  finalization: {
    sealedThroughSample: number | null
    ingestComplete: boolean
    localPlaybackReady: boolean
    serverPlaybackState: string
    postprocessState: string
  }
  authority: {
    capture: 'local_manifest'
    ingest: 'authenticated_server_checkpoint'
    realtime: 'authenticated_server_checkpoint'
    finalization: 'authenticated_server_checkpoint'
  }
}

export interface LocalPlaybackAsset {
  handle: string
  mediaType: 'audio/wav'
  durationMs: number
}

export interface PlaybackStatus {
  handle: string | null
  source: 'none' | 'local' | 'server'
  positionMs: number
  durationMs: number
  playing: boolean
  serverReady: boolean
}

export interface RolloverResult {
  captureId: string
  previousEpoch: number
  streamEpoch: number
  streamTicket: string
  streamTicketExpiresAt: string
  wsUrl: string
  lastAckedSequence: number
  captureOffsetMs: number
  reconnectWindowSeconds: number
  replayed: boolean
}

export interface MeetingCaptureEventMap {
  'capture.started': CaptureStatus
  'capture.progress': {
    recordedThroughSample: number
    manifestRevision: number
    pendingUploadCount: number
  }
  'capture.interrupted': { reason: string; startSample: number }
  'capture.resumed': CaptureStatus
  'batch.sealed': {
    streamEpoch: number
    sequence: number
    firstSample: number
    sampleCount: number
    manifestRevision: number
  }
  'batch.uploaded': MeetingCaptureEventMap['batch.sealed']
  'capture.stopped': CaptureStatus
  'capture.checkpoint': CaptureCheckpoints
  'capture.synced': {
    captureId: string
    ingestComplete: boolean
    serverPlaybackState: string
  }
  'local.playback.ready': LocalPlaybackAsset
  'capture.error': { code: string; recoverable: boolean }
}

export interface MeetingCapturePlugin {
  prepare(options: PrepareOptions): Promise<{ status: CaptureStatus }>
  start(): Promise<{ status: CaptureStatus }>
  pause(options?: { reason?: string }): Promise<{ status: CaptureStatus }>
  resume(): Promise<{ status: CaptureStatus }>
  stop(): Promise<{ status: CaptureStatus; playbackAsset: LocalPlaybackAsset | null }>
  getStatus(): Promise<{ status: CaptureStatus }>
  getCheckpoints(): Promise<{ checkpoints: CaptureCheckpoints }>
  getLocalPlaybackAsset(): Promise<{ playbackAsset: LocalPlaybackAsset | null }>
  retryPendingUploads(): Promise<{ status: CaptureStatus }>
  recoverPendingCaptures(): Promise<{ captures: CaptureStatus[] }>
  rollover(): Promise<{ rollover: RolloverResult }>
  playLocalPlayback(options: { handle: string }): Promise<{ playback: PlaybackStatus }>
  pausePlayback(): Promise<{ playback: PlaybackStatus }>
  seekPlayback(options: { positionMs: number }): Promise<{ playback: PlaybackStatus }>
  getPlaybackStatus(): Promise<{ playback: PlaybackStatus }>
  switchToServerPlayback(options: {
    handle: string
    serverUrl: string
  }): Promise<{ playback: PlaybackStatus }>
  discardLocalCapture(options: { confirmedServerComplete: boolean }): Promise<{ discarded: boolean }>
  addListener<Name extends keyof MeetingCaptureEventMap>(
    eventName: Name,
    listener: (event: MeetingCaptureEventMap[Name]) => void,
  ): Promise<PluginListenerHandle>
}
