import { buildMeetingWebSocketUrl, createMeetingStreamTicket } from './api'
import { createMeetingAudioCapture, describeMeetingMicrophoneError, type MeetingAudioCapture } from './audioCapture'
import {
  createMeetingHotwordUpdateMessage,
  createMeetingStreamStartMessage,
  encodeMeetingAudioFrame,
  MEETING_AUDIO_HEADER_SIZE,
  MeetingAudioFrameFlag,
} from './audioProtocol'
import type { MeetingConnectionStatus } from './eventReducer'
import { createMeetingOutboxStore, type MeetingOutboxStore } from './meetingOutbox'
import type { MeetingEvent, MeetingStreamTicket } from './types'

export interface MeetingStreamCallbacks {
  onEvent: (event: MeetingEvent) => void
  onStatus: (status: MeetingConnectionStatus) => void
  onLevel?: (level: number) => void
  onError?: (error: Error) => void
  onRecovered?: () => void
  onInterrupted?: (error: Error) => void
}

export interface MeetingStreamConnectOptions {
  streamEpoch?: number
  lastAckedSequence?: number
  lastServerCursor?: number
  deviceId?: string
  audioSource?: string
  hotwords?: string[]
  hotwordVersion?: number
}

export const MEETING_REALTIME_CHUNK_MS = 200
export const MEETING_REALTIME_SEND_INTERVAL_MS = 160
export const MEETING_OUTBOX_CAPACITY_FRAMES = 600
const MAX_OUTBOX_FRAMES = MEETING_OUTBOX_CAPACITY_FRAMES
const OUTBOX_RESUME_FRAMES = MEETING_OUTBOX_CAPACITY_FRAMES / 2
const INITIAL_CONNECTION_TIMEOUT_MS = 15_000
const STREAM_STOP_CONFIRM_TIMEOUT_MS = 12_000
const DEFAULT_RECONNECT_WINDOW_MS = 60_000
const DEFAULT_AUDIO_FRAME_SEND_INTERVAL_MS = 250
const REALTIME_AUDIO_FRAME_SEND_INTERVAL_MS = MEETING_REALTIME_SEND_INTERVAL_MS
const MAX_IN_FLIGHT_AUDIO_FRAMES = 12
const MAX_SOCKET_BUFFERED_BYTES = 64 * 1024
const SOCKET_BUFFER_RECHECK_MS = 50
const SOCKET_CONNECTING = 0
const SOCKET_OPEN = 1

type MeetingAudioCaptureFactory = typeof createMeetingAudioCapture

export interface MeetingStreamDependencies {
  createTicket?: (meetingId: string) => Promise<MeetingStreamTicket>
  createSocket?: (url: string) => WebSocket
  performanceNow?: () => number
  stopAckTimeoutMs?: (pendingFrames: number) => number
}

export interface MeetingFrameDrainOptions {
  frames: () => ReadonlyMap<number, ArrayBuffer>
  lastAckedSequence: () => number
  canSend: () => boolean
  bufferedAmount: () => number
  send: (frame: ArrayBuffer) => void
  isReady?: (sequence: number) => boolean
  onError?: (error: Error) => void
  now?: () => number
  setTimer?: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>
  clearTimer?: (timer: ReturnType<typeof setTimeout>) => void
  frameIntervalMs?: number
  maxInFlightFrames?: number
  maxBufferedBytes?: number
}

/**
 * Drains every audio path through one ordered, ACK-aware queue. A reconnect or
 * gap request only marks frames eligible again; it never bypasses pacing.
 */
export class MeetingFrameDrainScheduler {
  private readonly options: Required<Pick<MeetingFrameDrainOptions,
    'now' | 'setTimer' | 'clearTimer' | 'frameIntervalMs' | 'maxInFlightFrames' | 'maxBufferedBytes'>>
    & Omit<MeetingFrameDrainOptions,
      'now' | 'setTimer' | 'clearTimer' | 'frameIntervalMs' | 'maxInFlightFrames' | 'maxBufferedBytes'>
  private readonly sentSequences = new Set<number>()
  private timer: ReturnType<typeof setTimeout> | null = null
  private lastSentAt = Number.NEGATIVE_INFINITY

  constructor(options: MeetingFrameDrainOptions) {
    this.options = {
      ...options,
      now: options.now ?? (() => Date.now()),
      setTimer: options.setTimer ?? ((callback, delayMs) => setTimeout(callback, delayMs)),
      clearTimer: options.clearTimer ?? ((timer) => clearTimeout(timer)),
      frameIntervalMs: options.frameIntervalMs ?? DEFAULT_AUDIO_FRAME_SEND_INTERVAL_MS,
      maxInFlightFrames: options.maxInFlightFrames ?? MAX_IN_FLIGHT_AUDIO_FRAMES,
      maxBufferedBytes: options.maxBufferedBytes ?? MAX_SOCKET_BUFFERED_BYTES,
    }
  }

