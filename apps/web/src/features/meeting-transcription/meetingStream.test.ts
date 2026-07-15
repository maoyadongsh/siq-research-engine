/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  describeMeetingMicrophoneError,
  type MeetingAudioCapture,
  type MeetingAudioCaptureOptions,
} from './audioCapture.ts'
import { MemoryMeetingOutboxStore, type MeetingOutboxStore } from './meetingOutbox.ts'
import {
  MeetingFrameDrainScheduler,
  MEETING_OUTBOX_CAPACITY_FRAMES,
  MEETING_REALTIME_CHUNK_MS,
  MEETING_REALTIME_SEND_INTERVAL_MS,
  MeetingStreamTransport,
  meetingCaptureWindow,
  meetingReconnectPlan,
  normalizeMeetingWireEvent,
} from './meetingStream.ts'

class FakeMeetingSocket {
  readyState = 0
  bufferedAmount = 0
  binaryType = ''
  sent: unknown[] = []
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null

  constructor() {
    queueMicrotask(() => {
      if (this.readyState !== 0) return
      this.readyState = 1
      this.onopen?.({} as Event)
    })
  }

  send(data: unknown) {
    this.sent.push(data)
    if (typeof data !== 'string') return
    const message = JSON.parse(data) as { type?: string }
    if (message.type !== 'stream.start' && message.type !== 'stream.stop') return
    const eventType = message.type === 'stream.start' ? 'stream.ready' : 'stream.stopped'
    queueMicrotask(() => this.onmessage?.({
      data: JSON.stringify({
        schema_version: 'siq.meeting.event.v1',
        event_id: `${eventType}-${Math.random()}`,
        meeting_id: 'meeting-1',
        type: eventType,
        cursor: null,
        emitted_at: new Date().toISOString(),
        payload: {},
      }),
    } as MessageEvent))
  }

  close() {
    if (this.readyState === 3) return
    this.readyState = 3
    this.onclose?.({} as CloseEvent)
  }
}

function streamTicket(captureOffsetMs = 0) {
  return {
    ticket: `ticket-${captureOffsetMs}`,
    stream_epoch: 1,
    last_acked_sequence: -1,
    capture_offset_ms: captureOffsetMs,
    reconnect_window_seconds: 60,
  }
}

function nextTask() {
  return new Promise<void>((resolve) => setTimeout(resolve, 0))
}

