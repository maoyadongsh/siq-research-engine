/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  createMeetingStreamStartMessage,
  encodeMeetingAudioFrame,
  floatSamplesToPcm16,
  MEETING_AUDIO_HEADER_SIZE,
  MeetingAudioFrameFlag,
} from './audioProtocol.ts'

test('meeting PCM frame uses the fixed 32-byte network-order header', () => {
  const payload = new Uint8Array([0x01, 0x02, 0x03, 0x04])
  const frame = encodeMeetingAudioFrame({
    streamEpoch: 7,
    sequence: 0x0102030405060708n,
    captureTimeMs: 123456789n,
    flags: MeetingAudioFrameFlag.DISCONTINUITY,
    payload,
  })
  const bytes = new Uint8Array(frame)
  const view = new DataView(frame)

  assert.equal(frame.byteLength, MEETING_AUDIO_HEADER_SIZE + payload.byteLength)
  assert.equal(new TextDecoder().decode(bytes.slice(0, 4)), 'SIQA')
  assert.equal(view.getUint8(4), 1)
  assert.equal(view.getUint8(5), MeetingAudioFrameFlag.DISCONTINUITY)
  assert.equal(view.getUint16(6, false), 32)
  assert.equal(view.getUint32(8, false), 7)
  assert.equal(view.getBigUint64(12, false), 0x0102030405060708n)
  assert.equal(view.getBigUint64(20, false), 123456789n)
  assert.equal(view.getUint32(28, false), 4)
  assert.deepEqual([...bytes.slice(32)], [...payload])
})

test('meeting PCM frame rejects a partial 16-bit sample', () => {
  assert.throws(() => encodeMeetingAudioFrame({
    streamEpoch: 1,
    sequence: 0,
    captureTimeMs: 0,
    payload: new Uint8Array([1]),
  }), /even number of bytes/)
})

test('stream start declares the frozen audio contract and resume cursor', () => {
  assert.deepEqual(createMeetingStreamStartMessage({
    meetingId: 'meeting/alpha',
    clientStreamId: 'client-1',
    streamEpoch: 3,
    lastAckedSequence: 41,
    hotwords: ['海光信息'],
  }), {
    type: 'stream.start',
    schema_version: 'siq.meeting.stream.v1',
    meeting_id: 'meeting/alpha',
    client_stream_id: 'client-1',
    stream_epoch: 3,
    audio: { encoding: 'pcm_s16le', sample_rate: 16000, channels: 1, chunk_ms: 500 },
    last_acked_sequence: 41,
    hotwords: ['海光信息'],
  })
})

test('float audio conversion clamps samples into signed PCM16', () => {
  assert.deepEqual([...floatSamplesToPcm16(new Float32Array([-2, -1, 0, 0.5, 1, 2]))], [
    -32768,
    -32768,
    0,
    16384,
    32767,
    32767,
  ])
})