  connectionReady() {
    this.cancelTimer()
    this.sentSequences.clear()
    this.kick()
  }

  suspend() {
    this.cancelTimer()
    this.sentSequences.clear()
  }

  acknowledge(ackSequence: number) {
    for (const sequence of this.sentSequences) {
      if (sequence <= ackSequence) this.sentSequences.delete(sequence)
    }
    this.kick()
  }

  requestResend(from: number, to: number) {
    if (!Number.isFinite(from) || !Number.isFinite(to) || from > to) return
    for (const sequence of this.sentSequences) {
      if (sequence >= from && sequence <= to) this.sentSequences.delete(sequence)
    }
    this.kick()
  }

  kick() {
    if (this.timer !== null) return
    this.timer = this.options.setTimer(() => {
      this.timer = null
      this.drainOne()
    }, 0)
  }

  dispose() {
    this.cancelTimer()
    this.sentSequences.clear()
  }

  private drainOne() {
    if (!this.options.canSend()) return
    if (this.sentSequences.size >= this.options.maxInFlightFrames) return

    const next = this.nextFrame()
    if (!next) return

    if (this.options.bufferedAmount() >= this.options.maxBufferedBytes) {
      this.schedule(SOCKET_BUFFER_RECHECK_MS)
      return
    }

    const elapsed = this.options.now() - this.lastSentAt
    const paceDelay = Math.max(0, this.options.frameIntervalMs - elapsed)
    if (paceDelay > 0) {
      this.schedule(paceDelay)
      return
    }

    const [sequence, frame] = next
    try {
      this.options.send(frame)
    } catch (error) {
      this.options.onError?.(error instanceof Error ? error : new Error('会议音频帧发送失败'))
      this.schedule(SOCKET_BUFFER_RECHECK_MS)
      return
    }
    this.sentSequences.add(sequence)
    this.lastSentAt = this.options.now()
    if (this.sentSequences.size < this.options.maxInFlightFrames && this.nextFrame()) {
      this.schedule(this.options.frameIntervalMs)
    }
  }

  private nextFrame(): [number, ArrayBuffer] | null {
    const ackSequence = this.options.lastAckedSequence()
    let next: [number, ArrayBuffer] | null = null
    for (const [sequence, frame] of this.options.frames()) {
      if (
        sequence <= ackSequence
        || this.sentSequences.has(sequence)
        || this.options.isReady?.(sequence) === false
      ) continue
      if (!next || sequence < next[0]) next = [sequence, frame]
    }
    return next
  }

  private schedule(delayMs: number) {
    if (this.timer !== null) return
    this.timer = this.options.setTimer(() => {
      this.timer = null
      this.drainOne()
    }, delayMs)
  }

  private cancelTimer() {
    if (this.timer === null) return
    this.options.clearTimer(this.timer)
    this.timer = null
  }
}

export function meetingReconnectPlan(input: {
  startedAtMs: number
  nowMs: number
  reconnectWindowMs: number
  attempt: number
}) {
  const remainingMs = Math.max(0, input.reconnectWindowMs - (input.nowMs - input.startedAtMs))
  if (remainingMs === 0) return { expired: true as const, delayMs: 0 }
  return {
    expired: false as const,
    delayMs: Math.min(remainingMs, 10_000, 500 * 2 ** Math.max(0, input.attempt)),
  }
}

export function meetingCaptureWindow(input: {
  previousEndMs: number | null
  observedEndMs: number
  durationMs: number
  offsetMs: number
  discontinuity: boolean
}) {
  const observedStartMs = Math.max(input.offsetMs, Math.round(input.observedEndMs - input.durationMs))
  const startMs = input.previousEndMs == null
    ? observedStartMs
    : input.discontinuity
      ? Math.max(input.previousEndMs, observedStartMs)
      : input.previousEndMs
  return { startMs, endMs: startMs + input.durationMs }
}

function meetingFrameEndMs(frame: ArrayBuffer) {
  if (frame.byteLength < MEETING_AUDIO_HEADER_SIZE) return 0
  const view = new DataView(frame)
  const captureTimeMs = Number(view.getBigUint64(20, false))
  const payloadBytes = view.getUint32(28, false)
  if (!Number.isSafeInteger(captureTimeMs)) return 0
  return captureTimeMs + Math.round(payloadBytes * 1_000 / 32_000)
}

