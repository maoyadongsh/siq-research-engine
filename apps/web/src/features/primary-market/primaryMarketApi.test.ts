/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { after, test } from 'node:test'

const {
  advancePrimaryMarketMeetingWorkflow,
  appendPrimaryMarketMeetingEvent,
  confirmPrimaryMarketDecision,
  fetchPrimaryMarketMeetingAgentReadiness,
  fetchPrimaryMarketMeetingTranscript,
  fetchPrimaryMarketProject,
  fetchPrimaryMarketProjects,
  fetchPrimaryMarketProjectStatus,
  postPrimaryMarketMeetingChat,
  preparePrimaryMarketMeetingAgent,
  preparePrimaryMarketMeetingCommittee,
  runPrimaryMarketMeetingR1Agent,
  runPrimaryMarketMeetingR1Serial,
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

  const response = await postPrimaryMarketMeetingChat({
    agentId: 'siq_ic_finance_auditor',
    agentLabel: '财务审计委员',
    message: '请审阅财务质量',
    displayMessage: '@财务审计委员 请审阅财务质量',
    dealId: 'DEAL/Alpha 001',
    companyName: 'Alpha Robotics',
  })

  assert.deepEqual(response, { reply: '收到，开始发言。', sessionId: null })
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
          page: { title: '一级市场多智能体投研决策' },
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

  const response = await postPrimaryMarketMeetingChat({
    agentId: 'siq_ic_legal_scanner',
    agentLabel: '法务合规委员',
    message: '请审阅附件',
    displayMessage: '@法务合规委员 请审阅附件',
    dealId: 'DEAL-ALPHA-001',
    lane: 'agent-siq_ic_legal_scanner',
    attachments: [attachment],
  })

  assert.deepEqual(response, { reply: '附件已读取。', sessionId: null })
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
      {
        id: 'quality-1',
        event_type: 'quality_check',
        phase: 'R1',
        speaker: '财务审计委员',
        title: '回答质量检查',
        body: 'role.boundary=fail; evidence.reference=warn',
        tone: 'error',
        meta: {
          source: 'ic_agent_output_quality',
          quality: {
            status: 'fail',
            checks: [
              { id: 'role.boundary', status: 'fail', detail: '越权' },
              { id: 'evidence.reference', status: 'warn', detail: '缺证据' },
            ],
          },
        },
        agent_id: 'siq_ic_finance_auditor',
        created_at: '2026-07-05T11:01:00Z',
      },
    ],
  })

  const transcript = await fetchPrimaryMarketMeetingTranscript('DEAL/Alpha 001', { lane: 'main', limit: 20 })

  assert.equal(transcript.dealId, 'DEAL/Alpha 001')
  assert.equal(transcript.events[0]?.type, 'agent_speech')
  assert.equal(transcript.events[0]?.agentId, 'siq_ic_finance_auditor')
  assert.equal(transcript.events[0]?.createdAt, '2026-07-05T11:00:00Z')
  assert.equal(transcript.events[1]?.type, 'quality_check')
  assert.equal(transcript.events[1]?.quality?.status, 'fail')
  assert.equal(transcript.events[1]?.quality?.checks[0]?.id, 'role.boundary')
  assert.equal(transcript.events[1]?.meta, 'ic_agent_output_quality')
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

