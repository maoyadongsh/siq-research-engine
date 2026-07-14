import {
  createMeetingAudioCapture,
  type MeetingAudioCapture,
  type MeetingAudioCaptureOptions,
} from './audioCapture'
import {
  MEETING_NATIVE_CAPTURE_SCHEMA_VERSION,
  type MeetingCaptureAdapterId,
  type MeetingCaptureCheckpoints,
  type MeetingCaptureEventMap,
  type MeetingCaptureEventName,
  type MeetingCaptureListenerHandle,
  type MeetingCapturePluginBridge,
  type MeetingCapturePrepareOptions,
  type MeetingCaptureStatus,
  type MeetingLocalPlaybackAsset,
  type MeetingNativeCapturePrepareOptions,
  type MeetingNativeRuntimeCapability,
} from './nativeCapture'

export interface MeetingCaptureAdapterCapability {
  available: boolean
  background_recording?: boolean
  adapter?: MeetingCaptureAdapterId
  requires_native_runtime?: boolean
  web_background_recording_supported?: boolean
  configuration_errors?: string[]
}

export interface MeetingCaptureCapabilityEnvelope {
  audio?: {
    capture_adapters?: {
      web_audio_worklet?: MeetingCaptureAdapterCapability
      ios_native?: MeetingCaptureAdapterCapability
    }
  }
}

export type MeetingCaptureSelectionReason =
  | 'native_selected'
  | 'native_frontend_flag_disabled'
  | 'not_native_runtime'
  | 'not_ios_runtime'
  | 'native_plugin_unavailable'
  | 'native_backend_capability_unavailable'

export interface MeetingCaptureAdapterDecision {
  adapter: MeetingCaptureAdapterId
  reason: MeetingCaptureSelectionReason
}

export function selectMeetingCaptureAdapter(input: {
  nativeFeatureEnabled: boolean
  runtime: MeetingNativeRuntimeCapability
  capabilities?: MeetingCaptureCapabilityEnvelope | null
}): MeetingCaptureAdapterDecision {
  if (!input.nativeFeatureEnabled) {
    return { adapter: 'web_audio_worklet', reason: 'native_frontend_flag_disabled' }
  }
  if (!input.runtime.native) return { adapter: 'web_audio_worklet', reason: 'not_native_runtime' }
  if (input.runtime.platform !== 'ios') return { adapter: 'web_audio_worklet', reason: 'not_ios_runtime' }
  if (!input.runtime.pluginAvailable) return { adapter: 'web_audio_worklet', reason: 'native_plugin_unavailable' }
  if (input.capabilities?.audio?.capture_adapters?.ios_native?.available !== true) {
    return { adapter: 'web_audio_worklet', reason: 'native_backend_capability_unavailable' }
  }
  return { adapter: 'ios_native', reason: 'native_selected' }
}

export interface MeetingCaptureAdapter<PrepareOptions extends MeetingCapturePrepareOptions = MeetingCapturePrepareOptions> {
  readonly id: MeetingCaptureAdapterId
  readonly supportsBackgroundCapture: boolean
  prepare(options: PrepareOptions): Promise<MeetingCaptureStatus>
  start(): Promise<MeetingCaptureStatus>
  pause(reason?: string): Promise<MeetingCaptureStatus>
  resume(): Promise<MeetingCaptureStatus>
  stop(): Promise<{ status: MeetingCaptureStatus; playbackAsset: MeetingLocalPlaybackAsset | null }>
  getStatus(): Promise<MeetingCaptureStatus>
  getCheckpoints(): Promise<MeetingCaptureCheckpoints>
  getLocalPlaybackAsset(): Promise<MeetingLocalPlaybackAsset | null>
  retryPendingUploads(): Promise<MeetingCaptureStatus>
  discardLocalCapture(confirmedServerComplete: boolean): Promise<boolean>
  addListener<Name extends MeetingCaptureEventName>(
    eventName: Name,
    listener: (event: MeetingCaptureEventMap[Name]) => void,
  ): Promise<MeetingCaptureListenerHandle>
}

type MeetingAudioCaptureFactory = (options: MeetingAudioCaptureOptions) => MeetingAudioCapture

export class WebAudioWorkletCaptureAdapter implements MeetingCaptureAdapter {
  readonly id = 'web_audio_worklet' as const
  readonly supportsBackgroundCapture = false

  private readonly capture: MeetingAudioCapture
  private readonly listeners = new Map<MeetingCaptureEventName, Set<(event: unknown) => void>>()
  private meetingId: string | null = null
  private state: MeetingCaptureStatus['state'] = 'idle'
  private streamEpoch = 0
  private recordedThroughSample = 0
  private interruptionReason: string | null = null
  private errorCode: string | null = null

  constructor(
    options: MeetingAudioCaptureOptions,
    captureFactory: MeetingAudioCaptureFactory = createMeetingAudioCapture,
  ) {
    this.capture = captureFactory({
      ...options,
      onChunk: (pcm, capturedAt) => {
        this.recordedThroughSample += Math.floor(pcm.byteLength / 2)
        options.onChunk(pcm, capturedAt)
        this.emit('capture.progress', {
          recordedThroughSample: this.recordedThroughSample,
          manifestRevision: 0,
          pendingUploadCount: 0,
        })
      },
    })
  }

  async prepare(options: MeetingCapturePrepareOptions) {
    this.meetingId = options.meetingId
    this.streamEpoch = options.streamEpoch ?? 0
    this.state = 'prepared'
    this.interruptionReason = null
    this.errorCode = null
    return this.snapshot()
  }