function randomId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  const bytes = new Uint8Array(16)
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes)
  else for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256)
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function normalizeMeetingWireEvent(value: unknown): MeetingEvent | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const event = value as Partial<MeetingEvent>
  const eventType = typeof event.type === 'string' ? event.type : typeof raw.event_type === 'string' ? raw.event_type : ''
  if (!eventType) return null
  return {
    schema_version: event.schema_version || 'siq.meeting.event.v1',
    event_id: event.event_id || `${eventType}-${Date.now()}-${Math.random()}`,
    meeting_id: event.meeting_id || '',
    type: eventType,
    cursor: event.cursor ?? null,
    emitted_at: event.emitted_at || (typeof raw.created_at === 'string' ? raw.created_at : new Date().toISOString()),
    trace_id: event.trace_id,
    payload: event.payload && typeof event.payload === 'object' ? event.payload : {},
  }
}

export class MeetingStreamTransport {
  private readonly meetingId: string
  private readonly callbacks: MeetingStreamCallbacks
  private socket: WebSocket | null = null
  private streamReady = false
  private capture: MeetingAudioCapture | null = null
  private desired = false
  private stopping = false
  private paused = false
  private flowPaused = false
  private discontinuityPending = false
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectDeadlineTimer: ReturnType<typeof setTimeout> | null = null
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null
  private reconnectAttempts = 0
  private reconnectStartedAtMs: number | null = null
  private reconnectWindowMs = DEFAULT_RECONNECT_WINDOW_MS
  private reconnectExpired = false
  private connectionAttempt = 0
  private ticket: MeetingStreamTicket | null = null
  private options: MeetingStreamConnectOptions = {}
  private clientStreamId = randomId()
  private sequence = 0
  private lastAckedSequence = -1
  private captureStartedAt = 0
  private captureOffsetMs = 0
  private nextCaptureTimeMs: number | null = null
  private outbox = new Map<number, ArrayBuffer>()
  private readySequences = new Set<number>()
  private readonly outboxStore: MeetingOutboxStore
  private hydratedEpoch: number | null = null
  private persistenceQueue: Promise<void> = Promise.resolve()
  private persistenceErrorReported = false
  private ackWaiters: Array<{ target: number; resolve: () => void }> = []
  private readyWaiters: Array<{ resolve: () => void; reject: (error: Error) => void }> = []
  private stopWaiters: Array<{ resolve: (confirmed: boolean) => void }> = []
  private readonly captureFactory: MeetingAudioCaptureFactory
  private readonly createTicket: (meetingId: string) => Promise<MeetingStreamTicket>
  private readonly createSocket: (url: string) => WebSocket
  private readonly performanceNow: () => number
  private readonly stopAckTimeoutMs: (pendingFrames: number) => number
  private readonly frameDrain: MeetingFrameDrainScheduler

  constructor(
    meetingId: string,
    callbacks: MeetingStreamCallbacks,
    outboxStore: MeetingOutboxStore = createMeetingOutboxStore(),
    captureFactory: MeetingAudioCaptureFactory = createMeetingAudioCapture,
    dependencies: MeetingStreamDependencies = {},
  ) {
    this.meetingId = meetingId
    this.callbacks = callbacks
    this.outboxStore = outboxStore
    this.captureFactory = captureFactory
    this.createTicket = dependencies.createTicket ?? createMeetingStreamTicket
    this.createSocket = dependencies.createSocket ?? ((url) => new WebSocket(url))
    this.performanceNow = dependencies.performanceNow ?? (() => performance.now())
    this.stopAckTimeoutMs = dependencies.stopAckTimeoutMs
      ?? ((pendingFrames) => Math.max(2_500, pendingFrames * REALTIME_AUDIO_FRAME_SEND_INTERVAL_MS + 2_500))
    this.frameDrain = new MeetingFrameDrainScheduler({
      frames: () => this.outbox,
      isReady: (sequence) => this.readySequences.has(sequence),
      lastAckedSequence: () => this.lastAckedSequence,
      canSend: () => Boolean(
        this.socket?.readyState === SOCKET_OPEN
        && this.streamReady
        && (this.desired || this.stopping)
      ),
      bufferedAmount: () => this.socket?.bufferedAmount ?? 0,
      send: (frame) => this.socket?.send(frame),
      onError: (error) => this.callbacks.onError?.(error),
      frameIntervalMs: REALTIME_AUDIO_FRAME_SEND_INTERVAL_MS,
    })
  }

  async prepareCapture(options: MeetingStreamConnectOptions = {}) {
    this.options = { ...this.options, ...options }
    await this.startCapture()
  }