test('primary-market meeting readiness normalizes snake case response', async () => {
  const calls = installFetchRecorder({
    schema_version: 'siq_primary_market_meeting_readiness_v1',
    deal_id: 'DEAL/Alpha 001',
    profiles: [
      {
        profile_id: 'siq_ic_finance_auditor',
        label: '财务审计委员',
        role: 'finance',
        runtime: { health: 'running', port: 18664 },
        contract: {
          responsibilities: ['核验收入质量'],
          source_files: ['IDENTITY.md', 'AGENTS.md'],
        },
        startup_receipt: {
          present: true,
          receipt_id: 'startup-finance-R1-001',
          shared_hits: 6,
          private_hits: 1,
          gaps: [],
        },
        r1_report: {
          present: false,
        },
        quality: {
          ready_for_formal_task: false,
          blocking_reasons: ['r1_report_missing'],
          warnings: ['receipt hit is low'],
        },
      },
    ],
    summary: {
      runtime_running: 1,
      receipt_present: 1,
      r1_reports_present: 0,
      blocking_profiles: ['siq_ic_finance_auditor'],
    },
  })

  const readiness = await fetchPrimaryMarketMeetingAgentReadiness('DEAL/Alpha 001')

  assert.equal(readiness.dealId, 'DEAL/Alpha 001')
  assert.equal(readiness.profiles[0]?.profileId, 'siq_ic_finance_auditor')
  assert.equal(readiness.profiles[0]?.runtime.port, 18664)
  assert.equal(readiness.profiles[0]?.contract.sourceFiles.length, 2)
  assert.equal(readiness.profiles[0]?.startupReceipt.present, true)
  assert.equal(readiness.profiles[0]?.startupReceipt.receiptId, 'startup-finance-R1-001')
  assert.equal(readiness.profiles[0]?.r1Report.present, false)
  assert.equal(readiness.profiles[0]?.quality.readyForFormalTask, false)
  assert.deepEqual(readiness.summary.blockingProfiles, ['siq_ic_finance_auditor'])
  assert.deepEqual(calls.map((call) => ({ url: call.url, method: call.method })), [
    { url: '/api/primary-market/meeting/DEAL%2FAlpha%20001/agents/readiness', method: 'GET' },
  ])
})

test('primary-market meeting readiness normalizes backend agents response', async () => {
  installFetchRecorder({
    schema_version: 'siq_primary_market_meeting_readiness_v1',
    deal_id: 'DEAL-BACKEND-001',
    agents: [
      {
        agent_id: 'siq_ic_risk_controller',
        label: '风控委员',
        role: 'risk',
        runtime: { status: 'running', enabled: true, port: 18666, runs_url: 'http://127.0.0.1:18666/v1/runs' },
        contract: {
          responsibilities: ['downside scenarios'],
          source_files: [
            { name: 'IDENTITY.md', path: 'agents/hermes/profiles/siq_ic_risk_controller/IDENTITY.md' },
          ],
          output_features: ['风险扫描报告'],
          core_focus: '市场风险、ESG、舆情',
        },
        startup_receipt: {
          required: true,
          present: false,
          gaps: ['missing_risk_evidence'],
        },
        workflow: {
          blocking_reasons: ['startup_receipt_missing'],
          warnings: ['R0 warning'],
        },
        report: {
          has_report: true,
          score: 76,
          recommendation: 'review',
          artifact_path: 'discussion/01_R1_risk_controller_report.md',
        },
        ready_for_formal_task: false,
        blocking_reasons: ['startup_receipt_missing'],
      },
    ],
    summary: {
      runtime_running: 1,
      startup_receipts: 0,
      r1_reports: 1,
      blocking_profiles: ['siq_ic_risk_controller'],
    },
  })

  const readiness = await fetchPrimaryMarketMeetingAgentReadiness('DEAL-BACKEND-001')
  const risk = readiness.profiles[0]

  assert.equal(risk?.profileId, 'siq_ic_risk_controller')
  assert.equal(risk?.contract.sourceFiles[0], 'agents/hermes/profiles/siq_ic_risk_controller/IDENTITY.md')
  assert.equal(risk?.contract.outputs[0], '风险扫描报告')
  assert.equal(risk?.contract.focus, '市场风险、ESG、舆情')
  assert.equal(risk?.r1Report.present, true)
  assert.equal(risk?.r1Report.score, 76)
  assert.equal(risk?.quality.readyForFormalTask, false)
  assert.deepEqual(risk?.quality.blockingReasons, ['startup_receipt_missing'])
  assert.deepEqual(risk?.quality.warnings, ['R0 warning'])
  assert.equal(readiness.summary.receiptPresent, 0)
  assert.equal(readiness.summary.r1ReportsPresent, 1)
})

