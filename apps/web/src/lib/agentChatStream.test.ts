/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { createStreamConsumer, type StreamApi } from './agentChatStream.ts'
import type { AgentProgress } from './agentChatTypes.ts'

function responseFromChunks(chunks: string[]) {
  const encoder = new TextEncoder()
  let index = 0
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index >= chunks.length) {
        controller.close()
        return
      }
      controller.enqueue(encoder.encode(chunks[index]))
      index += 1
    },
  })
  return new Response(stream, { status: 200 })
}

function createApi() {
  const calls: Array<[string, unknown]> = []
  const api: StreamApi = {
    setCurrentSession: (sessionId) => calls.push(['session', sessionId]),
    setActiveRunId: (runId) => calls.push(['run', runId]),
    startFirstEventTimer: () => calls.push(['timer:start', null]),
    clearFirstEventTimer: () => calls.push(['timer:clear', null]),
    appendAssistantDelta: (content) => calls.push(['append', content]),
    flushAssistantDelta: () => calls.push(['flush', null]),
    replaceAssistantContent: (content) => calls.push(['replace', content]),
    setAssistantAuditTraceId: (traceId) => calls.push(['audit', traceId]),
    updateAssistantProgress: (progress: AgentProgress) => calls.push(['progress', progress]),
    responseErrorMessage: async (_res, fallback) => fallback,
  }
  return { api, calls }
}

test('consumeEventStream dispatches only after the SSE blank line', async () => {
  const { api, calls } = createApi()
  const consumer = createStreamConsumer(api)
  const response = responseFromChunks([
    'event: run\n',
    'data: {"run_id":"run-1","session_id":"session-1"}',
    '\n\n',
  ])

  await consumer.consumeEventStream(response)

  assert.deepEqual(calls.slice(0, 3), [
    ['run', 'run-1'],
    ['session', 'session-1'],
    ['timer:start', null],
  ])
  assert.deepEqual(calls.at(-1), ['flush', null])
})

test('consumeEventStream flushes a final buffered event without a trailing newline', async () => {
  const { api, calls } = createApi()
  const consumer = createStreamConsumer(api)

  await consumer.consumeEventStream(responseFromChunks(['data: {"content":"tail chunk"}']))

  assert.ok(calls.some(([name, value]) => name === 'append' && value === 'tail chunk'))
})

test('consumeEventStream joins multi-line data fields before parsing', async () => {
  const { api, calls } = createApi()
  const consumer = createStreamConsumer(api)

  await consumer.consumeEventStream(responseFromChunks([
    'data: {"content":\n',
    'data: "hello from two lines"}\n\n',
    'data: plain\n',
    'data: fallback\n\n',
  ]))

  assert.ok(calls.some(([name, value]) => name === 'append' && value === 'hello from two lines'))
  assert.ok(calls.some(([name, value]) => name === 'append' && value === 'plain\nfallback'))
})

test('consumeEventStream forwards answer audit trace id from done payload', async () => {
  const { api, calls } = createApi()
  const consumer = createStreamConsumer(api)

  await consumer.consumeEventStream(responseFromChunks([
    'event: done\n',
    'data: {"content":"final","audit_trace_id":"aat_1234567890abcdef1234567890abcdef"}\n\n',
  ]))

  assert.ok(calls.some(([name, value]) => name === 'replace' && value === 'final'))
  assert.ok(calls.some(([name, value]) => name === 'audit' && value === 'aat_1234567890abcdef1234567890abcdef'))
})