  async connect(options: MeetingStreamConnectOptions = {}) {
    if (this.desired && (this.socket?.readyState === SOCKET_OPEN || this.socket?.readyState === SOCKET_CONNECTING)) return
    this.connectionAttempt += 1
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
    this.reconnectStartedAtMs = null
    this.reconnectAttempts = 0
    this.clearReconnectDeadline()
    const recoveringExpiredConnection = this.reconnectExpired
    if (recoveringExpiredConnection) {
      this.paused = false
      this.reconnectExpired = false
      this.discontinuityPending = true
    }
    await this.prepareCapture(options)
    if (recoveringExpiredConnection && !this.flowPaused) await this.capture?.resume()
    this.desired = true
    this.stopping = false
    this.options = options
    this.lastAckedSequence = Math.max(
      recoveringExpiredConnection ? this.lastAckedSequence : -1,
      options.lastAckedSequence ?? -1,
    )
    this.sequence = Math.max(0, this.lastAckedSequence + 1)
    await this.openSocket(false)
    await this.waitForStreamReady()
  }

  private async openSocket(reconnecting: boolean) {
    const connectionAttempt = ++this.connectionAttempt
    if (reconnecting && this.reconnectWindowHasExpired()) {
      await this.expireReconnectWindow()
      return
    }
    this.callbacks.onStatus(reconnecting ? 'reconnecting' : 'connecting')
    try {
      const ticket = await this.createTicket(this.meetingId)
      if (connectionAttempt !== this.connectionAttempt || !this.desired) return
      this.ticket = ticket
      const reconnectWindowSeconds = Number(this.ticket.reconnect_window_seconds)
      if (Number.isFinite(reconnectWindowSeconds) && reconnectWindowSeconds > 0) {
        this.reconnectWindowMs = reconnectWindowSeconds * 1_000
        this.armReconnectDeadline()
      }
      const ticketAck = Number(this.ticket.last_acked_sequence)
      if (Number.isFinite(ticketAck)) this.lastAckedSequence = Math.max(this.lastAckedSequence, ticketAck)
      if (this.nextCaptureTimeMs == null) {
        this.captureOffsetMs = Math.max(0, Number(this.ticket.capture_offset_ms) || 0)
        this.captureStartedAt = this.performanceNow()
      }
      const hydrated = await this.hydrateOutbox(
        this.ticket.stream_epoch,
        () => connectionAttempt === this.connectionAttempt && this.desired,
      )
      if (!hydrated) return
      if (connectionAttempt !== this.connectionAttempt || !this.desired) return
      if (reconnecting && this.reconnectWindowHasExpired()) {
        await this.expireReconnectWindow()
        return
      }
      const socket = this.createSocket(buildMeetingWebSocketUrl(this.meetingId, this.ticket))
      socket.binaryType = 'arraybuffer'
      this.socket = socket
      this.streamReady = false
      this.frameDrain.suspend()
      socket.onopen = () => {
        if (this.socket !== socket) return
        const streamEpoch = this.ticket?.stream_epoch ?? this.options.streamEpoch ?? 1
        socket.send(JSON.stringify(createMeetingStreamStartMessage({
          meetingId: this.meetingId,
          clientStreamId: this.clientStreamId,
          streamEpoch,
          lastAckedSequence: this.lastAckedSequence,
          lastServerCursor: this.options.lastServerCursor,
          hotwords: this.options.hotwords,
          hotwordVersion: this.options.hotwordVersion,
          chunkMs: MEETING_REALTIME_CHUNK_MS,
        })))
        if (reconnecting) {
          socket.send(JSON.stringify({
            type: 'stream.resume_request',
            schema_version: 'siq.meeting.stream.v1',
            last_acked_sequence: this.lastAckedSequence,
          }))
        }
      }
      socket.onmessage = (message) => {
        if (this.socket === socket) this.handleMessage(message)
      }
      socket.onerror = () => {
        if (this.socket === socket) this.callbacks.onStatus('error')
      }
      socket.onclose = () => {
        if (this.socket !== socket) return
        if (this.heartbeatTimer) clearInterval(this.heartbeatTimer)
        this.heartbeatTimer = null
        this.socket = null
        this.streamReady = false
        this.frameDrain.suspend()
        if (this.desired) this.scheduleReconnect()
        else this.callbacks.onStatus('offline')
      }
    } catch (error) {
      if (connectionAttempt !== this.connectionAttempt) return
      const normalized = error instanceof Error ? error : new Error('会议流连接失败')
      this.callbacks.onError?.(normalized)
      this.callbacks.onStatus('error')
      if (this.desired) this.scheduleReconnect()
    }
  }