  async start() {
    this.requirePrepared()
    await this.capture.start()
    this.state = 'recording'
    const status = this.snapshot()
    this.emit('capture.started', status)
    return status
  }

  async pause(reason = 'user') {
    this.requirePrepared()
    await this.capture.pause()
    this.state = reason === 'user' ? 'paused' : 'interrupted'
    this.interruptionReason = reason === 'user' ? null : reason
    if (reason !== 'user') {
      this.emit('capture.interrupted', { reason, startSample: this.recordedThroughSample })
    }
    return this.snapshot()
  }

  async resume() {
    this.requirePrepared()
    if (this.capture.recover) await this.capture.recover()
    else await this.capture.resume()
    this.state = 'recording'
    this.interruptionReason = null
    const status = this.snapshot()
    this.emit('capture.resumed', status)
    return status
  }

  async stop() {
    if (this.state !== 'idle' && this.state !== 'stopped') {
      this.state = 'stopping'
      await this.capture.stop()
      this.state = 'stopped'
    }
    const status = this.snapshot()
    this.emit('capture.stopped', status)
    return { status, playbackAsset: null }
  }

  async getStatus() {
    return this.snapshot()
  }

  async getCheckpoints(): Promise<MeetingCaptureCheckpoints> {
    return {
      capture: {
        recordedThroughSample: this.recordedThroughSample,
        lastSealedSequence: -1,
        manifestRevision: 0,
      },
      ingest: {
        highestUploadedSequence: -1,
        highestContiguousSequence: -1,
        persistedThroughSample: 0,
        missingSequenceRanges: [],
      },
      realtime: {
        streamEpoch: this.streamEpoch,
        consumedThroughSample: 0,
        stableOrdinal: 0,
        eventCursor: 0,
      },
      finalization: {
        sealedThroughSample: 0,
        ingestComplete: false,
        localPlaybackReady: false,
        serverPlaybackState: 'not_ready',
        postprocessState: 'not_started',
      },
    }
  }

  async getLocalPlaybackAsset() {
    return null
  }

  async retryPendingUploads() {
    return this.snapshot()
  }

  async discardLocalCapture(confirmedServerComplete: boolean) {
    if (!confirmedServerComplete) throw new Error('服务端尚未确认完整接收，不能清理本地采集状态。')
    return true
  }

  async addListener<Name extends MeetingCaptureEventName>(
    eventName: Name,
    listener: (event: MeetingCaptureEventMap[Name]) => void,
  ): Promise<MeetingCaptureListenerHandle> {
    const callbacks = this.listeners.get(eventName) ?? new Set<(event: unknown) => void>()
    callbacks.add(listener as (event: unknown) => void)
    this.listeners.set(eventName, callbacks)
    return {
      remove: async () => {
        callbacks.delete(listener as (event: unknown) => void)
        if (!callbacks.size) this.listeners.delete(eventName)
      },
    }
  }

  private emit<Name extends MeetingCaptureEventName>(eventName: Name, event: MeetingCaptureEventMap[Name]) {
    for (const listener of this.listeners.get(eventName) ?? []) listener(event)
  }

  private requirePrepared() {
    if (!this.meetingId || this.state === 'idle') throw new Error('会议采集适配器尚未 prepare。')
  }

  private snapshot(): MeetingCaptureStatus {
    return {
      schemaVersion: MEETING_NATIVE_CAPTURE_SCHEMA_VERSION,
      adapter: this.id,
      state: this.state,
      meetingId: this.meetingId,
      captureId: null,
      streamEpoch: this.streamEpoch,
      recordedThroughSample: this.recordedThroughSample,
      lastSealedSequence: -1,
      manifestRevision: 0,
      pendingUploadCount: 0,
      localPlaybackReady: false,
      interruptionReason: this.interruptionReason,
      errorCode: this.errorCode,
    }
  }
}

export class IosNativeCaptureAdapter implements MeetingCaptureAdapter<MeetingNativeCapturePrepareOptions> {
  readonly id = 'ios_native' as const
  readonly supportsBackgroundCapture = true
  private readonly plugin: MeetingCapturePluginBridge

  constructor(plugin: MeetingCapturePluginBridge) {
    this.plugin = plugin
  }

  async prepare(options: MeetingNativeCapturePrepareOptions) {
    return (await this.plugin.prepare(options)).status
  }

  async start() {
    return (await this.plugin.start()).status
  }

  async pause(reason?: string) {
    return (await this.plugin.pause({ reason })).status
  }

  async resume() {
    return (await this.plugin.resume()).status
  }

  async stop() {
    return this.plugin.stop()
  }

  async getStatus() {
    return (await this.plugin.getStatus()).status
  }

  async getCheckpoints() {
    return (await this.plugin.getCheckpoints()).checkpoints
  }

  async getLocalPlaybackAsset() {
    return (await this.plugin.getLocalPlaybackAsset()).playbackAsset
  }

  async retryPendingUploads() {
    return (await this.plugin.retryPendingUploads()).status
  }

  async discardLocalCapture(confirmedServerComplete: boolean) {
    return (await this.plugin.discardLocalCapture({ confirmedServerComplete })).discarded
  }

  async addListener<Name extends MeetingCaptureEventName>(
    eventName: Name,
    listener: (event: MeetingCaptureEventMap[Name]) => void,
  ) {
    return this.plugin.addListener(eventName, listener)
  }
}
