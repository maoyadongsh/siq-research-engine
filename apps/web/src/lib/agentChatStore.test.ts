/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { afterEach, test } from 'node:test'

import { AgentChatStore } from './agentChatStore.ts'
import type { AgentAttachment, AgentChatContext } from './agentChatTypes.ts'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
})

test('history load preserves persisted assistant audit trace id', async () => {
  globalThis.fetch = (async () => new Response(JSON.stringify({
    session_id: 'history-session',
    messages: [
      { role: 'user', content: '问题', created_at: '2026-07-12T01:00:00Z', audit_trace_id: null },
      {
        role: 'assistant',
        content: '历史回答',
        created_at: '2026-07-12T01:00:01Z',
        audit_trace_id: 'aat_1234567890abcdef1234567890abcdef',
      },
    ],
  }), { status: 200, headers: { 'content-type': 'application/json' } })) as typeof fetch

  const store = new AgentChatStore('/api')
  await store.loadHistory()

  assert.equal(store.getSnapshot().currentSessionId, 'history-session')
  assert.equal(store.getSnapshot().messages[1]?.auditTraceId, 'aat_1234567890abcdef1234567890abcdef')
})

test('history load does not promote malformed audit trace ids into the message model', async () => {
  globalThis.fetch = (async () => new Response(JSON.stringify({
    session_id: 'history-session',
    messages: [{ role: 'assistant', content: '历史回答', audit_trace_id: 'untrusted-id' }],
  }), { status: 200, headers: { 'content-type': 'application/json' } })) as typeof fetch

  const store = new AgentChatStore('/api')
  await store.loadHistory()

  assert.equal(store.getSnapshot().messages[0]?.auditTraceId, undefined)
})

test('sendMessage preserves complete research identity in the chat payload', async () => {
  let requestPayload: Record<string, unknown> | undefined
  globalThis.fetch = (async (_input, init) => {
    if (init?.method === 'POST') {
      requestPayload = JSON.parse(String(init.body)) as Record<string, unknown>
      return new Response('event: done\ndata: {"content":"完成"}\n\n', {
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
      })
    }
    return new Response(JSON.stringify({ sessions: [], messages: [] }), { status: 200 })
  }) as typeof fetch

  const context: AgentChatContext = {
    company: { code: '00700', name: 'Tencent', dir: 'HK:00700', market: 'HK', company_id: 'HK:00700' },
    report: { type: 'analysis', filename: '2025.html', market: 'HK', company_id: 'HK:00700', filing_id: 'HK:00700:2025-annual', parse_run_id: 'parse-hk-00700' },
    research_identity: {
      market: 'HK',
      company_id: 'HK:00700',
      filing_id: 'HK:00700:2025-annual',
      parse_run_id: 'parse-hk-00700',
    },
  }

  const store = new AgentChatStore('/api')
  await store.sendMessage('2025 年营业收入是多少？', context)

  assert.deepEqual((requestPayload?.context as Record<string, unknown>)?.research_identity, context.research_identity)
  assert.deepEqual((requestPayload?.context as Record<string, unknown>)?.report, context.report)
})

test('sendMessage does not fabricate research identity from display-only company fields', async () => {
  let requestPayload: Record<string, unknown> | undefined
  globalThis.fetch = (async (_input, init) => {
    if (init?.method === 'POST') {
      requestPayload = JSON.parse(String(init.body)) as Record<string, unknown>
      return new Response('event: done\ndata: {"content":"完成"}\n\n', { status: 200 })
    }
    return new Response(JSON.stringify({ sessions: [], messages: [] }), { status: 200 })
  }) as typeof fetch

  const store = new AgentChatStore('/api')
  await store.sendMessage('请查看这家公司', {
    company: { code: '00700', name: 'Tencent Holdings', dir: 'HK:00700' },
    report: { type: 'analysis', title: '智能分析' },
  })

  const payloadContext = requestPayload?.context as Record<string, unknown>
  assert.equal(payloadContext.research_identity, undefined)
  assert.equal((payloadContext.company as Record<string, unknown>).company_id, undefined)
})

test('voice transcription is uploaded as multipart and sent with its replay attachment', async () => {
  const attachment: AgentAttachment = {
    id: 'voice-id',
    filename: 'voice.webm',
    content_type: 'audio/webm',
    size: 10,
    path: '/tmp/chat_uploads/1/voice-id_voice.webm',
    url: '/api/chat/attachments/voice-id_voice.webm',
    kind: 'audio',
    metadata: { duration_ms: 2100, transcript: '分析这家公司' },
  }
  let chatPayload: Record<string, unknown> | undefined
  let transcriptionForm: FormData | undefined
  globalThis.fetch = (async (input, init) => {
    const url = String(input)
    if (url.endsWith('/chat/transcribe')) {
      transcriptionForm = init?.body as FormData
      return new Response(JSON.stringify({
        text: '分析这家公司',
        duration: 2.1,
        language: 'zh',
        provider: 'funasr',
        attachment,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    }
    if (url.endsWith('/chat/stream') && init?.method === 'POST') {
      chatPayload = JSON.parse(String(init.body)) as Record<string, unknown>
      return new Response('event: done\ndata: {"content":"完成"}\n\n', {
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
      })
    }
    return new Response(JSON.stringify({ sessions: [], messages: [] }), { status: 200 })
  }) as typeof fetch

  const store = new AgentChatStore('/api')
  const result = await store.transcribeVoice({
    blob: new Blob(['voice-data'], { type: 'audio/webm' }),
    mimeType: 'audio/webm',
    suggestedFilename: 'voice.webm',
  })
  await store.sendMessage(result.text, undefined, result.text, [result.attachment])

  assert.ok(transcriptionForm instanceof FormData)
  assert.equal(transcriptionForm.get('language'), 'zh')
  assert.equal((transcriptionForm.get('file') as File).name, 'voice.webm')
  assert.equal(chatPayload?.message, '分析这家公司')
  assert.deepEqual(chatPayload?.attachments, [attachment])
})