  private handleMessage(message: MessageEvent) {
    if (typeof message.data !== 'string') return
    try {
      const event = normalizeMeetingWireEvent(JSON.parse(message.data))
      if (!event) return
      const payload = event.payload as Record<string, unknown>
      if (event.type === 'stream.ready') {
        this.reconnectAttempts = 0
        this.reconnectStartedAtMs = null
        this.clearReconnectDeadline()
        this.reconnectExpired = false
        this.streamReady = true
        this.callbacks.onStatus('connected')
        this.callbacks.onRecovered?.()
        for (const waiter of this.readyWaiters.splice(0)) waiter.resolve()
        void this.startCapture().catch((error) => {
          const normalized = describeMeetingMicrophoneError(error)
          this.callbacks.onError?.(normalized)
          this.callbacks.onStatus('error')
        })
        this.frameDrain.connectionReady()
        if (!this.heartbeatTimer) {
          this.heartbeatTimer = setInterval(() => this.sendControl('stream.heartbeat'), 15_000)
        }
      } else if (event.type === 'audio.ack') {
        const ack = Number(payload.ack_sequence)
        if (Number.isFinite(ack)) {
          this.lastAckedSequence = Math.max(this.lastAckedSequence, ack)
          for (const sequence of this.outbox.keys()) {
            if (sequence <= ack) {
              this.outbox.delete(sequence)
              this.readySequences.delete(sequence)
            }
          }
          const streamEpoch = this.ticket?.stream_epoch
          if (streamEpoch != null) {
            void this.enqueuePersistence(() => this.outboxStore.acknowledge(
              this.meetingId,
              streamEpoch,
              this.clientStreamId,
              this.lastAckedSequence,
            ))
          }
          const pending = this.ackWaiters
          this.ackWaiters = pending.filter((waiter) => {
            if (waiter.target <= ack) {
              waiter.resolve()
              return false
            }
            return true
          })
          this.frameDrain.acknowledge(this.lastAckedSequence)
          if (this.flowPaused && this.outbox.size <= OUTBOX_RESUME_FRAMES) {
            this.flowPaused = false
            this.discontinuityPending = true
            if (!this.paused) void this.capture?.resume()
            this.callbacks.onRecovered?.()
          }
        }
      } else if (event.type === 'audio.gap.detected' && payload.retryable !== false) {
        this.frameDrain.requestResend(Number(payload.missing_from), Number(payload.missing_to))
      }
      if (event.type === 'stream.stopped' || (event.type === 'session.state.changed' && payload.state === 'stopped')) {
        for (const waiter of this.stopWaiters.splice(0)) waiter.resolve(true)
      }
      this.callbacks.onEvent(event)
    } catch {
      this.callbacks.onError?.(new Error('收到无法解析的会议实时事件'))
    }
  }

  private async startCapture() {
    if (!this.capture) {
      const capture = this.captureFactory({
        deviceId: this.options.deviceId,
        source: this.options.audioSource,
        chunkMs: MEETING_REALTIME_CHUNK_MS,
        onLevel: this.callbacks.onLevel,
        onChunk: (pcm, capturedAt) => this.sendAudioChunk(pcm, capturedAt),
      })
      try {
        await capture.start()
        this.capture = capture
        this.captureStartedAt = this.performanceNow()
        this.nextCaptureTimeMs = null
      } catch (error) {
        await capture.stop().catch(() => undefined)
        throw describeMeetingMicrophoneError(error)
      }
    }
    if (this.paused) await this.capture.pause()
  }

  private sendAudioChunk(pcm: ArrayBuffer, capturedAt: number) {
    if (!this.desired || this.paused || this.flowPaused || !this.ticket) return
    const sequence = this.sequence
    this.sequence += 1
    const offsetMs = this.captureOffsetMs
    const durationMs = Math.max(1, Math.round(pcm.byteLength * 1_000 / 32_000))
    const window = meetingCaptureWindow({
      previousEndMs: this.nextCaptureTimeMs,
      observedEndMs: offsetMs + capturedAt - this.captureStartedAt,
      durationMs,
      offsetMs,
      discontinuity: this.discontinuityPending,
    })
    const captureTimeMs = window.startMs
    this.nextCaptureTimeMs = window.endMs
    const frame = encodeMeetingAudioFrame({
      streamEpoch: this.ticket.stream_epoch,
      sequence,
      captureTimeMs,
      payload: pcm,
      flags: this.discontinuityPending ? MeetingAudioFrameFlag.DISCONTINUITY : 0,
    })
    this.discontinuityPending = false
    this.outbox.set(sequence, frame)
    if (this.outbox.size >= MAX_OUTBOX_FRAMES && !this.flowPaused) {
      this.flowPaused = true
      this.discontinuityPending = true
      void this.capture?.pause()
      this.callbacks.onError?.(new Error('网络发送积压已达上限，麦克风采集已暂停；连接恢复后将继续并标记时间线缺口。'))
    }
    void this.persistAndSend(sequence, frame)
  }

