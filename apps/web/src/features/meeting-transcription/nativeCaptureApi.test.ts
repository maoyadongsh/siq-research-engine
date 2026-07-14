/// <reference types="node" />

import assert from 'node:assert/strict'
import { after, test } from 'node:test'

import {
  createNativeCapture,
  getNativeCaptureCheckpoint,
  putNativeCaptureBatch,
  recordNativeCaptureGap,
  rolloverNativeCapture,
  sealNativeCapture,
} from './nativeCaptureApi'

const originalFetch = globalThis.fetch

after(() => {
  globalThis.fetch = originalFetch
})

function installFetch(responseBody: unknown = {}) {
  const calls: Array<{ url: string; init?: RequestInit }> = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(responseBody), { status: 200, headers: { 'content-type': 'application/json' } })
  }) as typeof fetch
  return calls
}

test('native capture creation is meeting scoped and carries a stable idempotency key', async () => {
  const calls = installFetch({ capture: { id: 'capture-1' }, capture_token: 'capture-token' })
  await createNativeCapture('meeting/one', {
    device_installation_id: 'installation-1234567890',
    encoding: 'pcm_s16le',
    sample_rate: 16_000,
    channels: 1,
  }, 'native-create-key')

  const captured = calls[0]
  assert.equal(captured.url, '/api/meetings/v1/sessions/meeting%2Fone/native-captures')
  assert.equal(captured.init?.method, 'POST')
  assert.equal(new Headers(captured.init?.headers).get('Idempotency-Key'), 'native-create-key')
  assert.equal(captured.url.includes('capture-token'), false)
})

test('native batch upload sends the capture token only in Authorization and freezes metadata headers', async () => {
  const calls = installFetch({ capture_id: 'capture/one', sequence: 7 })
  const audio = new Uint8Array([1, 2, 3, 4]).buffer
  await putNativeCaptureBatch('meeting/one', 'capture/one', 'capture-secret', 'device-installation-web-1', {
    stream_epoch: 2,
    sequence: 7,
    first_sample: 32_000,
    sample_count: 16_000,
    captured_monotonic_ns: 123_456,
    encoding: 'pcm_s16le',
    sample_rate: 16_000,
    channels: 1,
    sha256: 'A'.repeat(64),
    manifest_revision: 4,
    idempotency_key: 'batch-key-7',
  }, audio)

  const captured = calls[0]
  const headers = new Headers(captured.init?.headers)
  assert.equal(captured.url, '/api/meetings/v1/sessions/meeting%2Fone/native-captures/capture%2Fone/batches/2/7')
  assert.equal(captured.url.includes('capture-secret'), false)
  assert.equal(captured.init?.method, 'PUT')
  assert.equal(captured.init?.body, audio)
  assert.equal(headers.get('Authorization'), 'Bearer capture-secret')
  assert.equal(headers.get('X-SIQ-Device-Installation-Id'), 'device-installation-web-1')
  assert.equal(headers.get('Content-Type'), 'application/octet-stream')
  assert.equal(headers.get('Idempotency-Key'), 'batch-key-7')
  assert.equal(headers.get('X-SIQ-First-Sample'), '32000')
  assert.equal(headers.get('X-SIQ-Sample-Count'), '16000')
  assert.equal(headers.get('X-SIQ-Captured-Monotonic-Ns'), '123456')
  assert.equal(headers.get('X-SIQ-SHA256'), 'a'.repeat(64))
  assert.equal(headers.get('X-SIQ-Manifest-Revision'), '4')
})

