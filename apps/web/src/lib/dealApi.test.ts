/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { after, test } from 'node:test'

const {
  dryRunDealWorkflowR1Agent,
  fetchDealAgents,
  fetchDealAudit,
  fetchDealDecision,
  fetchDealDisputes,
  fetchDealManifest,
  fetchDealPhaseArtifacts,
  fetchDealPreflight,
  fetchDealR1AgentReports,
  fetchDealR2AgentReports,
  fetchDealR3ReviewSummary,
  fetchDealReport,
  fetchDealReports,
  fetchDealStatus,
  fetchDealWorkflow,
  generateDealWorkflowDisputeRulings,
  generateDealStartupRetrieval,
  identifyDealWorkflowDisputes,
  postDealDecisionHumanConfirmation,
  ruleDealWorkflowDispute,
  runDealWorkflowR1Serial,
} = await import('./dealApi.ts')

type FetchCall = {
  url: string
  method: string
  body?: unknown
}

const originalFetch = globalThis.fetch

after(() => {
  globalThis.fetch = originalFetch
})

function installFetchRecorder() {
  const calls: FetchCall[] = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const body = typeof init?.body === 'string' && init.body ? JSON.parse(init.body) : undefined
    calls.push({
      url: typeof input === 'string' ? input : input.toString(),
      method: init?.method || 'GET',
      body,
    })
    return new Response('{}', {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  }) as typeof fetch
  return calls
}

test('deal read APIs encode deal ids and use expected endpoints', async () => {
  const calls = installFetchRecorder()

  await fetchDealStatus('DEAL 1/测试')
  await fetchDealAgents('DEAL 1/测试')
  await fetchDealR1AgentReports('DEAL 1/测试')
  await fetchDealR2AgentReports('DEAL 1/测试')
  await fetchDealR3ReviewSummary('DEAL 1/测试')

  assert.deepEqual(calls.map((call) => ({ url: call.url, method: call.method })), [
    { url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/status', method: 'GET' },
    { url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/agents', method: 'GET' },
    { url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/reports/r1-agents', method: 'GET' },
    { url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/reports/r2-agents', method: 'GET' },
    { url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/reports/r3-review', method: 'GET' },
  ])
})

test('deal workspace read APIs keep route contracts and encode report path segments', async () => {
  const calls = installFetchRecorder()

  await fetchDealWorkflow('DEAL 1/测试')
  await fetchDealPreflight('DEAL 1/测试')
  await fetchDealDisputes('DEAL 1/测试')
  await fetchDealPhaseArtifacts('DEAL 1/测试')
  await fetchDealDecision('DEAL 1/测试')
  await fetchDealAudit('DEAL 1/测试')
  await fetchDealManifest('DEAL 1/测试')
  await fetchDealReports('DEAL 1/测试')
  await fetchDealReport('DEAL 1/测试', 'R1/财务 报告.md')

  assert.deepEqual(calls.map((call) => ({ url: call.url, method: call.method })), [
    { url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/workflow', method: 'GET' },
    { url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/preflight', method: 'GET' },
    { url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/disputes', method: 'GET' },
    { url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/phase-artifacts', method: 'GET' },
    { url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/decision', method: 'GET' },
    { url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/audit', method: 'GET' },
    { url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/manifest', method: 'GET' },
    { url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/reports', method: 'GET' },
    {
      url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/reports/R1/%E8%B4%A2%E5%8A%A1%20%E6%8A%A5%E5%91%8A.md',
      method: 'GET',
    },
  ])
})

test('deal agent APIs preserve R1 defaults and dry-run contract', async () => {
  const calls = installFetchRecorder()

  await generateDealStartupRetrieval('DEAL-1', 'siq/ic strategist', { query: 'revenue', limit: 3 })
  await dryRunDealWorkflowR1Agent('DEAL-1', 'siq/ic strategist')
  await runDealWorkflowR1Serial('DEAL-1')
  await runDealWorkflowR1Serial('DEAL 1/测试', { dry_run: false, max_agents: 2 })
  await identifyDealWorkflowDisputes('DEAL-1')
  await identifyDealWorkflowDisputes('DEAL 1/测试', { dry_run: false, preserve_rulings: false })
  await ruleDealWorkflowDispute('DEAL-1', 'DISP-001', { decision: 'resolved_with_conditions' })
  await generateDealWorkflowDisputeRulings('DEAL-1')
  await generateDealWorkflowDisputeRulings('DEAL 1/测试', { dry_run: false, overwrite: true })
  await ruleDealWorkflowDispute('DEAL 1/测试', 'DISP 1/测试', {
    decision: 'resolved_no_followup',
    rationale: 'Chair accepted the gap.',
    required_followups: ['Archive note'],
    evidence_ids: ['EVID-001'],
    resolved: true,
    overwrite: true,
    dry_run: false,
  })

  assert.deepEqual(calls, [
    {
      url: '/api/deals/DEAL-1/agents/siq%2Fic%20strategist/startup-retrieval',
      method: 'POST',
      body: {
        round_name: 'R1',
        query: 'revenue',
        limit: 3,
      },
    },
    {
      url: '/api/deals/DEAL-1/workflow/run-r1-agent',
      method: 'POST',
      body: {
        profile_id: 'siq/ic strategist',
        round_name: 'R1',
        dry_run: true,
      },
    },
    {
      url: '/api/deals/DEAL-1/workflow/run-r1-serial',
      method: 'POST',
      body: {
        round_name: 'R1',
        dry_run: true,
        max_agents: 6,
      },
    },
    {
      url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/workflow/run-r1-serial',
      method: 'POST',
      body: {
        round_name: 'R1',
        dry_run: false,
        max_agents: 2,
      },
    },
    {
      url: '/api/deals/DEAL-1/workflow/identify-disputes',
      method: 'POST',
      body: {
        dry_run: true,
        preserve_rulings: true,
      },
    },
    {
      url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/workflow/identify-disputes',
      method: 'POST',
      body: {
        dry_run: false,
        preserve_rulings: false,
      },
    },
    {
      url: '/api/deals/DEAL-1/workflow/disputes/DISP-001/ruling',
      method: 'POST',
      body: {
        decision: 'resolved_with_conditions',
        rationale: '',
        required_followups: [],
        evidence_ids: [],
        resolved: true,
        overwrite: false,
        dry_run: true,
      },
    },
    {
      url: '/api/deals/DEAL-1/workflow/generate-dispute-rulings',
      method: 'POST',
      body: {
        dry_run: true,
        overwrite: false,
      },
    },
    {
      url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/workflow/generate-dispute-rulings',
      method: 'POST',
      body: {
        dry_run: false,
        overwrite: true,
      },
    },
    {
      url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/workflow/disputes/DISP%201%2F%E6%B5%8B%E8%AF%95/ruling',
      method: 'POST',
      body: {
        decision: 'resolved_no_followup',
        rationale: 'Chair accepted the gap.',
        required_followups: ['Archive note'],
        evidence_ids: ['EVID-001'],
        resolved: true,
        overwrite: true,
        dry_run: false,
      },
    },
  ])
})

test('deal decision confirmation API defaults to dry-run', async () => {
  const calls = installFetchRecorder()

  await postDealDecisionHumanConfirmation('DEAL 1/测试', {
    status: 'confirmed',
  })

  assert.deepEqual(calls, [
    {
      url: '/api/deals/DEAL%201%2F%E6%B5%8B%E8%AF%95/decision/human-confirmation',
      method: 'POST',
      body: {
        dry_run: true,
        status: 'confirmed',
      },
    },
  ])
})