async function waitFor(condition: () => boolean, timeoutMs = 1_500) {
  const startedAt = Date.now()
  while (!condition()) {
    if (Date.now() - startedAt >= timeoutMs) throw new Error('condition did not become true before timeout')
    await new Promise((resolve) => setTimeout(resolve, 5))
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function createRecordingOutboxStore(beforePut?: (frame: ArrayBuffer) => Promise<void>) {
  const persisted: ArrayBuffer[] = []
  const store: MeetingOutboxStore = {
    async restore() {
      return { clientStreamId: null, lastAckedSequence: -1, frames: new Map() }
    },
    async putFrame(_meetingId, _streamEpoch, _clientStreamId, _lastAck, _sequence, frame) {
      await beforePut?.(frame)
      persisted.push(frame)
    },
    async acknowledge() {},
    async clear() {},
  }
  return { persisted, store }
}

function createCaptureHarness() {
  let onChunk: MeetingAudioCaptureOptions['onChunk'] = () => undefined
  let stops = 0
  let pauses = 0
  let chunkMs: number | undefined
  const capture: MeetingAudioCapture = {
    async start() {},
    async pause() { pauses += 1 },
    async resume() {},
    async recover() {},
    async stop() { stops += 1 },
  }
  return {
    capture,
    emit(pcm: ArrayBuffer, capturedAt: number) { onChunk(pcm, capturedAt) },
    factory(options: MeetingAudioCaptureOptions) {
      onChunk = options.onChunk
      chunkMs = options.chunkMs
      return capture
    },
    stops: () => stops,
    pauses: () => pauses,
    chunkMs: () => chunkMs,
  }
}

function createFakeClock() {
  let now = 0
  let nextId = 0
  const tasks = new Map<object, { at: number; callback: () => void }>()
  const clock = {
    now: () => now,
    setTimer(callback: () => void, delayMs: number) {
      const handle = { id: nextId += 1 }
      tasks.set(handle, { at: now + delayMs, callback })
      return handle as unknown as ReturnType<typeof setTimeout>
    },
    clearTimer(handle: ReturnType<typeof setTimeout>) {
      tasks.delete(handle as unknown as object)
    },
    advanceTo(targetMs: number) {
      while (true) {
        let next: [object, { at: number; callback: () => void }] | null = null
        for (const task of tasks) {
          if (task[1].at <= targetMs && (!next || task[1].at < next[1].at)) next = task
        }
        if (!next) break
        tasks.delete(next[0])
        now = next[1].at
        next[1].callback()
      }
      now = targetMs
    },
  }
  return clock
}

test('gateway durable envelope maps event_type and created_at into the UI event contract', () => {
  const event = normalizeMeetingWireEvent({
    meeting_id: 'meeting-1',
    cursor: 17,
    event_id: 'event-17',
    event_type: 'transcript.segment.stable',
    schema_version: 'meeting.event.v1',
    payload: { segment: { id: 'segment-1' } },
    trace_id: 'trace-1',
    created_at: '2026-07-13T08:00:00Z',
  })

  assert.equal(event?.type, 'transcript.segment.stable')
  assert.equal(event?.emitted_at, '2026-07-13T08:00:00Z')
  assert.equal(event?.cursor, 17)
  assert.deepEqual(event?.payload, { segment: { id: 'segment-1' } })
})

test('wire normalizer rejects messages without an event type', () => {
  assert.equal(normalizeMeetingWireEvent({ payload: {} }), null)
})

test('capture is prepared from the user action once and released on disconnect', async () => {
  let starts = 0
  let stops = 0
  const capture: MeetingAudioCapture = {
    async start() { starts += 1 },
    async pause() {},
    async resume() {},
    async stop() { stops += 1 },
  }
  const transport = new MeetingStreamTransport(
    'meeting-1',
    { onEvent() {}, onStatus() {} },
    new MemoryMeetingOutboxStore(),
    () => capture,
  )

  await transport.prepareCapture({ deviceId: 'mac-mini-microphone', audioSource: 'microphone' })
  await transport.prepareCapture({ deviceId: 'mac-mini-microphone', audioSource: 'microphone' })

  assert.equal(starts, 1)
  await transport.disconnect()
  assert.equal(stops, 1)
})

test('realtime capture and ACK queue keep a bounded 200 ms low-latency contract', async () => {
  const sockets: FakeMeetingSocket[] = []
  const capture = createCaptureHarness()
  const transport = new MeetingStreamTransport(
    'meeting-1',
    { onEvent() {}, onStatus() {} },
    new MemoryMeetingOutboxStore(),
    capture.factory,
    {
      createTicket: async () => streamTicket(),
      createSocket: () => {
        const socket = new FakeMeetingSocket()
        sockets.push(socket)
        return socket as unknown as WebSocket
      },
    },
  )

  await transport.connect()
  const start = JSON.parse(sockets[0].sent.find((value) => typeof value === 'string') as string)
  assert.equal(capture.chunkMs(), MEETING_REALTIME_CHUNK_MS)
  assert.equal(start.audio.chunk_ms, MEETING_REALTIME_CHUNK_MS)
  assert.ok(MEETING_REALTIME_SEND_INTERVAL_MS < MEETING_REALTIME_CHUNK_MS)
  assert.equal(MEETING_OUTBOX_CAPACITY_FRAMES * MEETING_REALTIME_CHUNK_MS, 120_000)
  await transport.disconnect()
})

test('live transport sends a versioned hotword update at the next uncaptured sequence', async () => {
  const sockets: FakeMeetingSocket[] = []
  const capture = createCaptureHarness()
  const transport = new MeetingStreamTransport(
    'meeting-1',
    { onEvent() {}, onStatus() {} },
    new MemoryMeetingOutboxStore(),
    capture.factory,
    {
      createTicket: async () => streamTicket(),
      createSocket: () => {
        const socket = new FakeMeetingSocket()
        sockets.push(socket)
        return socket as unknown as WebSocket
      },
    },
  )

  await transport.connect()
  capture.emit(new ArrayBuffer(6_400), performance.now() + 200)
  const boundary = transport.updateHotwords([' 海光信息 '], 2)
  const update = sockets[0].sent
    .filter((value): value is string => typeof value === 'string')
    .map((value) => JSON.parse(value))
    .find((message) => message.type === 'stream.hotwords.update')

  assert.equal(boundary.effectiveSequence, 1)
  assert.equal(update.hotword_version, 2)
  assert.equal(update.effective_sequence, 1)
  assert.deepEqual(update.hotwords, ['海光信息'])
  await transport.disconnect()
})

test('manual reconnect cancels the pending automatic producer attempt', async () => {
  const sockets: FakeMeetingSocket[] = []
  const capture = createCaptureHarness()
  const transport = new MeetingStreamTransport(
    'meeting-1',
    { onEvent() {}, onStatus() {} },
    new MemoryMeetingOutboxStore(),
    capture.factory,
    {
      createTicket: async () => streamTicket(),
      createSocket: () => {
        const socket = new FakeMeetingSocket()
        sockets.push(socket)
        return socket as unknown as WebSocket
      },
    },
  )

  await transport.connect()
  sockets[0].close()
  await transport.connect()
  await new Promise((resolve) => setTimeout(resolve, 550))

  assert.equal(sockets.length, 2)
  await transport.disconnect()
})

test('a stale ticket request cannot replace the explicit reconnect producer', async () => {
  const sockets: FakeMeetingSocket[] = []
  const capture = createCaptureHarness()
  const staleTicket = deferred<ReturnType<typeof streamTicket>>()
  let ticketRequests = 0
  const transport = new MeetingStreamTransport(
    'meeting-1',
    { onEvent() {}, onStatus() {} },
    new MemoryMeetingOutboxStore(),
    capture.factory,
    {
      createTicket: async () => {
        ticketRequests += 1
        if (ticketRequests === 2) return staleTicket.promise
        return streamTicket()
      },
      createSocket: () => {
        const socket = new FakeMeetingSocket()
        sockets.push(socket)
        return socket as unknown as WebSocket
      },
    },
  )

  await transport.connect()
  sockets[0].close()
  await waitFor(() => ticketRequests === 2)
  await transport.connect()
  staleTicket.resolve(streamTicket())
  await nextTask()

  assert.equal(ticketRequests, 3)
  assert.equal(sockets.length, 2)
  await transport.disconnect()
})

test('reconnect capture offset does not add persisted duration twice after a foreground gap', async () => {
  const sockets: FakeMeetingSocket[] = []
  const capture = createCaptureHarness()
  const outbox = createRecordingOutboxStore()
  let performanceNow = 10_000
  let ticketRequests = 0
  const transport = new MeetingStreamTransport(
    'meeting-1',
    { onEvent() {}, onStatus() {} },
    outbox.store,
    capture.factory,
    {
      performanceNow: () => performanceNow,
      createTicket: async () => streamTicket(ticketRequests++ === 0 ? 1_000 : 1_500),
      createSocket: () => {
        const socket = new FakeMeetingSocket()
        sockets.push(socket)
        return socket as unknown as WebSocket
      },
    },
  )

  await transport.connect()
  capture.emit(new ArrayBuffer(16_000), 10_500)
  await nextTask()
  assert.equal(Number(new DataView(outbox.persisted[0]).getBigUint64(20, false)), 1_000)

  sockets[0].close()
  performanceNow = 20_000
  await transport.recoverAfterForeground()
  await nextTask()
  capture.emit(new ArrayBuffer(16_000), 20_500)
  await nextTask()

  assert.equal(Number(new DataView(outbox.persisted[1]).getBigUint64(20, false)), 11_000)
  await transport.disconnect()
})

test('audio remains ineligible for WebSocket send until its outbox write settles', async () => {
  const sockets: FakeMeetingSocket[] = []
  const capture = createCaptureHarness()
  const writeGate = deferred<void>()
  const outbox = createRecordingOutboxStore(() => writeGate.promise)
  const transport = new MeetingStreamTransport(
    'meeting-1',
    { onEvent() {}, onStatus() {} },
    outbox.store,
    capture.factory,
    {
      createTicket: async () => streamTicket(),
      createSocket: () => {
        const socket = new FakeMeetingSocket()
        sockets.push(socket)
        return socket as unknown as WebSocket
      },
    },
  )

  await transport.connect()
  capture.emit(new ArrayBuffer(16_000), performance.now() + 500)
  await new Promise((resolve) => setTimeout(resolve, 20))
  assert.equal(sockets[0].sent.filter((value) => value instanceof ArrayBuffer).length, 0)

  writeGate.resolve()
  await waitFor(() => sockets[0].sent.some((value) => value instanceof ArrayBuffer))
  assert.equal(sockets[0].sent.filter((value) => value instanceof ArrayBuffer).length, 1)
  await transport.disconnect()
})

test('offline stop refuses to strand an unacknowledged local outbox', async () => {
  const sockets: FakeMeetingSocket[] = []
  const capture = createCaptureHarness()
  const outbox = createRecordingOutboxStore()
  const transport = new MeetingStreamTransport(
    'meeting-1',
    { onEvent() {}, onStatus() {} },
    outbox.store,
    capture.factory,
    {
      createTicket: async () => streamTicket(),
      createSocket: () => {
        const socket = new FakeMeetingSocket()
        sockets.push(socket)
        return socket as unknown as WebSocket
      },
    },
  )

  await transport.connect()
  capture.emit(new ArrayBuffer(16_000), performance.now() + 500)
  await nextTask()
  sockets[0].close()

  await assert.rejects(() => transport.stop(), /未上传的录音缓存/)
  assert.equal(capture.stops(), 0)
  await transport.disconnect()
  assert.equal(capture.stops(), 1)
})

test('online stop does not send EOS or stream.stop while an ACK-stalled backlog remains', async () => {
  const sockets: FakeMeetingSocket[] = []
  const capture = createCaptureHarness()
  const outbox = createRecordingOutboxStore()
  const transport = new MeetingStreamTransport(
    'meeting-1',
    { onEvent() {}, onStatus() {} },
    outbox.store,
    capture.factory,
    {
      stopAckTimeoutMs: () => 20,
      createTicket: async () => streamTicket(),
      createSocket: () => {
        const socket = new FakeMeetingSocket()
        sockets.push(socket)
        return socket as unknown as WebSocket
      },
    },
  )

  await transport.connect()
  const capturedAt = performance.now() + 500
  for (let sequence = 0; sequence < 9; sequence += 1) {
    capture.emit(new ArrayBuffer(16_000), capturedAt + sequence * 500)
  }
  await waitFor(() => outbox.persisted.length === 9)

  await assert.rejects(() => transport.stop(), /尚未确认全部录音/)
  assert.equal(outbox.persisted.length, 9)
  assert.equal(sockets[0].sent.some((value) => {
    if (typeof value !== 'string') return false
    return (JSON.parse(value) as { type?: string }).type === 'stream.stop'
  }), false)

  capture.emit(new ArrayBuffer(16_000), capturedAt + 4_500)
  await waitFor(() => outbox.persisted.length === 10)
  await transport.disconnect()
})

test('EOS ACK timeout completes stopping and never reopens capture after the end marker', async () => {
  const sockets: FakeMeetingSocket[] = []
  const errors: string[] = []
  const capture = createCaptureHarness()
  const outbox = createRecordingOutboxStore()
  const transport = new MeetingStreamTransport(
    'meeting-1',
    {
      onEvent() {},
      onStatus() {},
      onError(error) { errors.push(error.message) },
    },
    outbox.store,
    capture.factory,
    {
      stopAckTimeoutMs: () => 20,
      createTicket: async () => streamTicket(),
      createSocket: () => {
        const socket = new FakeMeetingSocket()
        sockets.push(socket)
        return socket as unknown as WebSocket
      },
    },
  )

  await transport.connect()
  assert.equal(await transport.stop(), true)
  assert.match(errors.at(-1) || '', /结束标记确认超时/)
  assert.equal(capture.stops(), 1)
  assert.equal(outbox.persisted.length, 1)
  assert.equal(new DataView(outbox.persisted[0]).getUint8(5), 1)

  capture.emit(new ArrayBuffer(16_000), performance.now() + 500)
  await nextTask()
  assert.equal(outbox.persisted.length, 1)
})

test('ticket reconnect deadline pauses capture, stops retries, and preserves the local outbox', async () => {
  const sockets: FakeMeetingSocket[] = []
  const statuses: string[] = []
  const capture = createCaptureHarness()
  const outbox = createRecordingOutboxStore()
  let interrupted = false
  const transport = new MeetingStreamTransport(
    'meeting-1',
    {
      onEvent() {},
      onStatus(status) { statuses.push(status) },
      onInterrupted() { interrupted = true },
    },
    outbox.store,
    capture.factory,
    {
      createTicket: async () => ({ ...streamTicket(), reconnect_window_seconds: 0.02 }),
      createSocket: () => {
        const socket = new FakeMeetingSocket()
        sockets.push(socket)
        return socket as unknown as WebSocket
      },
    },
  )

  await transport.connect()
  capture.emit(new ArrayBuffer(16_000), performance.now() + 500)
  await nextTask()
  sockets[0].close()
  await new Promise((resolve) => setTimeout(resolve, 50))

  assert.equal(interrupted, true)
  assert.equal(statuses.at(-1), 'error')
  assert.equal(capture.pauses(), 1)
  assert.equal(sockets.length, 1)
  assert.equal(outbox.persisted.length, 1)
  assert.equal(await transport.recoverAfterForeground(), true)
  assert.equal(sockets.length, 2)
  await transport.disconnect()
})

test('microphone permission and device failures are actionable', () => {
  assert.match(describeMeetingMicrophoneError(new DOMException('', 'NotAllowedError')).message, /允许麦克风/)
  assert.match(describeMeetingMicrophoneError(new DOMException('', 'NotReadableError')).message, /其他应用/)
  assert.match(describeMeetingMicrophoneError(new DOMException('', 'NotFoundError')).message, /未检测到/)
  assert.match(describeMeetingMicrophoneError(new DOMException('', 'AbortError')).message, /重新点击/)
})

test('PCM sample clock stays monotonic when AudioWorklet callbacks jitter backwards', () => {
  const first = meetingCaptureWindow({
    previousEndMs: null,
    observedEndMs: 981,
    durationMs: 500,
    offsetMs: 0,
    discontinuity: false,
  })
  const second = meetingCaptureWindow({
    previousEndMs: first.endMs,
    observedEndMs: 1_480,
    durationMs: 500,
    offsetMs: 0,
    discontinuity: false,
  })

  assert.deepEqual(first, { startMs: 481, endMs: 981 })
  assert.deepEqual(second, { startMs: 981, endMs: 1_481 })
})

test('PCM sample clock preserves a real pause as an explicit discontinuity', () => {
  assert.deepEqual(meetingCaptureWindow({
    previousEndMs: 1_000,
    observedEndMs: 3_500,
    durationMs: 500,
    offsetMs: 0,
    discontinuity: true,
  }), { startMs: 3_000, endMs: 3_500 })
})

test('audio drain sends restored and new frames in sequence order at no more than four frames per second', () => {
  const clock = createFakeClock()
  const frames = new Map<number, ArrayBuffer>([
    [2, Uint8Array.of(2).buffer],
    [0, Uint8Array.of(0).buffer],
    [1, Uint8Array.of(1).buffer],
  ])
  const sent: Array<{ sequence: number; at: number }> = []
  const drain = new MeetingFrameDrainScheduler({
    frames: () => frames,
    lastAckedSequence: () => -1,
    canSend: () => true,
    bufferedAmount: () => 0,
    send: (frame) => sent.push({ sequence: new Uint8Array(frame)[0], at: clock.now() }),
    now: clock.now,
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
  })

  drain.connectionReady()
  clock.advanceTo(0)
  frames.set(3, Uint8Array.of(3).buffer)
  drain.kick()
  clock.advanceTo(749)
  assert.deepEqual(sent, [
    { sequence: 0, at: 0 },
    { sequence: 1, at: 250 },
    { sequence: 2, at: 500 },
  ])
  clock.advanceTo(750)
  assert.deepEqual(sent.at(-1), { sequence: 3, at: 750 })
})

test('audio drain waits for ACK capacity before releasing more backlog', () => {
  const clock = createFakeClock()
  const frames = new Map<number, ArrayBuffer>([0, 1, 2].map((sequence) => [
    sequence,
    Uint8Array.of(sequence).buffer,
  ]))
  let ack = -1
  const sent: number[] = []
  const drain = new MeetingFrameDrainScheduler({
    frames: () => frames,
    lastAckedSequence: () => ack,
    canSend: () => true,
    bufferedAmount: () => 0,
    send: (frame) => sent.push(new Uint8Array(frame)[0]),
    now: clock.now,
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    maxInFlightFrames: 2,
  })

  drain.connectionReady()
  clock.advanceTo(1_000)
  assert.deepEqual(sent, [0, 1])
  ack = 0
  drain.acknowledge(ack)
  clock.advanceTo(1_000)
  assert.deepEqual(sent, [0, 1, 2])
})

test('audio drain never sends a frame before its outbox write has settled', () => {
  const clock = createFakeClock()
  const frames = new Map<number, ArrayBuffer>([
    [0, Uint8Array.of(0).buffer],
    [1, Uint8Array.of(1).buffer],
  ])
  const ready = new Set([0])
  const sent: number[] = []
  const drain = new MeetingFrameDrainScheduler({
    frames: () => frames,
    isReady: (sequence) => ready.has(sequence),
    lastAckedSequence: () => -1,
    canSend: () => true,
    bufferedAmount: () => 0,
    send: (frame) => sent.push(new Uint8Array(frame)[0]),
    now: clock.now,
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
  })

  drain.connectionReady()
  clock.advanceTo(500)
  assert.deepEqual(sent, [0])
  ready.add(1)
  drain.kick()
  clock.advanceTo(500)
  assert.deepEqual(sent, [0, 1])
})

test('reconnect makes unacknowledged frames eligible again without bypassing pacing', () => {
  const clock = createFakeClock()
  const frames = new Map<number, ArrayBuffer>([
    [0, Uint8Array.of(0).buffer],
    [1, Uint8Array.of(1).buffer],
  ])
  const sent: Array<{ sequence: number; at: number }> = []
  const drain = new MeetingFrameDrainScheduler({
    frames: () => frames,
    lastAckedSequence: () => -1,
    canSend: () => true,
    bufferedAmount: () => 0,
    send: (frame) => sent.push({ sequence: new Uint8Array(frame)[0], at: clock.now() }),
    now: clock.now,
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
  })

  drain.connectionReady()
  clock.advanceTo(250)
  assert.deepEqual(sent, [{ sequence: 0, at: 0 }, { sequence: 1, at: 250 }])
  drain.suspend()
  clock.advanceTo(300)
  drain.connectionReady()
  clock.advanceTo(499)
  assert.equal(sent.length, 2)
  clock.advanceTo(500)
  assert.deepEqual(sent.at(-1), { sequence: 0, at: 500 })
})

test('audio drain honors socket bufferedAmount and gap retries stay paced behind older frames', () => {
  const clock = createFakeClock()
  const frames = new Map<number, ArrayBuffer>([0, 1, 2, 3].map((sequence) => [
    sequence,
    Uint8Array.of(sequence).buffer,
  ]))
  let bufferedAmount = 64 * 1024
  const sent: Array<{ sequence: number; at: number }> = []
  const drain = new MeetingFrameDrainScheduler({
    frames: () => frames,
    lastAckedSequence: () => -1,
    canSend: () => true,
    bufferedAmount: () => bufferedAmount,
    send: (frame) => sent.push({ sequence: new Uint8Array(frame)[0], at: clock.now() }),
    now: clock.now,
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
  })

  drain.connectionReady()
  clock.advanceTo(49)
  assert.equal(sent.length, 0)
  bufferedAmount = 0
  clock.advanceTo(550)
  assert.deepEqual(sent.map((item) => item.sequence), [0, 1, 2])

  drain.requestResend(1, 2)
  clock.advanceTo(799)
  assert.deepEqual(sent.map((item) => item.sequence), [0, 1, 2])
  clock.advanceTo(800)
  assert.deepEqual(sent.at(-1), { sequence: 1, at: 800 })
  clock.advanceTo(1_050)
  assert.deepEqual(sent.at(-1), { sequence: 2, at: 1_050 })
})

test('reconnect plan caps retries at the ticket window', () => {
  assert.deepEqual(meetingReconnectPlan({
    startedAtMs: 1_000,
    nowMs: 1_000,
    reconnectWindowMs: 60_000,
    attempt: 0,
  }), { expired: false, delayMs: 500 })
  assert.deepEqual(meetingReconnectPlan({
    startedAtMs: 1_000,
    nowMs: 60_900,
    reconnectWindowMs: 60_000,
    attempt: 10,
  }), { expired: false, delayMs: 100 })
  assert.deepEqual(meetingReconnectPlan({
    startedAtMs: 1_000,
    nowMs: 61_000,
    reconnectWindowMs: 60_000,
    attempt: 10,
  }), { expired: true, delayMs: 0 })
})
