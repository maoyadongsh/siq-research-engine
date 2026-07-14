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

const MAX_SAFE_COUNT = Number.MAX_SAFE_INTEGER

function isNonNegativeSafeInteger(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 0
}

function missingSequenceCount(checkpoints: MeetingCaptureCheckpoints) {
  let count = 0
  for (const range of checkpoints.ingest.missingSequenceRanges) {
    if (
      !isNonNegativeSafeInteger(range.start)
      || !isNonNegativeSafeInteger(range.end)
      || range.end < range.start
    ) {
      return MAX_SAFE_COUNT
    }
    const size = range.end - range.start + 1
    if (!Number.isSafeInteger(size) || size > MAX_SAFE_COUNT - count) return MAX_SAFE_COUNT
    count += size
  }
  return count
}

function pendingSampleCount(checkpoints: MeetingCaptureCheckpoints): number {
  const recorded = checkpoints.capture.recordedThroughSample
  const persisted = checkpoints.ingest.persistedThroughSample
  if (!isNonNegativeSafeInteger(recorded) || !isNonNegativeSafeInteger(persisted)) {
    return MAX_SAFE_COUNT
  }
  return recorded > persisted ? recorded - persisted : 0
}

function hasPendingUploads(status: MeetingCaptureStatus): boolean {
  return !isNonNegativeSafeInteger(status.pendingUploadCount) || status.pendingUploadCount > 0
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
  const pendingSamples = pendingSampleCount(checkpoints)
  const pendingUploads = hasPendingUploads(status)

  let ingest: NativeCaptureIngestPhase = 'idle'
  if (checkpoints.finalization.ingestComplete) ingest = 'complete'
  else if (
    missing > 0
    && (status.state === 'stopped' || status.state === 'error')
    && !pendingUploads
  ) ingest = 'gap'
  else if (missing > 0 || pendingUploads || pendingSamples > 0) {
    ingest = input.online ? 'syncing' : 'pending_upload'
  } else if (stopped) ingest = 'verifying'

  let realtime: NativeCaptureRealtimePhase = 'inactive'
  if (!terminal) {
    if (missing > 0 || pendingSamples > 0) realtime = 'waiting_for_ingest'
    else if (
      checkpoints.realtime.streamEpoch !== status.streamEpoch
      || (
        checkpoints.realtime.consumedThroughSample !== null
        && checkpoints.realtime.consumedThroughSample < checkpoints.ingest.persistedThroughSample
      )
    ) realtime = 'recovering'
    else realtime = 'active'
  }

  let playback: NativeCapturePlaybackPhase = 'unavailable'
  if (checkpoints.finalization.serverPlaybackState === 'ready') playback = 'server_ready'
  else if (checkpoints.finalization.localPlaybackReady || status.localPlaybackReady) playback = 'local_ready'
  else if (checkpoints.finalization.serverPlaybackState === 'failed') playback = 'failed'
  else if (
    stopped
    || (
      checkpoints.finalization.sealedThroughSample !== null
      && checkpoints.finalization.sealedThroughSample > 0
    )
  ) playback = 'server_processing'

  return {
    capture: status.state,
    ingest,
    realtime,
    playback,
    postprocess: checkpoints.finalization.postprocessState,
    recordedThroughSample: isNonNegativeSafeInteger(checkpoints.capture.recordedThroughSample)
      ? checkpoints.capture.recordedThroughSample
      : 0,
    persistedThroughSample: isNonNegativeSafeInteger(checkpoints.ingest.persistedThroughSample)
      ? checkpoints.ingest.persistedThroughSample
      : 0,
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

export interface NativeCaptureRecoveryDependencies {
  getStatus(): Promise<MeetingCaptureStatus>
  getCheckpoints(): Promise<MeetingCaptureCheckpoints>
  retryPendingUploads(): Promise<MeetingCaptureStatus>
  rollover(input: {
    status: MeetingCaptureStatus
    checkpoints: MeetingCaptureCheckpoints
  }): Promise<{ streamEpoch: number }>
}

export interface NativeCaptureRecoveryResult {
  status: MeetingCaptureStatus
  checkpoints: MeetingCaptureCheckpoints
  steps: NativeCaptureRecoveryStep[]
  outcome: NativeCaptureRecoveryOutcome
  rollover: { streamEpoch: number } | null
}

export async function recoverNativeCaptureAfterForeground(
  dependencies: NativeCaptureRecoveryDependencies,
): Promise<NativeCaptureRecoveryResult> {
  const steps: NativeCaptureRecoveryStep[] = []
  let status = await dependencies.getStatus()
  steps.push('status')
  let checkpoints = await dependencies.getCheckpoints()
  steps.push('checkpoint')

  const needsUpload = () => (
    hasPendingUploads(status)
    || missingSequenceCount(checkpoints) > 0
    || pendingSampleCount(checkpoints) > 0
  )
  if (status.state === 'stopping') {
    return { status, checkpoints, steps, outcome: 'stopped', rollover: null }
  }
  if (status.state === 'stopped') {
    if (needsUpload()) {
      status = await dependencies.retryPendingUploads()
      steps.push('retry_uploads')
      checkpoints = await dependencies.getCheckpoints()
      steps.push('checkpoint_after_retry')
    }
    return { status, checkpoints, steps, outcome: 'stopped', rollover: null }
  }
  if (!['recording', 'paused', 'interrupted'].includes(status.state)) {
    return { status, checkpoints, steps, outcome: 'not_recording', rollover: null }
  }

  if (needsUpload()) {
    status = await dependencies.retryPendingUploads()
    steps.push('retry_uploads')
    checkpoints = await dependencies.getCheckpoints()
    steps.push('checkpoint_after_retry')
  }
  if (status.state === 'stopped' || status.state === 'stopping') {
    return { status, checkpoints, steps, outcome: 'stopped', rollover: null }
  }
  if (!['recording', 'paused', 'interrupted'].includes(status.state)) {
    return { status, checkpoints, steps, outcome: 'not_recording', rollover: null }
  }
  if (needsUpload()) {
    return { status, checkpoints, steps, outcome: 'waiting_for_upload', rollover: null }
  }

  const rolloverResult = await dependencies.rollover({ status, checkpoints })
  steps.push('rollover')
  if (
    !isNonNegativeSafeInteger(rolloverResult.streamEpoch)
    || rolloverResult.streamEpoch <= status.streamEpoch
    || rolloverResult.streamEpoch <= checkpoints.realtime.streamEpoch
  ) {
    throw new Error('native capture rollover did not advance the stream epoch')
  }
  const rollover = { streamEpoch: rolloverResult.streamEpoch }
  return { status, checkpoints, steps, outcome: 'rolled_over', rollover }
}

export type NativePlaybackSource = 'none' | 'local' | 'server'

export interface NativePlaybackState {
  source: NativePlaybackSource
  phase: 'unavailable' | 'local_ready' | 'switching_to_server' | 'server_ready'
  localAsset: Pick<MeetingLocalPlaybackAsset, 'handle' | 'mediaType' | 'durationMs'> | null
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
    if (!/^capture-asset:[A-Za-z0-9._-]{1,128}$/.test(event.asset.handle)) return state
    const localAsset = {
      handle: event.asset.handle,
      mediaType: event.asset.mediaType,
      durationMs: isNonNegativeSafeInteger(event.asset.durationMs) ? event.asset.durationMs : 0,
    }
    return {
      ...state,
      source: state.source === 'server' ? 'server' : 'local',
      phase: state.source === 'server'
        ? 'server_ready'
        : state.phase === 'switching_to_server' ? 'switching_to_server' : 'local_ready',
      localAsset,
      switchError: null,
    }
  }
  if (event.type === 'position') {
    if (!Number.isFinite(event.currentTimeSeconds)) return state
    return {
      ...state,
      currentTimeSeconds: Math.max(0, event.currentTimeSeconds),
      resumeAfterSwitch: event.playing,
    }
  }
  if (event.type === 'server_ready') {
    if (state.phase === 'switching_to_server') return state
    return {
      ...state,
      phase: 'switching_to_server',
      switchError: null,
    }
  }
  if (event.type === 'server_switch_succeeded') {
    if (state.phase !== 'switching_to_server') return state
    return {
      ...state,
      source: 'server',
      phase: 'server_ready',
      switchError: null,
    }
  }
  if (state.phase !== 'switching_to_server') return state
  return {
    ...state,
    source: state.localAsset ? 'local' : 'none',
    phase: state.localAsset ? 'local_ready' : 'unavailable',
    switchError: 'server_playback_switch_failed',
  }
}
