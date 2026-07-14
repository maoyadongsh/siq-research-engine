export const MEETING_AUDIO_MAGIC = 'SIQA'
export const MEETING_AUDIO_VERSION = 1
export const MEETING_AUDIO_HEADER_SIZE = 32

export const MeetingAudioFrameFlag = {
  END_OF_STREAM: 1,
  DISCONTINUITY: 2,
} as const

export interface MeetingAudioFrameInput {
  streamEpoch: number
  sequence: number | bigint
  captureTimeMs: number | bigint
  payload: ArrayBuffer | ArrayBufferView
  flags?: number
}

function payloadView(payload: ArrayBuffer | ArrayBufferView) {
  if (payload instanceof ArrayBuffer) return new Uint8Array(payload)
  return new Uint8Array(payload.buffer, payload.byteOffset, payload.byteLength)
}

export function encodeMeetingAudioFrame(input: MeetingAudioFrameInput) {
  const payload = payloadView(input.payload)
  if (payload.byteLength % 2 !== 0) throw new Error('PCM16 payload must contain an even number of bytes')
  const frame = new ArrayBuffer(MEETING_AUDIO_HEADER_SIZE + payload.byteLength)
  const bytes = new Uint8Array(frame)
  const view = new DataView(frame)
  for (let index = 0; index < MEETING_AUDIO_MAGIC.length; index += 1) {
    bytes[index] = MEETING_AUDIO_MAGIC.charCodeAt(index)
  }
  view.setUint8(4, MEETING_AUDIO_VERSION)
  view.setUint8(5, input.flags || 0)
  view.setUint16(6, MEETING_AUDIO_HEADER_SIZE, false)
  view.setUint32(8, input.streamEpoch, false)
  view.setBigUint64(12, BigInt(input.sequence), false)
  view.setBigUint64(20, BigInt(input.captureTimeMs), false)
  view.setUint32(28, payload.byteLength, false)
  bytes.set(payload, MEETING_AUDIO_HEADER_SIZE)
  return frame
}

export function createMeetingStreamStartMessage(input: {
  meetingId: string
  clientStreamId: string
  streamEpoch: number
  lastAckedSequence?: number
  lastServerCursor?: number
  hotwords?: string[]
  chunkMs?: number
}) {
  const message: Record<string, unknown> = {
    type: 'stream.start',
    schema_version: 'siq.meeting.stream.v1',
    meeting_id: input.meetingId,
    client_stream_id: input.clientStreamId,
    stream_epoch: input.streamEpoch,
    audio: {
      encoding: 'pcm_s16le',
      sample_rate: 16000,
      channels: 1,
      chunk_ms: input.chunkMs || 500,
    },
    last_acked_sequence: input.lastAckedSequence ?? -1,
    hotwords: input.hotwords || [],
  }
  if (input.lastServerCursor != null) message.last_server_cursor = input.lastServerCursor
  return message
}

export function floatSamplesToPcm16(samples: Float32Array) {
  const pcm = new Int16Array(samples.length)
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]))
    pcm[index] = sample < 0 ? Math.round(sample * 0x8000) : Math.round(sample * 0x7fff)
  }
  return pcm
}
