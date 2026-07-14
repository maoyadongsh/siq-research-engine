import type {
  MeetingCaptureCheckpoints,
  MeetingCaptureStatus,
  MeetingLocalPlaybackAsset,
} from './nativeCapture'

export type NativeCaptureIngestPhase =
  | 'idle'
  | 'pending_upload'
  | 'syncing'
  | 'verifying'
  | 'complete'
  | 'gap'

export type NativeCaptureRealtimePhase =
  | 'inactive'
  | 'active'
  | 'waiting_for_ingest'
  | 'recovering'

export type NativeCapturePlaybackPhase =
  | 'unavailable'
  | 'local_ready'
  | 'server_processing'
  | 'server_ready'
  | 'failed'

export interface NativeCaptureOperationalState {
  capture: MeetingCaptureStatus['state']
  ingest: NativeCaptureIngestPhase
  realtime: NativeCaptureRealtimePhase
  playback: NativeCapturePlaybackPhase
  postprocess: string
  recordedThroughSample: number
  persistedThroughSample: number
  missingSequenceCount: number
}

function missingSequenceCount(checkpoints: MeetingCaptureCheckpoints) {
  return checkpoints.ingest.missingSequenceRanges.reduce(
    (count, range) => count + Math.max(0, range.end - range.start + 1),
    0,
  )
}

export function deriveNativeCaptureOperationalState(input: {
  status: MeetingCaptureStatus
  checkpoints: MeetingCaptureCheckpoints
  online: boolean
}): NativeCaptureOperationalState {
  const { status, checkpoints } = input
  const missing = missingSequenceCount(checkpoints)
  const stopped = status.state === 'stopped' || status.state === 'stopping'
  const terminal = stopped || status.state === 'error'
  const pendingSamples = Math.max(
    0,
    checkpoints.capture.recordedThroughSample - checkpoints.ingest.persistedThroughSample,
  )

  let ingest: NativeCaptureIngestPhase = 'idle'
  if (checkpoints.finalization.ingestComplete) ingest = 'complete'
  else if (missing > 0 && terminal) ingest = 'gap'
  else if (missing > 0 || status.pendingUploadCount > 0 || pendingSamples > 0) {
    ingest = input.online ? 'syncing' : 'pending_upload'
  } else if (stopped) ingest = 'verifying'

  let realtime: NativeCaptureRealtimePhase = 'inactive'
  if (!terminal) {
    if (missing > 0 || pendingSamples > 0) realtime = 'waiting_for_ingest'
    else if (
      checkpoints.realtime.streamEpoch !== status.streamEpoch
      || checkpoints.realtime.consumedThroughSample < checkpoints.ingest.persistedThroughSample
    ) realtime = 'recovering'
    else realtime = 'active'
  }

  let playback: NativeCapturePlaybackPhase = 'unavailable'
  if (checkpoints.finalization.serverPlaybackState === 'ready') playback = 'server_ready'
  else if (checkpoints.finalization.localPlaybackReady || status.localPlaybackReady) playback = 'local_ready'
  else if (checkpoints.finalization.serverPlaybackState === 'failed') playback = 'failed'
  else if (stopped || checkpoints.finalization.sealedThroughSample > 0) playback = 'server_processing'

  return {
    capture: status.state,
    ingest,
    realtime,
    playback,
    postprocess: checkpoints.finalization.postprocessState,
    recordedThroughSample: checkpoints.capture.recordedThroughSample,
    persistedThroughSample: checkpoints.ingest.persistedThroughSample,
    missingSequenceCount: missing,
  }
}

export type NativeCaptureRecoveryStep =
  | 'status'
  | 'checkpoint'
  | 'retry_uploads'
  | 'checkpoint_after_retry'
  | 'rollover'

export type NativeCaptureRecoveryOutcome =
  | 'rolled_over'
  | 'waiting_for_upload'
  | 'stopped'
  | 'not_recording'

export interface NativeCaptureRecoveryDependencies<RolloverResult> {
  getStatus(): Promise<MeetingCaptureStatus>
  getCheckpoints(): Promise<MeetingCaptureCheckpoints>
  retryPendingUploads(): Promise<MeetingCaptureStatus>
  rollover(input: {
    status: MeetingCaptureStatus
    checkpoints: MeetingCaptureCheckpoints
  }): Promise<RolloverResult>
}