test('checkpoint, rollover, and seal keep the capture token out of URLs and request bodies', async () => {
  const calls = installFetch({})
  const manifestEntries = Array.from({ length: 10 }, (_, sequence) => ({
    sequence,
    first_sample: sequence * 16_000,
    sample_count: 16_000,
    captured_monotonic_ns: 1_000_000_000 + sequence * 1_000_000_000,
    encoding: 'pcm_s16le' as const,
    sample_rate: 16_000 as const,
    channels: 1 as const,
    sha256: String(sequence).padStart(64, '0'),
  }))
  const boundary = {
    expected_epoch: 3,
    final_sequence: 9,
    recorded_through_sample: 160_000,
    manifest_revision: 5,
    manifest_sha256: 'b'.repeat(64),
    manifest_entries: manifestEntries,
  }

  await getNativeCaptureCheckpoint('meeting-1', 'capture-1', 'capture-secret', 'device-installation-web-1')
  await rolloverNativeCapture(
    'meeting-1',
    'capture-1',
    boundary,
    'rollover-key',
  )
  await sealNativeCapture('meeting-1', 'capture-1', 'capture-secret', 'device-installation-web-1', boundary)
  await recordNativeCaptureGap(
    'meeting-1',
    'capture-1',
    {
      stream_epoch: 3,
      from_sequence: 10,
      to_sequence: 10,
      start_sample: 160_000,
      end_sample: 176_000,
      reason: 'system_interruption',
      manifest_revision: 6,
    },
    'gap-key',
  )

  assert.deepEqual(calls.map((call) => call.url), [
    '/api/meetings/v1/sessions/meeting-1/native-captures/capture-1/checkpoint',
    '/api/meetings/v1/sessions/meeting-1/native-captures/capture-1/rollover',
    '/api/meetings/v1/sessions/meeting-1/native-captures/capture-1/seal',
    '/api/meetings/v1/sessions/meeting-1/native-captures/capture-1/gaps',
  ])
  for (const call of [calls[0], calls[2]]) {
    assert.equal(call.url.includes('capture-secret'), false)
    assert.equal(new Headers(call.init?.headers).get('Authorization'), 'Bearer capture-secret')
    assert.equal(new Headers(call.init?.headers).get('X-SIQ-Device-Installation-Id'), 'device-installation-web-1')
    assert.equal(typeof call.init?.body === 'string' && call.init.body.includes('capture-secret'), false)
  }
  for (const call of [calls[1], calls[3]]) {
    assert.equal(new Headers(call.init?.headers).has('Authorization'), false)
    assert.equal(new Headers(call.init?.headers).has('X-SIQ-Device-Installation-Id'), false)
  }
  assert.equal(new Headers(calls[1].init?.headers).get('Idempotency-Key'), 'rollover-key')
  assert.equal(new Headers(calls[3].init?.headers).get('Idempotency-Key'), 'gap-key')
  assert.deepEqual(JSON.parse(String(calls[2].init?.body)).manifest_entries, manifestEntries)
})

test('checkpoint missing ranges retain the backend start/end contract', async () => {
  const calls = installFetch({
    epochs: [{
      stream_epoch: 4,
      state: 'active',
      highest_contiguous_sequence: 5,
      highest_received_sequence: 8,
      declared_last_sequence: null,
      recorded_through_sample: 144_000,
      missing_sequence_ranges: [{ start: 6, end: 7 }],
    }],
  })

  const checkpoint = await getNativeCaptureCheckpoint(
    'meeting-1',
    'capture-1',
    'capture-secret',
    'device-installation-web-1',
  )

  assert.deepEqual(checkpoint.epochs[0]?.missing_sequence_ranges, [{ start: 6, end: 7 }])
  assert.equal(calls.length, 1)
})

test('capture scoped APIs reject an empty token before issuing a request', () => {
  const calls = installFetch({})
  assert.throws(
    () => getNativeCaptureCheckpoint('meeting-1', 'capture-1', '  ', 'device-installation-web-1'),
    /token 不能为空/,
  )
  assert.equal(calls.length, 0)
})

test('capture scoped APIs reject a short installation id before issuing a request', () => {
  const calls = installFetch({})
  assert.throws(
    () => getNativeCaptureCheckpoint('meeting-1', 'capture-1', 'capture-secret', 'short'),
    /设备安装标识无效/,
  )
  assert.equal(calls.length, 0)
})
