/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { after, test } from 'node:test'

const {
  completeMeetingImport,
  createMeetingImport,
  putMeetingImportChunk,
  sha256Blob,
} = await import('./meetingImportApi.ts')

const originalFetch = globalThis.fetch

after(() => {
  globalThis.fetch = originalFetch
})

test('meeting import create stays outside chat and carries an idempotency key', async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify({ id: 'upload-1' }), { status: 201 })
  }) as typeof fetch

  await createMeetingImport({
    filename: 'meeting.wav',
    media_type: 'audio/wav',
    file_size: 4,
    chunk_size: 4,
    title: 'Meeting',
    language: 'zh-CN',
    voiceprint_enabled: false,
    ai_enabled: false,
    model_selection: { mode: 'none', model_ref: null, fallback_policy: 'disabled' },
  }, 'import-idempotency')

  const captured = calls[0]
  assert.ok(captured)
  assert.equal(captured.url, '/api/meetings/v1/imports')
  assert.equal(captured.init?.method, 'POST')
  assert.equal(new Headers(captured.init?.headers).get('Idempotency-Key'), 'import-idempotency')
  assert.equal(captured.url.includes('/chat/'), false)
})

test('meeting import chunk sends exact ordinal offset hash and binary body', async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify({
      upload_id: 'upload/one',
      ordinal: 2,
      byte_offset: 8,
      byte_size: 3,
      sha256: 'hash',
      received_size: 11,
      received_chunks: 3,
      next_ordinal: 3,
      replayed: false,
    }), { status: 200 })
  }) as typeof fetch
  const chunk = new Blob(['abc'], { type: 'application/octet-stream' })
  const digest = await sha256Blob(chunk)
  assert.equal(digest, 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

  await putMeetingImportChunk('upload/one', 2, 8, chunk, digest)

  const captured = calls[0]
  assert.ok(captured)
  assert.equal(captured.url, '/api/meetings/v1/imports/upload%2Fone/chunks/2')
  assert.equal(captured.init?.method, 'PUT')
  assert.equal(new Headers(captured.init?.headers).get('X-Chunk-Offset'), '8')
  assert.equal(new Headers(captured.init?.headers).get('X-Chunk-SHA256'), digest)
  assert.equal(captured.init?.body, chunk)
})

test('meeting import completion uses its durable upload resource', async () => {
  let url = ''
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    url = String(input)
    return new Response(JSON.stringify({ id: 'upload-1', state: 'queued' }), { status: 200 })
  }) as typeof fetch
  await completeMeetingImport('upload-1')
  assert.equal(url, '/api/meetings/v1/imports/upload-1/complete')
})