  private scheduleReconnect() {
    if (this.reconnectTimer || !this.desired) return
    if (this.reconnectStartedAtMs == null) {
      this.reconnectStartedAtMs = Date.now()
      this.armReconnectDeadline()
    }
    const plan = meetingReconnectPlan({
      startedAtMs: this.reconnectStartedAtMs,
      nowMs: Date.now(),
      reconnectWindowMs: this.reconnectWindowMs,
      attempt: this.reconnectAttempts,
    })
    if (plan.expired) {
      void this.expireReconnectWindow()
      return
    }
    this.callbacks.onStatus('reconnecting')
    this.reconnectAttempts += 1
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      if (this.reconnectWindowHasExpired()) void this.expireReconnectWindow()
      else void this.openSocket(true)
    }, plan.delayMs)
  }

  private reconnectWindowHasExpired() {
    return this.reconnectStartedAtMs != null
      && Date.now() - this.reconnectStartedAtMs >= this.reconnectWindowMs
  }

  private armReconnectDeadline() {
    if (this.reconnectStartedAtMs == null) return
    this.clearReconnectDeadline()
    const remainingMs = this.reconnectWindowMs - (Date.now() - this.reconnectStartedAtMs)
    if (remainingMs <= 0) {
      void this.expireReconnectWindow()
      return
    }
    this.reconnectDeadlineTimer = setTimeout(() => {
      this.reconnectDeadlineTimer = null
      void this.expireReconnectWindow()
    }, remainingMs)
  }

  private clearReconnectDeadline() {
    if (this.reconnectDeadlineTimer) clearTimeout(this.reconnectDeadlineTimer)
    this.reconnectDeadlineTimer = null
  }

  private async expireReconnectWindow() {
    if (this.reconnectExpired) return
    this.reconnectExpired = true
    this.desired = false
    this.paused = true
    this.discontinuityPending = true
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
    this.clearReconnectDeadline()
    this.connectionAttempt += 1
    this.frameDrain.suspend()
    const socket = this.socket
    this.socket = null
    this.streamReady = false
    try {
      socket?.close(4001, 'reconnect window expired')
    } catch {
      // The deadline state is authoritative even if the browser rejects close().
    }
    await this.capture?.pause().catch(() => undefined)
    const seconds = Math.max(1, Math.round(this.reconnectWindowMs / 1_000))
    const error = new Error(
      `网络中断已超过 ${seconds} 秒，浏览器录音已暂停；未确认的音频仍保存在本机。请点击恢复，息屏期间可能存在录音缺口。`,
    )
    this.callbacks.onStatus('error')
    this.callbacks.onInterrupted?.(error)
    this.callbacks.onError?.(error)
  }

  private async hydrateOutbox(streamEpoch: number, isCurrent: () => boolean) {
    if (this.hydratedEpoch === streamEpoch) {
      await this.persistenceQueue
      if (!isCurrent()) return false
      for (const sequence of this.outbox.keys()) {
        if (sequence <= this.lastAckedSequence) {
          this.outbox.delete(sequence)
          this.readySequences.delete(sequence)
        }
      }
      const highestPersisted = this.outbox.size ? Math.max(...this.outbox.keys()) : -1
      this.sequence = Math.max(this.sequence, this.lastAckedSequence + 1, highestPersisted + 1, 0)
      return true
    }
    try {
      await this.persistenceQueue
      const snapshot = await this.outboxStore.restore(this.meetingId, streamEpoch)
      if (!isCurrent()) return false
      const optionAck = this.options.streamEpoch == null || this.options.streamEpoch === streamEpoch
        ? this.options.lastAckedSequence ?? -1
        : -1
      this.lastAckedSequence = Math.max(this.lastAckedSequence, optionAck, snapshot.lastAckedSequence)
      this.clientStreamId = snapshot.clientStreamId || this.clientStreamId
      this.outbox = new Map(
        [...snapshot.frames].filter(([sequence]) => sequence > this.lastAckedSequence),
      )
      this.readySequences = new Set(this.outbox.keys())
      let restoredEndMs = 0
      for (const frame of this.outbox.values()) restoredEndMs = Math.max(restoredEndMs, meetingFrameEndMs(frame))
      if (restoredEndMs > 0) this.nextCaptureTimeMs = Math.max(this.nextCaptureTimeMs ?? 0, restoredEndMs)
      const highestPersisted = this.outbox.size ? Math.max(...this.outbox.keys()) : -1
      this.sequence = Math.max(this.lastAckedSequence + 1, highestPersisted + 1, 0)
      this.hydratedEpoch = streamEpoch
      return true
    } catch (error) {
      if (!isCurrent()) return false
      this.reportPersistenceError(error)
      this.hydratedEpoch = streamEpoch
      return true
    }
  }

  private enqueuePersistence(operation: () => Promise<void>) {
    const result = this.persistenceQueue.then(operation)
    this.persistenceQueue = result.catch((error) => this.reportPersistenceError(error))
    return result.catch(() => undefined)
  }

  private reportPersistenceError(error: unknown) {
    if (this.persistenceErrorReported) return
    this.persistenceErrorReported = true
    const detail = error instanceof Error ? error.message : 'unknown storage error'
    this.callbacks.onError?.(new Error(`断线音频缓存不可用：${detail}。当前页面仍会使用有界内存缓存，请不要刷新。`))
  }

  private async persistAndSend(sequence: number, frame: ArrayBuffer) {
    const streamEpoch = this.ticket?.stream_epoch
    if (streamEpoch == null) return
    await this.enqueuePersistence(() => this.outboxStore.putFrame(
      this.meetingId,
      streamEpoch,
      this.clientStreamId,
      this.lastAckedSequence,
      sequence,
      frame,
    ))
    if (this.outbox.has(sequence) && sequence > this.lastAckedSequence) {
      this.readySequences.add(sequence)
      this.frameDrain.kick()
    }
  }

  async pause() {
    this.paused = true
    await this.capture?.pause()
    this.sendControl('stream.pause')
  }

  async resume() {
    this.paused = false
    this.discontinuityPending = true
    this.sendControl('stream.resume')
    if (!this.flowPaused) await this.capture?.resume()
  }

  async recoverAfterForeground() {
    if (this.reconnectExpired) {
      try {
        await this.connect(this.options)
        return true
      } catch (error) {
        this.callbacks.onError?.(error instanceof Error ? error : new Error('会议流恢复失败'))
        this.callbacks.onStatus('error')
        return false
      }
    }
    if (!this.desired || this.paused) return false
    this.discontinuityPending = true
    try {
      if (this.capture?.recover) await this.capture.recover()
      else await this.capture?.resume()
    } catch (error) {
      this.callbacks.onError?.(describeMeetingMicrophoneError(error))
      this.callbacks.onStatus('error')
      return false
    }

    if (!this.desired) return false
    this.callbacks.onStatus('reconnecting')
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    const socket = this.socket
    if (socket?.readyState === SOCKET_OPEN || socket?.readyState === SOCKET_CONNECTING) {
      try {
        socket.close(4000, 'foreground recovery')
      } catch {
        if (this.socket === socket) this.socket = null
        await this.openSocket(true)
      }
    } else {
      await this.openSocket(true)
    }
    return true
  }

  updateHotwords(hotwords: string[], hotwordVersion: number) {
    if (this.socket?.readyState !== SOCKET_OPEN || !this.streamReady) {
      throw new Error('会议实时连接尚未就绪，无法更新术语')
    }
    const requestId = randomId()
    const effectiveSequence = this.sequence
    const message = createMeetingHotwordUpdateMessage({
      requestId,
      hotwordVersion,
      effectiveSequence,
      hotwords,
    })
    this.socket.send(JSON.stringify(message))
    this.options = { ...this.options, hotwords: message.hotwords, hotwordVersion }
    return { requestId, effectiveSequence }
  }

  private sendControl(type: string) {
    if (this.socket?.readyState !== SOCKET_OPEN) return
    const message: Record<string, unknown> = {
      type,
      schema_version: 'siq.meeting.stream.v1',
    }
    if (type === 'stream.resume_request') message.last_acked_sequence = this.lastAckedSequence
    if (type === 'stream.heartbeat') message.next_sequence = this.sequence
    this.socket.send(JSON.stringify(message))
  }

  private waitForAck(target: number, timeoutMs = 2500) {
    if (this.lastAckedSequence >= target) return Promise.resolve(true)
    return new Promise<boolean>((resolve) => {
      let settled = false
      const waiter: { target: number; resolve: () => void } = { target, resolve: () => undefined }
      const finish = (acknowledged: boolean) => {
        if (settled) return
        settled = true
        clearTimeout(timeout)
        this.ackWaiters = this.ackWaiters.filter((item) => item !== waiter)
        resolve(acknowledged)
      }
      waiter.resolve = () => finish(true)
      this.ackWaiters.push(waiter)
      const timeout = setTimeout(() => finish(false), timeoutMs)
    })
  }

  private waitForStreamReady(timeoutMs = INITIAL_CONNECTION_TIMEOUT_MS) {
    if (this.streamReady) return Promise.resolve()
    return new Promise<void>((resolve, reject) => {
      let settled = false
      const waiter = {
        resolve: () => {
          if (settled) return
          settled = true
          clearTimeout(timeout)
          this.readyWaiters = this.readyWaiters.filter((item) => item !== waiter)
          resolve()
        },
        reject: (error: Error) => {
          if (settled) return
          settled = true
          clearTimeout(timeout)
          this.readyWaiters = this.readyWaiters.filter((item) => item !== waiter)
          reject(error)
        },
      }
      const timeout = setTimeout(
        () => waiter.reject(new Error('实时转写连接超时，请检查网络后重试。')),
        timeoutMs,
      )
      this.readyWaiters.push(waiter)
    })
  }

  private waitForStreamStopped(timeoutMs = STREAM_STOP_CONFIRM_TIMEOUT_MS) {
    return new Promise<boolean>((resolve) => {
      let settled = false
      const waiter = {
        resolve: (confirmed: boolean) => {
          if (settled) return
          settled = true
          clearTimeout(timeout)
          this.stopWaiters = this.stopWaiters.filter((item) => item !== waiter)
          resolve(confirmed)
        },
      }
      const timeout = setTimeout(() => waiter.resolve(false), timeoutMs)
      this.stopWaiters.push(waiter)
    })
  }

  async stop() {
    const hasPendingAudio = [...this.outbox.keys()].some((sequence) => sequence > this.lastAckedSequence)
    if (
      hasPendingAudio
      && (this.socket?.readyState !== SOCKET_OPEN || !this.streamReady)
    ) {
      const error = new Error('仍有未上传的录音缓存在本机。请先恢复连接，待音频补传后再结束会议。')
      this.callbacks.onError?.(error)
      throw error
    }
    this.desired = false
    this.stopping = true
    this.connectionAttempt += 1
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer)
    this.clearReconnectDeadline()
    this.reconnectTimer = null
    this.heartbeatTimer = null
    let serverStopped = false
    if (this.socket?.readyState === SOCKET_OPEN && this.streamReady && this.ticket) {
      const pendingBeforeEos = [...this.outbox.keys()].filter((sequence) => sequence > this.lastAckedSequence)
      if (pendingBeforeEos.length) {
        const backlogTarget = Math.max(...pendingBeforeEos)
        const backlogAcknowledged = await this.waitForAck(
          backlogTarget,
          this.stopAckTimeoutMs(pendingBeforeEos.length),
        )
        if (!backlogAcknowledged) {
          this.desired = true
          this.stopping = false
          this.discontinuityPending = true
          const connectionAvailable = this.socket?.readyState === SOCKET_OPEN && this.streamReady
          if (connectionAvailable && !this.heartbeatTimer) {
            this.heartbeatTimer = setInterval(() => this.sendControl('stream.heartbeat'), 15_000)
          }
          const error = new Error('网络尚未确认全部录音，已取消结束操作并继续保留本地缓存。请等待连接恢复后重试。')
          if (connectionAvailable) this.callbacks.onStatus('connected')
          else this.scheduleReconnect()
          this.callbacks.onError?.(error)
          throw error
        }
      }
      const finalSequence = this.sequence
      this.sequence += 1
      const finalFrame = encodeMeetingAudioFrame({
        streamEpoch: this.ticket.stream_epoch,
        sequence: finalSequence,
        captureTimeMs: this.nextCaptureTimeMs ?? Math.max(0, Math.round(this.performanceNow() - this.captureStartedAt)),
        payload: new ArrayBuffer(0),
        flags: MeetingAudioFrameFlag.END_OF_STREAM,
      })
      this.outbox.set(finalSequence, finalFrame)
      await this.persistAndSend(finalSequence, finalFrame)
      const finalAcknowledged = await this.waitForAck(finalSequence, this.stopAckTimeoutMs(1))
      if (!finalAcknowledged) {
        this.callbacks.onError?.(new Error('结束标记确认超时，正在继续完成安全收尾；此前的实际录音均已确认保存。'))
      }
      // Register immediately before the control message so a same-turn stop
      // event cannot beat the waiter after a long paced backlog drain.
      const stoppedPromise = this.waitForStreamStopped()
      this.sendControl('stream.stop')
      serverStopped = await stoppedPromise
    }
    await this.capture?.stop()
    this.capture = null
    this.captureOffsetMs = 0
    this.nextCaptureTimeMs = null
    this.flowPaused = false
    this.stopping = false
    this.discontinuityPending = false
    this.frameDrain.suspend()
    this.socket?.close(1000, 'meeting stopped')
    this.socket = null
    this.streamReady = false
    this.callbacks.onStatus('offline')
    return serverStopped
  }

  async disconnect() {
    this.desired = false
    this.stopping = false
    this.connectionAttempt += 1
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer)
    this.clearReconnectDeadline()
    this.reconnectTimer = null
    this.heartbeatTimer = null
    for (const waiter of this.readyWaiters.splice(0)) waiter.reject(new Error('实时转写连接已取消。'))
    for (const waiter of this.stopWaiters.splice(0)) waiter.resolve(false)
    await this.capture?.stop()
    await this.persistenceQueue
    this.capture = null
    this.captureOffsetMs = 0
    this.nextCaptureTimeMs = null
    this.flowPaused = false
    this.discontinuityPending = false
    this.reconnectStartedAtMs = null
    this.reconnectExpired = false
    this.frameDrain.suspend()
    this.socket?.close(1000, 'page left')
    this.socket = null
    this.streamReady = false
    this.callbacks.onStatus('idle')
  }
}
