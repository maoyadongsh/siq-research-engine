/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { after, test } from 'node:test'

const {
  appendPrimaryMarketMeetingEvent,
  fetchPrimaryMarketMeetingTranscript,
  fetchPrimaryMarketProject,
  fetchPrimaryMarketProjects,
  fetchPrimaryMarketProjectStatus,
  postPrimaryMarketMeetingChat,
  uploadPrimaryMarketMeetingAttachments,
} = await import('./primaryMarketApi.ts')

type FetchCall = {
  url: string
  method: string
  body?: unknown
}

const originalFetch = globalThis.fetch

after(() => {
  globalThis.fetch = originalFetch
})

function installFetchRecorder(responseBody: unknown = { reply: '收到，开始发言。' }) {
  const calls: FetchCall[] = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const body = typeof init?.body === 'string' && init.body ? JSON.parse(init.body) : undefined
    calls.push({
      url: typeof input === 'string' ? input : input.toString(),
      method: init?.method || 'GET',
      body,
    })
    return new Response(JSON.stringify(responseBody), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  }) as typeof fetch
  return calls
}

test('primary-market meeting chat uses isolated route and project context', async () => {
  const calls = installFetchRecorder()

  const reply = await postPrimaryMarketMeetingChat({
    agentId: 'siq_ic_finance_auditor',
    agentLabel: '财务审计委员',
    message: '请审阅财务质量',
    displayMessage: '@财务审计委员 请审阅财务质量',
    dealId: 'DEAL/Alpha 001',
    companyName: 'Alpha Robotics',
  })

  assert.equal(reply, '收到，开始发言。')
  assert.deepEqual(calls, [
    {
      url: '/api/primary-market/meeting/siq_ic_finance_auditor/chat',
      method: 'POST',
      body: {
        message: '请审阅财务质量',
        display_message: '@财务审计委员 请审阅财务质量',
        deal_id: 'DEAL/Alpha 001',
        company_name: 'Alpha Robotics',
        lane: 'main',
        context: {
          deal_id: 'DEAL/Alpha 001',
          company_name: 'Alpha Robotics',
          lane: 'main',
          page: { title: '一级市场多智能体投研会议室' },
          agent: {
            id: 'siq_ic_finance_auditor',
            label: '财务审计委员',
          },
        },
      },
    },
  ])
})

test('primary-market meeting chat can target an agent window with attachments', async () => {
  const attachment = {
    id: 'att-1',
    filename: 'memo.txt',
    content_type: 'text/plain',
    size: 5,
    path: '/tmp/chat_uploads/primary_market_projects/DEAL/att-1_memo.txt',
    url: '/api/primary-market/projects/DEAL-ALPHA-001/meeting/attachments/att-1_memo.txt',
    kind: 'document' as const,
  }
  const calls = installFetchRecorder({ content: '附件已读取。' })

  const reply = await postPrimaryMarketMeetingChat({
    agentId: 'siq_ic_legal_scanner',
    agentLabel: '法务合规委员',
    message: '请审阅附件',
    displayMessage: '@法务合规委员 请审阅附件',
    dealId: 'DEAL-ALPHA-001',
    lane: 'agent-siq_ic_legal_scanner',
    attachments: [attachment],
  })

  assert.equal(reply, '附件已读取。')
  assert.equal(calls[0]?.url, '/api/primary-market/meeting/siq_ic_legal_scanner/chat')
  assert.equal((calls[0]?.body as Record<string, unknown>).lane, 'agent-siq_ic_legal_scanner')
  assert.deepEqual((calls[0]?.body as Record<string, unknown>).attachments, [attachment])
})

test('primary-market meeting uploads attachments through project scoped route', async () => {
  const calls = installFetchRecorder({
    attachments: [
      {
        id: 'att-2',
        filename: 'memo.txt',
        content_type: 'text/plain',
        size: 5,
        path: '/tmp/chat_uploads/primary_market_projects/DEAL-ALPHA-001/att-2_memo.txt',
        url: '/api/primary-market/projects/DEAL-ALPHA-001/meeting/attachments/att-2_memo.txt',
        kind: 'document',
      },
    ],
  })

  const attachments = await uploadPrimaryMarketMeetingAttachments('DEAL/Alpha 001', [
    {
      filename: 'memo.txt',
      content_type: 'text/plain',
      data_url: 'data:text/plain;base64,aGVsbG8=',
    },
  ])

  assert.equal(attachments[0]?.id, 'att-2')
  assert.deepEqual(calls, [
    {
      url: '/api/primary-market/projects/DEAL%2FAlpha%20001/meeting/attachments',
      method: 'POST',
      body: {
        files: [
          {
            filename: 'memo.txt',
            content_type: 'text/plain',
            data_url: 'data:text/plain;base64,aGVsbG8=',
          },
        ],
      },
    },
  ])
})