export interface NativeCaptureRecoveryResult<RolloverResult> {
  status: MeetingCaptureStatus
  checkpoints: MeetingCaptureCheckpoints
  steps: NativeCaptureRecoveryStep[]
  outcome: NativeCaptureRecoveryOutcome
  rollover: RolloverResult | null
}

export async function recoverNativeCaptureAfterForeground<RolloverResult>(
  dependencies: NativeCaptureRecoveryDependencies<RolloverResult>,
): Promise<NativeCaptureRecoveryResult<RolloverResult>> {
  const steps: NativeCaptureRecoveryStep[] = []
  let status = await dependencies.getStatus()
  steps.push('status')
  let checkpoints = await dependencies.getCheckpoints()
  steps.push('checkpoint')

  const terminal = status.state === 'stopped' || status.state === 'stopping'
  if (terminal) {
    return { status, checkpoints, steps, outcome: 'stopped', rollover: null }
  }
  if (!['recording', 'paused', 'interrupted'].includes(status.state)) {
    return { status, checkpoints, steps, outcome: 'not_recording', rollover: null }
  }

  const needsUpload = () => (
    status.pendingUploadCount > 0
    || missingSequenceCount(checkpoints) > 0
    || checkpoints.ingest.persistedThroughSample < checkpoints.capture.recordedThroughSample
  )
  if (needsUpload()) {
    status = await dependencies.retryPendingUploads()
    steps.push('retry_uploads')
    checkpoints = await dependencies.getCheckpoints()
    steps.push('checkpoint_after_retry')
  }
  if (needsUpload()) {
    return { status, checkpoints, steps, outcome: 'waiting_for_upload', rollover: null }
  }

  const rollover = await dependencies.rollover({ status, checkpoints })
  steps.push('rollover')
  return { status, checkpoints, steps, outcome: 'rolled_over', rollover }
}

export type NativePlaybackSource = 'none' | 'local' | 'server'

export interface NativePlaybackState {
  source: NativePlaybackSource
  phase: 'unavailable' | 'local_ready' | 'switching_to_server' | 'server_ready'
  localAsset: MeetingLocalPlaybackAsset | null
  serverUrl: string | null
  pendingServerUrl: string | null
  currentTimeSeconds: number
  resumeAfterSwitch: boolean
  switchError: string | null
}

export type NativePlaybackEvent =
  | { type: 'local_ready'; asset: MeetingLocalPlaybackAsset }
  | { type: 'position'; currentTimeSeconds: number; playing: boolean }
  | { type: 'server_ready'; url: string }
  | { type: 'server_switch_succeeded' }
  | { type: 'server_switch_failed'; message: string }

export function createNativePlaybackState(): NativePlaybackState {
  return {
    source: 'none',
    phase: 'unavailable',
    localAsset: null,
    serverUrl: null,
    pendingServerUrl: null,
    currentTimeSeconds: 0,
    resumeAfterSwitch: false,
    switchError: null,
  }
}

export function reduceNativePlaybackState(
  state: NativePlaybackState,
  event: NativePlaybackEvent,
): NativePlaybackState {
  if (event.type === 'local_ready') {
    return {
      ...state,
      source: state.source === 'server' ? 'server' : 'local',
      phase: state.source === 'server' ? 'server_ready' : 'local_ready',
      localAsset: event.asset,
      switchError: null,
    }
  }
  if (event.type === 'position') {
    return {
      ...state,
      currentTimeSeconds: Math.max(0, event.currentTimeSeconds),
      resumeAfterSwitch: event.playing,
    }
  }
  if (event.type === 'server_ready') {
    return {
      ...state,
      phase: 'switching_to_server',
      pendingServerUrl: event.url,
      switchError: null,
    }
  }
  if (event.type === 'server_switch_succeeded') {
    if (!state.pendingServerUrl) return state
    return {
      ...state,
      source: 'server',
      phase: 'server_ready',
      serverUrl: state.pendingServerUrl,
      pendingServerUrl: null,
      switchError: null,
    }
  }
  return {
    ...state,
    source: state.localAsset ? 'local' : 'none',
    phase: state.localAsset ? 'local_ready' : 'unavailable',
    pendingServerUrl: null,
    switchError: event.message,
  }
}
