import type { IosNativeCaptureAdapter } from './captureAdapter'
import {
  type MeetingCaptureStatus,
  type MeetingNativeCaptureCleanupReceipt,
  type MeetingNativeCapturePrepareOptions,
} from './nativeCapture'
import {
  createNativeCapture,
  type NativeCaptureCreateResponse,
} from './nativeCaptureApi'

const DEVICE_INSTALLATION_KEY = 'siq-meeting-native-device-installation'
const CREATE_KEY_PREFIX = 'siq-meeting-native-create:'

interface NativeCaptureStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

export interface NativeCaptureIdentity {
  deviceInstallationId: string
  createIdempotencyKey: string
}

function randomIdentifier(prefix: string) {
  const random = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  return `${prefix}-${random}`
}

function readOrCreate(storage: NativeCaptureStorage | null, key: string, prefix: string) {
  try {
    const existing = storage?.getItem(key)?.trim()
    if (existing && existing.length >= 16) return existing
    const created = randomIdentifier(prefix)
    storage?.setItem(key, created)
    return created
  } catch {
    return randomIdentifier(prefix)
  }
}

/** Stable non-secret identifiers may survive a WebView reload; credentials never enter storage. */
export function getOrCreateNativeCaptureIdentity(
  meetingId: string,
  storage: NativeCaptureStorage | null = typeof localStorage === 'undefined' ? null : localStorage,
): NativeCaptureIdentity {
  return {
    deviceInstallationId: readOrCreate(storage, DEVICE_INSTALLATION_KEY, 'ios-device'),
    createIdempotencyKey: readOrCreate(
      storage,
      `${CREATE_KEY_PREFIX}${encodeURIComponent(meetingId)}`,
      'ios-capture',
    ),
  }
}

export function nativeMeetingApiBaseUrl(resolvedUrl: string) {
  let url: URL
  try {
    url = new URL(resolvedUrl)
  } catch {
    throw new Error('iOS 原生采集缺少可信的 HTTPS 会议服务配置。')
  }
  if (
    url.protocol !== 'https:'
    || url.username
    || url.password
    || url.pathname !== '/api/meetings/v1'
    || url.search
    || url.hash
  ) throw new Error('iOS 原生采集要求使用可信的 HTTPS 会议服务。')
  return url.toString().replace(/\/$/, '')
}

interface NativeLifecycleAdapter {
  prepare(options: MeetingNativeCapturePrepareOptions): Promise<MeetingCaptureStatus>
  start(): Promise<MeetingCaptureStatus>
  resume(): Promise<MeetingCaptureStatus>
}

export interface StartNativeCaptureSessionInput {
  adapter: NativeLifecycleAdapter
  meetingId: string
  streamEpoch: number
  apiBaseUrl: string
  identity: NativeCaptureIdentity
  userBearerToken?: string
  expectedCaptureId?: string | null
  createCapture?: typeof createNativeCapture
}

export interface StartedNativeCaptureSession {
  captureId: string
  status: MeetingCaptureStatus
  replayed: boolean
}

function numericLimit(value: unknown) {
  return Number.isSafeInteger(value) && Number(value) > 0 ? Number(value) : undefined
}

export async function startNativeCaptureSession({
  adapter,
  meetingId,
  streamEpoch,
  apiBaseUrl,
  identity,
  userBearerToken,
  expectedCaptureId,
  createCapture = createNativeCapture,
}: StartNativeCaptureSessionInput): Promise<StartedNativeCaptureSession> {
  const created: NativeCaptureCreateResponse = await createCapture(meetingId, {
    device_installation_id: identity.deviceInstallationId,
    encoding: 'pcm_s16le',
    sample_rate: 16_000,
    channels: 1,
  }, identity.createIdempotencyKey)
  const captureId = created.capture.id
  if (expectedCaptureId && captureId !== expectedCaptureId) {
    throw new Error('原生采集恢复身份不一致，已拒绝绑定新的采集记录。')
  }
  const prepared = await adapter.prepare({
    meetingId,
    captureId,
    captureToken: created.capture_token,
    userBearerToken: userBearerToken || undefined,
    deviceInstallationId: identity.deviceInstallationId,
    apiBaseUrl,
    streamEpoch,
    audioConfig: {
      encoding: 'pcm_s16le',
      sampleRate: 16_000,
      channels: 1,
      batchDurationMs: 5_000,
    },
    maxBatchBytes: numericLimit(created.limits.max_batch_bytes),
    maxTotalBytes: numericLimit(created.limits.max_total_bytes),
    maxDurationSeconds: numericLimit(created.limits.max_duration_seconds),
  })

  let status = prepared
  if (prepared.state === 'prepared') status = await adapter.start()
  else if (prepared.state === 'paused' || prepared.state === 'interrupted') status = await adapter.resume()

  return { captureId, status, replayed: created.replayed }
}

export function nativeCleanupReady(
  status: MeetingCaptureStatus | null,
  checkpoints: import('./nativeCapture').MeetingCaptureCheckpoints | null,
) {
  if (!status || !checkpoints) return false
  const sealedThroughSample = checkpoints.finalization.sealedThroughSample
  return Boolean(
    status.state === 'stopped'
    && checkpoints.finalization.ingestComplete
    && checkpoints.finalization.serverPlaybackState === 'ready'
    && checkpoints.ingest.missingSequenceRanges.length === 0
    && checkpoints.capture.recordedThroughSample === status.recordedThroughSample
    && checkpoints.capture.manifestRevision === status.manifestRevision
    && sealedThroughSample !== null
    && Number.isSafeInteger(sealedThroughSample)
    && sealedThroughSample >= checkpoints.capture.recordedThroughSample
    && checkpoints.ingest.persistedThroughSample >= checkpoints.capture.recordedThroughSample
  )
}

export function validateNativeCleanupReceipt(
  receipt: MeetingNativeCaptureCleanupReceipt,
  status: MeetingCaptureStatus,
  checkpoints?: import('./nativeCapture').MeetingCaptureCheckpoints | null,
) {
  const recordedThroughSample = checkpoints?.capture.recordedThroughSample ?? status.recordedThroughSample
  const manifestRevision = checkpoints?.capture.manifestRevision ?? status.manifestRevision
  const validTimestamp = Number.isFinite(Date.parse(receipt.verifiedAt))
  const valid = receipt.schemaVersion === 'siq.meeting.native_capture.cleanup_receipt.v1'
    && receipt.captureId === status.captureId
    && receipt.meetingId === status.meetingId
    && receipt.streamEpoch === status.streamEpoch
    && receipt.recordedThroughSample === recordedThroughSample
    && receipt.manifestRevision === manifestRevision
    && /^[0-9a-f]{64}$/.test(receipt.serverWavSha256)
    && Number.isSafeInteger(receipt.serverWavByteSize)
    && receipt.serverWavByteSize > 0
    && validTimestamp
  if (!valid) throw new Error('原生采集清理回执校验失败，本地录音未标记为已清理。')
  return receipt
}

export type NativeCaptureAdapterWithReceipt = IosNativeCaptureAdapter & {
  discardLocalCaptureWithReceipt(
    confirmedServerComplete: boolean,
  ): Promise<MeetingNativeCaptureCleanupReceipt>
}