test('primary-market project reads use primary-market facade routes', async () => {
  const calls = installFetchRecorder({ deals: [] })

  await fetchPrimaryMarketProjects({ q: 'Alpha', status: 'draft' })
  await fetchPrimaryMarketProject('DEAL/Alpha 001')
  await fetchPrimaryMarketProjectStatus('DEAL/Alpha 001')

  assert.deepEqual(calls.map((call) => ({ url: call.url, method: call.method })), [
    { url: '/api/primary-market/projects?q=Alpha&status=draft', method: 'GET' },
    { url: '/api/primary-market/projects/DEAL%2FAlpha%20001', method: 'GET' },
    { url: '/api/primary-market/projects/DEAL%2FAlpha%20001/status', method: 'GET' },
  ])
})

test('primary-market meeting transcript uses project scoped facade routes', async () => {
  const calls = installFetchRecorder({
    deal_id: 'DEAL/Alpha 001',
    lane: 'main',
    events: [
      {
        id: 'event-1',
        event_type: 'agent_speech',
        phase: 'R1',
        speaker: '财务审计委员',
        title: '委员发言',
        body: '现金流需要复核。',
        tone: 'warning',
        meta: 'hermes:siq_ic_finance_auditor',
        agent_id: 'siq_ic_finance_auditor',
        created_at: '2026-07-05T11:00:00Z',
      },
    ],
  })

  const transcript = await fetchPrimaryMarketMeetingTranscript('DEAL/Alpha 001', { lane: 'main', limit: 20 })

  assert.equal(transcript.dealId, 'DEAL/Alpha 001')
  assert.equal(transcript.events[0]?.type, 'agent_speech')
  assert.equal(transcript.events[0]?.agentId, 'siq_ic_finance_auditor')
  assert.equal(transcript.events[0]?.createdAt, '2026-07-05T11:00:00Z')
  assert.deepEqual(calls.map((call) => ({ url: call.url, method: call.method })), [
    { url: '/api/primary-market/projects/DEAL%2FAlpha%20001/meeting-transcript?lane=main&limit=20', method: 'GET' },
  ])
})

test('primary-market meeting transcript appends snake case event payload', async () => {
  const calls = installFetchRecorder({
    event: {
      id: 'event-2',
      event_type: 'human_intervention',
      phase: 'R2',
      speaker: 'Human',
      title: '主持人追问',
      body: '请补充估值敏感性。',
      tone: 'info',
      created_at: '2026-07-05T12:00:00Z',
    },
  })

  const event = await appendPrimaryMarketMeetingEvent('DEAL/Alpha 001', {
    id: 'event-2',
    phase: 'R2',
    type: 'human_intervention',
    speaker: 'Human',
    title: '主持人追问',
    body: '请补充估值敏感性。',
    tone: 'info',
    createdAt: '2026-07-05T12:00:00Z',
  })

  assert.equal(event.type, 'human_intervention')
  assert.deepEqual(calls, [
    {
      url: '/api/primary-market/projects/DEAL%2FAlpha%20001/meeting-transcript/events',
      method: 'POST',
      body: {
        lane: 'main',
        event: {
          id: 'event-2',
          event_type: 'human_intervention',
          phase: 'R2',
          speaker: 'Human',
          title: '主持人追问',
          body: '请补充估值敏感性。',
          tone: 'info',
          meta: null,
          agent_id: null,
          created_at: '2026-07-05T12:00:00Z',
        },
      },
    },
  ])
})

test('primary-market meeting chat rejects missing deal id before fetch', async () => {
  const calls = installFetchRecorder()

  await assert.rejects(
    () => postPrimaryMarketMeetingChat({
      agentId: 'siq_ic_finance_auditor',
      agentLabel: '财务审计委员',
      message: 'hello',
      displayMessage: 'hello',
      dealId: '   ',
    }),
    /缺少项目 ID/,
  )
  assert.equal(calls.length, 0)
})

test('primary-market meeting transcript rejects missing deal id before fetch', async () => {
  const calls = installFetchRecorder()

  await assert.rejects(
    () => fetchPrimaryMarketMeetingTranscript('   '),
    /缺少项目 ID/,
  )
  await assert.rejects(
    () => appendPrimaryMarketMeetingEvent('   ', {
      id: 'event-3',
      phase: 'R1',
      type: 'agent_speech',
      speaker: '委员',
      title: '发言',
      body: '内容',
      tone: 'info',
    }),
    /缺少项目 ID/,
  )
  assert.equal(calls.length, 0)
})