test('primary-market meeting task APIs use facade routes and defaults', async () => {
  const calls = installFetchRecorder({
    deal_id: 'DEAL/Alpha 001',
    readiness: {
      deal_id: 'DEAL/Alpha 001',
      profiles: [],
      summary: {},
    },
    result: {
      workflow_action: 'advance-next',
      selected_action: 'run-r1-serial',
      blocking_reasons: [],
    },
  })

  const prepared = await preparePrimaryMarketMeetingAgent('DEAL/Alpha 001', 'siq_ic_finance_auditor', {
    limit: 12,
    include_vector: true,
  })
  const committee = await preparePrimaryMarketMeetingCommittee('DEAL/Alpha 001', {
    profile_ids: ['siq_ic_finance_auditor'],
    include_vector: true,
  })
  const workflow = await advancePrimaryMarketMeetingWorkflow('DEAL/Alpha 001', {
    dry_run: false,
    allow_hermes: true,
    max_agents: 1,
  })
  const r1Agent = await runPrimaryMarketMeetingR1Agent('DEAL/Alpha 001', 'siq_ic_finance_auditor', {
    dry_run: false,
    allow_hermes: true,
    round_name: 'R1',
    lane: 'agent-siq_ic_finance_auditor',
  })
  const r1Serial = await runPrimaryMarketMeetingR1Serial('DEAL/Alpha 001', {
    dry_run: false,
    allow_hermes: true,
    max_agents: 6,
    lane: 'workflow-main',
  })
  const confirmation = await confirmPrimaryMarketDecision('DEAL/Alpha 001', {
    status: 'confirmed',
    dry_run: false,
  })

  assert.equal(prepared.readiness?.dealId, 'DEAL/Alpha 001')
  assert.equal(committee.readiness?.dealId, 'DEAL/Alpha 001')
  assert.equal(workflow.result?.selected_action, 'run-r1-serial')
  assert.equal(r1Agent.deal_id, 'DEAL/Alpha 001')
  assert.equal(r1Serial.deal_id, 'DEAL/Alpha 001')
  assert.equal(confirmation.readiness?.dealId, 'DEAL/Alpha 001')
  assert.deepEqual(calls, [
    {
      url: '/api/primary-market/meeting/DEAL%2FAlpha%20001/agents/siq_ic_finance_auditor/prepare',
      method: 'POST',
      body: {
        round_name: 'R1',
        limit: 12,
        include_external: false,
        include_vector: true,
        include_rerank: false,
      },
    },
    {
      url: '/api/primary-market/meeting/DEAL%2FAlpha%20001/agents/prepare-all',
      method: 'POST',
      body: {
        round_name: 'R1',
        limit: 10,
        include_external: false,
        include_vector: true,
        include_rerank: false,
        profile_ids: ['siq_ic_finance_auditor'],
      },
    },
    {
      url: '/api/primary-market/meeting/DEAL%2FAlpha%20001/workflow/advance',
      method: 'POST',
      body: {
        dry_run: false,
        allow_hermes: true,
        max_agents: 1,
        r3_skip: true,
        r3_skip_reason: 'R2 已覆盖核心分歧，P0 留痕跳过。',
        r4_overwrite: false,
      },
    },
    {
      url: '/api/primary-market/meeting/DEAL%2FAlpha%20001/agents/siq_ic_finance_auditor/run-r1',
      method: 'POST',
      body: {
        round_name: 'R1',
        dry_run: false,
        allow_hermes: true,
        lane: 'agent-siq_ic_finance_auditor',
      },
    },
    {
      url: '/api/primary-market/meeting/DEAL%2FAlpha%20001/workflow/run-r1-serial',
      method: 'POST',
      body: {
        round_name: 'R1',
        dry_run: false,
        allow_hermes: true,
        max_agents: 6,
        lane: 'workflow-main',
      },
    },
    {
      url: '/api/primary-market/meeting/DEAL%2FAlpha%20001/decision/human-confirm',
      method: 'POST',
      body: {
        dry_run: false,
        status: 'confirmed',
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
