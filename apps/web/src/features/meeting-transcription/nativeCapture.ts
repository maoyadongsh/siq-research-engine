export const MEETING_CAPTURE_PLUGIN_NAME = 'MeetingCapture'
export const MEETING_NATIVE_CAPTURE_SCHEMA_VERSION = 'siq.meeting.native_capture.v1'

export type MeetingCaptureAdapterId = 'web_audio_worklet' | 'ios_native'
export type MeetingCaptureLifecycleState =
  | 'idle'
  | 'prepared'
  | 'recording'
  | 'paused'
  | 'interrupted'
  | 'stopping'
  | 'stopped'
  | 'error'

export interface MeetingCaptureAudioConfig {
  encoding: 'pcm_s16le'
  sampleRate: 16_000
  channels: 1
  batchDurationMs?: number
}

export interface MeetingCapturePrepareOptions {
  meetingId: string
  audioConfig: MeetingCaptureAudioConfig
  streamEpoch?: number
}

export interface MeetingNativeCapturePrepareOptions extends MeetingCapturePrepareOptions {
  captureId: string
  captureToken: string
  deviceInstallationId: string
  apiBaseUrl: string
  streamEpoch: number
  maxBatchBytes?: number
  maxTotalBytes?: number
  maxDurationSeconds?: number
}

export interface MeetingCaptureStatus {
  schemaVersion: typeof MEETING_NATIVE_CAPTURE_SCHEMA_VERSION
  adapter: MeetingCaptureAdapterId
  state: MeetingCaptureLifecycleState
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

export interface MeetingCaptureCheckpoint {
  recordedThroughSample: number
  lastSealedSequence: number
  manifestRevision: number
}

export interface MeetingIngestCheckpoint {
  highestUploadedSequence: number
  highestContiguousSequence: number
  persistedThroughSample: number
  missingSequenceRanges: Array<{ start: number; end: number }>
}

export interface MeetingRealtimeCheckpoint {
  streamEpoch: number
  consumedThroughSample: number | null
  stableOrdinal: number
  eventCursor: number | null
}

export interface MeetingFinalizationCheckpoint {
  sealedThroughSample: number | null
  ingestComplete: boolean
  localPlaybackReady: boolean
  serverPlaybackState: 'not_ready' | 'pending_upload' | 'pending_packaging' | 'packaging' | 'ready' | 'failed'
  postprocessState: string
}

export interface MeetingCaptureCheckpoints {
  capture: MeetingCaptureCheckpoint
  ingest: MeetingIngestCheckpoint
  realtime: MeetingRealtimeCheckpoint
  finalization: MeetingFinalizationCheckpoint
}

export interface MeetingLocalPlaybackAsset {
  /** Opaque native handle. It must never contain an absolute sandbox path. */
  handle: string
  webUrl?: string
  mediaType: string
  durationMs: number
}

export interface MeetingCaptureProgressEvent {
  recordedThroughSample: number
  manifestRevision: number
  pendingUploadCount: number
}

export interface MeetingCaptureBatchEvent {
  streamEpoch: number
  sequence: number
  firstSample: number
  sampleCount: number
  manifestRevision: number
}

export interface MeetingCaptureErrorEvent {
  code: string
  recoverable: boolean
}

export interface MeetingCaptureEventMap {
  'capture.started': MeetingCaptureStatus
  'capture.progress': MeetingCaptureProgressEvent
  'capture.interrupted': { reason: string; startSample: number }
  'capture.resumed': MeetingCaptureStatus
  'batch.sealed': MeetingCaptureBatchEvent
  'batch.uploaded': MeetingCaptureBatchEvent
  'capture.stopped': MeetingCaptureStatus
  'local.playback.ready': MeetingLocalPlaybackAsset
  'capture.error': MeetingCaptureErrorEvent
}

export type MeetingCaptureEventName = keyof MeetingCaptureEventMap

export interface MeetingCaptureListenerHandle {
  remove(): Promise<void>
}

export interface MeetingCapturePluginBridge {
  prepare(options: MeetingNativeCapturePrepareOptions): Promise<{ status: MeetingCaptureStatus }>
  start(): Promise<{ status: MeetingCaptureStatus }>
  pause(options?: { reason?: string }): Promise<{ status: MeetingCaptureStatus }>
  resume(): Promise<{ status: MeetingCaptureStatus }>
  stop(): Promise<{ status: MeetingCaptureStatus; playbackAsset: MeetingLocalPlaybackAsset | null }>
  getStatus(): Promise<{ status: MeetingCaptureStatus }>
  getCheckpoints(): Promise<{ checkpoints: MeetingCaptureCheckpoints }>
  getLocalPlaybackAsset(): Promise<{ playbackAsset: MeetingLocalPlaybackAsset | null }>
  retryPendingUploads(): Promise<{ status: MeetingCaptureStatus }>
  discardLocalCapture(options: { confirmedServerComplete: boolean }): Promise<{ discarded: boolean }>
  addListener<Name extends MeetingCaptureEventName>(
    eventName: Name,
    listener: (event: MeetingCaptureEventMap[Name]) => void,
  ): Promise<MeetingCaptureListenerHandle>
}

export interface CapacitorRuntimeLike {
  isNativePlatform(): boolean
  getPlatform(): string
  isPluginAvailable(name: string): boolean
}

export interface MeetingNativeRuntimeCapability {
  native: boolean
  platform: string
  pluginAvailable: boolean
}

export function probeMeetingNativeRuntime(runtime?: CapacitorRuntimeLike | null): MeetingNativeRuntimeCapability {
  if (!runtime) return { native: false, platform: 'web', pluginAvailable: false }
  try {
    const native = runtime.isNativePlatform()
    const platform = runtime.getPlatform()
    return {
      native,
      platform,
      pluginAvailable: native && platform === 'ios' && runtime.isPluginAvailable(MEETING_CAPTURE_PLUGIN_NAME),
    }
  } catch {
    return { native: false, platform: 'web', pluginAvailable: false }
  }
}
